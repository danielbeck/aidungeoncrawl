### Freeform Mode

Freeform treats the LLM as a storyteller rather than a world-state manager.
The narrator receives the player's action and a continuity summary, or the
opening narration on the first turn, and returns plain prose. It receives no
tracked world state, JSON output schema, or tools. Apart from preparing missing
history, each turn needs just one LLM call.

Only the action and narration are added to history. Tracked locations,
inventory, NPCs, and game-over status remain unchanged, so the sidebar can
become stale as the story develops. This makes Freeform the least constrained
mode, but switching to a stateful mode does not reconstruct those untracked
changes.

```mermaid
sequenceDiagram
    participant FF as Freeform orchestrator
    participant GS as History preparation
    participant H as LLMCall harness
    participant P as Provider transport
    participant L as LLM

    FF->>GS: Obtain current historical summary
    alt summary is current
        GS-->>FF: saved summary, no LLM call
    else fewer than two events
        GS-->>FF: empty summary
        FF->>FF: Use opening narration as background
    else summary is stale
        GS->>H: HistorySummarizerCall.run(completed history)
        H->>H: Build system prompt and user message
        H->>P: chat(messages, summary JSON schema)
        P->>L: Structured-output request
        L-->>P: summary JSON
        P-->>H: response, provider, model
        H->>H: Log, parse JSON, validate summary
        H-->>GS: LLMResult with validated summary
        GS-->>FF: summary
    end

    FF->>H: FreeformNarratorCall.run(action and background)
    H->>H: Build system prompt and user message
    H->>P: chat(messages, no schema)
    P->>L: Plain-text narration request
    L-->>P: story prose
    P-->>H: response, provider, model
    H->>H: Log, strip prose, validate non-empty narration
    H-->>FF: LLMResult with narration

    Note over FF,L: Provider or validation failures propagate without returning a successful turn.
    Note over FF,L: No manager calls, comparator calls, or tools in Freeform.
```

### Direct Mode

Direct extends Freeform's storytelling with explicit world-state updates.
One narrator LLM receives the action, continuity summary, player ID, and a
projection of the tracked state, then returns structured JSON containing
narration, the complete updated world, action success, and game-over status.

The harness validates the response, and conventional code checks world
references before applying it to a draft. There are no specialist managers,
semantic comparator calls, or incremental tool operations. This keeps
orchestration simple but asks one model to maintain entity identity and world
consistency in a single response. Managed refines this approach by separating
interpretation, reconciliation, and narration.

```mermaid
sequenceDiagram
    participant D as Direct orchestrator
    participant GS as History preparation
    participant H as LLMCall harness
    participant P as Provider transport
    participant L as LLM

    D->>GS: Obtain current historical summary
    opt Missing or stale summary with at least two events
        GS->>H: HistorySummarizerCall.run(completed history)
        H->>H: Build system prompt and user message
        H->>P: chat(messages, summary JSON schema)
        P->>L: Structured-output request
        L-->>P: summary JSON
        P-->>H: response, provider, model
        H->>H: Log, parse JSON, validate summary
        H-->>GS: LLMResult with validated summary
    end
    GS-->>D: Current summary or empty initial background

    D->>H: DirectNarratorCall.run(action, summary, player ID, projected world)
    H->>H: Build system prompt and user message
    H->>P: chat(messages, direct narrator JSON schema)
    P->>L: Decide outcome and return the complete updated world
    L-->>P: narration, updated world, action success, game over
    P-->>H: response, provider, model
    H->>H: Log, parse JSON, validate response
    H-->>D: LLMResult with narration and world
    D->>D: Validate world references and apply to draft
    Note over D,L: No managers, comparator calls, or native tools.
    Note over D,L: Provider or validation failures prevent a successful turn.
```

### Managed Mode

Managed refines Direct's single-call world updates by splitting interpretation
across specialist LLM calls. Inventory, movement
intent, and NPC managers run in parallel, then conventional code reconciles
their proposals into a draft world. When identity is uncertain, the comparator
receives an eligible candidate list and resolves it to an existing entity,
a new identity, or an ambiguity. A separate movement resolver handles
destinations that cannot be determined through deterministic shortcuts.

The narrator runs last and describes the reconciled outcomes rather than
choosing world changes itself as it does in Direct. Unlike Tool Using, the LLM does not choose which
functions to invoke: the engine owns the workflow and interprets structured
JSON responses. This provides explicit state constraints and specialist
reasoning, at the cost of more LLM calls per turn.

```mermaid
sequenceDiagram
    participant M as Managed orchestrator
    participant GS as History preparation
    participant H as LLMCall harness
    participant P as Provider transport
    participant L as LLM

    M->>GS: Obtain current historical summary
    opt Missing or stale summary with at least two events
        GS->>H: HistorySummarizerCall.run(completed history)
        H->>P: chat(messages, summary JSON schema)
        P->>L: Structured-output request
        L-->>P: summary JSON
        P-->>H: response, provider, model
        H-->>GS: Parsed and validated summary
    end
    GS-->>M: Current summary or empty initial background

    par Inventory interpretation
        M->>H: InventoryManagerCall.run(action, summary, local state)
        H->>P: chat(messages, inventory JSON schema)
        P->>L: Identify item interactions
        L-->>P: item proposals
        P-->>H: response
        H-->>M: Validated inventory result
    and Movement intent
        M->>H: MapManagerCall.run(action only)
        H->>P: chat(messages, movement intent JSON schema)
        P->>L: Interpret movement intent
        L-->>P: movement intent
        P-->>H: response
        H-->>M: Validated movement intent
    and NPC interpretation
        M->>H: NPCManagerCall.run(action, summary, local NPCs)
        H->>P: chat(messages, NPC JSON schema)
        P->>L: Identify NPC interactions
        L-->>P: NPC proposals
        P-->>H: response
        H-->>M: Validated NPC result
    end

    M->>M: Reconcile inventory proposals
    opt Identity requires semantic resolution
        M->>H: ComparatorCall.run(proposal, eligible candidates, context)
        H->>P: chat(messages, comparator JSON schema)
        P->>L: Resolve entity identity against candidate list
        L-->>P: existing, new, or ambiguous
        P-->>H: response
        H-->>M: Validated identity outcome
    end

    opt Movement requires contextual resolution
        M->>H: MovementResolverCall.run(intent and location context)
        H->>P: chat(messages, destination JSON schema)
        P->>L: Resolve destination or clarification
        L-->>P: destination result
        P-->>H: response
        H-->>M: Validated destination result
    end
    M->>M: Reconcile destination and NPC proposals
    Note over M,H: Reconciliation may make further candidate-list comparator calls.
    Note over M,H: Exact identity matches and deterministic movement shortcuts avoid LLM calls.

    M->>H: NarratorCall.run(action, summary, reconciled outcomes)
    H->>P: chat(messages, narrator JSON schema)
    P->>L: Narrate authoritative outcomes
    L-->>P: narration, action success, game over
    P-->>H: response, provider, model
    H-->>M: Validated narration result
    Note over M,H: Each harness call builds messages, logs, parses JSON, and validates the result.
    Note over M,L: No native tools are offered to the LLM in Managed mode.
```

### Tool Using Mode

Tool Using lets a game-master LLM choose the sequence of world changes. It
receives the action, continuity summary, and current scene alongside native
tool schemas for moving the player and creating, moving, acquiring, dropping,
or destroying entities. Each request offers all currently applicable tools
together, with eligible IDs constrained by the draft state.

Conventional tool functions validate and apply changes, invoking the semantic
comparator when creation needs identity resolution. Tool results and the
updated scene go back into the conversation, allowing further LLM rounds
before final plain-text narration. Unlike Managed's fixed pipeline or Direct's
whole-world replacement, the model controls incremental operations. Rejected
tools return errors without changing the draft, while provider failures or
conversation-limit failures abort the turn.

```mermaid
sequenceDiagram
    participant T as Tool turn orchestrator
    participant GS as History preparation
    participant H as LLMCall harness
    participant GM as GameMasterCall
    participant W as WorldTools executor
    participant P as Provider transport
    participant L as LLM

    T->>GS: Obtain current historical summary
    opt Missing or stale summary with at least two events
        GS->>H: HistorySummarizerCall.run(completed history)
        H->>P: chat(messages, summary JSON schema)
        P->>L: Structured-output request
        L-->>P: summary JSON
        P-->>H: response
        H-->>GS: Parsed and validated summary
    end
    GS-->>T: Current summary or empty initial background
    T->>GM: Build system prompt from harness definitions
    T->>T: Start conversation with action, summary, and current scene

    loop Until final prose or conversation limit
        T->>W: Select state-applicable tool schemas
        W-->>T: Tools with eligible entity IDs
        T->>GM: respond(conversation, offered tools)
        GM->>P: chat(messages, native tools)
        P->>L: Game master request with all applicable tools
        L-->>P: Assistant tool calls or final prose
        P-->>GM: Normalized assistant message, provider, model
        GM-->>T: Logged response
        alt Assistant requests tools
            loop Each requested tool in order
                T->>W: execute(tool call)
                W->>W: Validate arguments and state constraints
                opt Creation requires semantic identity resolution
                    W->>H: ComparatorCall.run(proposal, candidates, context)
                    H->>P: chat(messages, comparator JSON schema)
                    P->>L: Resolve existing entity or new identity
                    L-->>P: existing, new, or ambiguous
                    P-->>H: response
                    H-->>W: Parsed and validated identity outcome
                end
                W->>W: Apply accepted change or roll back rejected tool
                W-->>T: Tool result or error with authoritative scene
                T->>T: Append tool result to conversation
            end
            T->>T: Append execution checkpoint for next LLM round
        else Assistant returns final prose
            T->>T: Validate non-empty story text and finish loop
        end
    end

    Note over GM,H: GameMasterCall reuses prompt loading but uses respond instead of LLMCall.run.
    Note over T,L: Tool calls and nested comparator calls are logged in execution order.
    Note over T,L: Provider failures or exhausted limits abort the draft turn.
```

After a successful turn in any mode, separate history preparation can run
`HistorySummarizerCall` through the same harness and provider transport. It does
not delay returning the story. The next turn reuses the prepared summary when
current, or regenerates it before proceeding.