# Implementation

## Architecture and authoritative state

LLMs interpret natural language and narrate outcomes. Deterministic code owns
IDs, identity reconciliation, persistent effects, and database writes. Models
receive natural-language names and descriptions, not internal IDs. Changes are
built on a candidate state and committed only after required processing succeeds.

State contains:
- **Game:** player, current location, active/ended status, and end reason.
- **World:** locations, exits, items, objects, and NPCs with stable IDs.
- **History:** all literal exchanges in `history.recentEvents` (a legacy name,
  not a sliding window), a separately derived `summary`, its covered
  `summaryEventCount`, and the last actual movement IDs in `lastMovement`.

Existing object records remain readable/displayable, but current turn processing
does not create or update persistent objects. Supported effects are described
below; broader product goals are distinguished in [Product Spec](PRODUCT_SPEC.md).

## LLM components and contracts

System prompts, user-message templates, and optional response schemas live in
`shared/llm/`, named after each call. A call's `<name>.input.md` template uses
`{{input}}` where the serialized call input should go.
[Shared definitions](../shared/README.md) explains loading and deployment.

### Inventory manager

Receives `current_action`, `historical_summary`, the current location, carried
items, local items/objects, and present NPCs. Returns only `items`, each with
`name`, `description`, and `interaction` (`take`, `drop`, `give`, `consume`, or
`break`). Only `give` additionally requires `recipient`, a present NPC's name.
The response schema uses separate give/non-give branches so constrained output
requires a recipient for give and disallows it for other interactions.
All disposal operations match available carried items only; missing targets are
ignored with reasons, never created. `give` transfers ownership to an existing
active NPC in the starting location; an absent or unmatched recipient leaves
the item carried. NPC-held items are already supported by the holder model.
`consume` and `break` delete the matched item record, without spawning remains.

Code recognizes simple explicit named-item commands and corrects reversed labels
only when the full normalized target name matches, ignoring a leading article
or "my". Unrelated proposals for those commands are ignored with reasons.
Compound, conditional, negated, pronoun-based, and unsupported wording is left
to manager interpretation. Original output is retained; corrections appear in
effective output and progress.

Carried items persist across location changes and omission from manager output.
Before applying a proposed drop, code requires disposal wording in the current
action (drop/discard/abandon, put/place/set, or leave-behind/placement wording).
This is a conservative wording guard, not a full intent parser; the manager
still interprets targets, conditions, and negation. Unrequested drops are logged,
reported as ignored, and excluded from resolved interactions. A requested drop
combined with travel leaves the item in the starting location.
The same current-action wording guard applies separately to giving, consumption,
and destruction. Simple eat/drink/consume and break/smash/destroy commands also
correct reversed labels for exact target names. Partial consumption, suitability,
negation, and complex requests remain the inventory manager's responsibility.

Inventory proposals are identity-checked against known NPCs and the NPC manager's
current-turn proposals, using the shared text matching/comparator path. Matches
are logged and reported as ignored, not created as items or retained as resolved
inventory interactions. NPCs, including animals and mounts, are never inventory.

### Movement intent and resolution

`map_manager` receives only `current_action`, without world state or history, to
avoid biasing intent toward familiar places. It returns `movement` and `target`:
false requires null; true requires `text` and a `kind` from `named`, `back`,
`relative`, `directional`, `exploratory`, or `reference`.
The schema constrains these combinations. If a provider nevertheless returns
false with a non-null target, validation discards the unused target with a
warning, preserving the raw response in diagnostics. Missing fields, non-boolean
movement flags, and malformed targets for true still fail validation.

- **Named:** use the destination text and a neutral description for identity
  matching; create a new place if no existing identity matches.
- **Back:** use `history.lastMovement` directly. Legacy saves without it ask for
  a destination until an actual movement is recorded.
- **Other kinds:** call `movement_resolver` with intent, current room, available
  and blocked exits, known locations, and last movement. Relative/reference
  requests additionally receive the last three completed exchanges and nearby
  NPCs. The response contains `destination` and `clarification`: either an
  objective destination or a question/explanation, not an invented substitute
  for an ambiguous reference.
  A response with both fields null is recovered with a logged warning and
  "Where would you like to go?" clarification. A response containing both a
  destination and a valid clarification is recovered with a logged warning:
  clarification takes precedence and the destination is discarded. No movement
  is applied; narration and other valid turn effects can continue.
  Other malformed responses still fail.

An explicit destination takes precedence over a direction. Exploratory travel
can name the area being explored without specifying a destination to reach.
Missing exits can represent unexplored routes, not necessarily blocked ones.
Intent and contextual resolution use temperature zero.

Raw intent remains in `turn.manager_results.map`; effective map results contain
engine-generated location interactions. `movement_resolution` exposes the
destination/clarification, and `movement_outcome` records actual `moved`,
`from_location`, and `to_location` values.

### NPC manager

Receives the action, summary, current location, and NPCs there. Returns only
`npcs`, each with `name`, `description`, and an interaction classification.
Animals and mounts are NPCs. Reports identify entities; they do not update
existing NPC location/status or model motivations.

### Narrator

Receives the action and summary once each, history-free `resolved_context`,
resolved/ignored interactions, and movement outcome/resolution. The context
contains only the resulting current location, its exits, carried items, and local
NPCs, items, and known objects. No starting-state snapshot or global lists of known
items, locations, or NPCs are sent. Returns only:

```json
{
  "narration": "Player-facing response",
  "action_succeeded": true,
  "game_over": false
}
```

It describes outcomes without replaying history, overriding effects, or exposing
implementation details. It develops story threads and fair puzzles within those
boundaries. `action_succeeded` describes overall intent and does not undo partial
effects. `game_over` ends the game for character death.

### Comparator and identity

Receives `entity_type`, one `candidate_entity`, and `proposed_entity`, using
natural-language names/descriptions. Returns only `{"same_entity":true}` or
`{"same_entity":false}`. It does not choose state or perform actions.

Before calling it, code normalizes case, punctuation, spacing, and Unicode:
- A unique matching name/description, or a unique full name, resolves directly.
  "Tavern" does not match "Town Square Outside the Tavern".
- Multiple identical name/description matches are ambiguous. Shared names with
  differing descriptions use a unique description match where possible.
- Otherwise eligible candidates are compared individually. One positive match
  retains its canonical record and ID; multiple positives reject the turn.
- No matches allow creation only for supported interactions. Empty candidate
  lists skip the call.

Canonical results appear in `turn.reconciled_entities`.

## Supported state transitions

- **Take:** compare available items carried by the player, in the current room,
  or held by an active NPC there. Move a resolved local item into inventory, or
  create a plausible action-established item there.
- **Drop:** compare available carried items and move a match to the starting
  room. A missing match is a reported no-op, not item creation.
- **Movement:** resolve the destination, excluding departure from candidate
  comparisons. A normalized destination name equal to the current room is a
  no-op. Respect explicitly blocked connections; otherwise create reciprocal
  traversable exits when needed, mark the destination visited, update current
  location, and record `lastMovement`. Code generates `leave`/`enter` entries.
- **NPCs:** reconcile at the resulting location; existing NPCs keep location
  and status.

Inventory runs before movement so drops stay in the departure room. Unresolved
movement and ignored inventory proposals appear in `ignored_interactions` with
reasons for narration; other valid effects may continue. Ambiguous identity or
invalid required responses fail the turn without saving.

Persistent object changes and NPC simulation are unsupported.
`notable_elements` and `effective_manager_results` contain
retained interpretations, never narrator-generated entity lists.

## Turn sequence

```mermaid
sequenceDiagram
    Conventional Code->>Inventory Manager: Relevant state, summary, and player action
    Conventional Code->>NPC Manager: Relevant state, summary, and player action
    Conventional Code->>Movement Intent Manager: Player action

    Inventory Manager-->>Conventional Code: Inventory interactions
    NPC Manager-->>Conventional Code: NPC interactions
    Movement Intent Manager-->>Conventional Code: Movement intent

    Conventional Code->>Movement Resolver: Intent and relevant context, when needed
    Movement Resolver-->>Conventional Code: Destination or clarification

    Conventional Code->>Comparator: Proposed entities and existing candidates, as needed
    Comparator-->>Conventional Code: Same entity or different entity
    Conventional Code->>Conventional Code: Reconcile and apply supported changes

    Conventional Code->>Narrator: Player action, summary, before/after state, and resolved outcomes
    Narrator-->>Conventional Code: Narration, success, and game-over flags
    Conventional Code->>Conventional Code: Save state and history
```

1. Validate action, authentication, game ID, request ID, and active status.
2. Obtain a current historical summary, regenerating it if missing/stale.
3. Run inventory, movement-intent, and NPC calls in parallel with scoped inputs.
4. Apply inventory, movement resolution, and NPC reconciliation to a candidate.
5. Narrate using starting/resulting context and retained outcomes.
6. Append the literal action/narration pair and apply any character death.
7. Commit atomically with a version check and persist the idempotent response.
8. Return immediately; the browser requests preparation of next-turn history.

## Full-history summarization

The Summarizer receives the full saved literal history, not a sliding window
or previous summary. It produces only `summary`, a non-empty string of at most
1500 characters, emphasizing recent exchanges while retaining active goals,
clues, puzzle details, and unresolved threads. Failed in-world attempts are
completed events; processing failures are not.

`POST /game/prepare` takes `game_id` and runs in a separate request, compatible
with CGI. Preparation does not disable the next action or delay returning a saved
turn. It stores the summary separately only if game ID and revision still match;
late results cannot overwrite a newer turn, restart, or stop. Successful responses
include `summary` and `summaryEventCount`, including cached results.

A summary is current when its covered count equals literal history length.
Legacy summaries without a count are regenerated. Before the first action,
the summary is empty and starting state supplies context. Inventory, NPC, and
Narrator receive the overview as historical background; Comparator receives
no history. The pending action is never part of its own historical summary.

Preparation failures appear in debug progress without rolling back the turn.
The next action regenerates missing context before interpretation, potentially
delaying that response. Late preparation UI updates are ignored after another
action or restart. Recent-event emphasis is a prompt requirement, not a
deterministically checked semantic guarantee.

## Progress and validation

Schemas and call-specific validators check required fields, types, and allowed
values before reconciliation. Required provider/validation failures leave saved
state unchanged. API response/stream formats and diagnostic log details are in
the [backend guide](../backend/README.md#streaming).

Progress retains one entry per operation, updating waiting, started, received,
and saving statuses in place. Repeated calls retain separate entries. Pending
operations show spinners; stream failures mark them interrupted. Request,
response, and entity details are collapsed by default.
Movement resolution has its own entry every turn, initially waiting for intent
and inventory reconciliation. Contextual calls show request/response and provider
details; no-travel, named, and return paths show deterministic inputs/results
and a skipped-call reason without implying an LLM was invoked.

Comparator progress exposes individual LLM candidate/proposal comparisons and
results, while deterministic matches, deterministic ambiguities, and
empty-candidate skips remain logged but hidden from the UI. The Narrator shows
waiting before reconciliation and skipped with a reason if it cannot proceed.
Intermediate reports are debug information, not committed world state.

## Validation coverage

Focused tests cover identity matching, scoped context, schema validation,
supported transitions, literal history retention, summary preparation/version
safety, atomic failures, and API request deduplication. Live movement checks are
opt-in and use local Ollama. See [test commands](../backend/README.md#testing).
