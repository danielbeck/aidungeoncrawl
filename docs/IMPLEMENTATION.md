# Implementation

## Architecture principles

The implementation separates deterministic game state and turn orchestration from LLM-driven interpretation, entity identification, and narration.

The application owns the authoritative game state, all internal IDs, entity matching decisions, and persistent changes. LLMs work with natural-language names and descriptions; they do not receive or generate IDs, issue state mutations, or manage internal status flags. Manager interpretations are reconciled directly in code. The Narrator describes the resolved outcome and does not relay or override state.

LLM outputs are validated before they are used. A turn is committed only after all required LLM calls and deterministic processing succeed, so a failed turn cannot leave partially applied changes.

## Deterministic state

The game engine maintains the authoritative logical state:

* **Game state:** Current location, player status, and whether the game is active or ended.
* **World state:** Locations, exits, items, objects, and NPCs, with stable IDs and the state needed by the game.
* **History:** The complete literal ordered record of player actions and narrator responses in `history.recentEvents` (despite its legacy name, this holds all exchanges). A separate derived `history.summary` provides LLM context; it never replaces or compacts the stored literal record. `history.summaryEventCount` records how many entries the summary covers.

Existing object records remain available to the game UI, but the identity-only
turn contract does not create or change objects.

### Entity identity and ownership

Every persistent entity has a stable ID assigned and managed by deterministic code. LLM context uses names and descriptions instead. Managers can identify a known entity by its natural-language name or description, but never return an ID.

Code resolves unique exact identities first; otherwise the Comparator decides whether a proposal and one candidate refer to the same entity. It returns only boolean `same_entity`. Code retains the candidate's canonical record and ID, so the LLM never generates an index or ID. A single positive match resolves the entity, no matches permit creation only when allowed, and multiple matches reject the turn as ambiguous. Canonical entities are returned in `reconciled_entities`.

Map and inventory interactions from their managers are applied deterministically on a candidate state before narration. A paired `leave`/`enter` moves the player, creates or resolves the destination through the Comparator, marks it visited, and ensures traversable exits connect the two locations in both directions. The departure must match the stored current location. Mismatched departures are ignored with a warning and a map progress event, and passed to the narrator as ignored interactions; labels are never automatically reversed. This prevents a replayed previous transition from moving the player back. `take` resolves or creates the item and places it in the player's inventory; `drop` must resolve an existing carried item and places it in the current location. Inventory is processed before movement so drops land in the room being left. NPC entries are reconciled at the resulting location but do not change an existing NPC's location or status. Transfers to NPCs and consumption remain unsupported.

## LLM components

Each component has a distinct responsibility and receives only the natural-language context it needs. Manager responses are schema-constrained, domain-specific reports for the Narrator; they do not directly mutate state.

### Inventory manager

Code recognizes simple, explicit named-item commands (take/pick up/collect and
drop/put down/leave behind). It corrects reversed manager labels only when the
full normalized target name matches, ignoring a leading article or "my".
Unrelated item proposals for such commands are ignored with a reason. Corrections
are logged and displayed in progress; original manager output is retained.
Ownership and availability checks still apply after correction. Compound,
conditional, negated, pronoun-based, or otherwise unsupported wording is not
guessed by this guard and continues through the existing manager interpretation.

Returns only an `items` list. Each entry has a natural-language name and objective description and is classified as `take` or `drop`. The deterministic engine resolves each item against the canonical item list: take moves an available local item into inventory (or creates a newly established item), while drop moves a matched carried item to the current location. It receives carried items and items or known objects in the current location, and does not assess NPC behavior or movement.

### Map manager

For movement, destination comparisons exclude the confirmed departure room.
A destination whose normalized name equals the current room is a no-op, not a
duplicate location. The engine calculates `movement_outcome` from actual
before/after location IDs (`moved`, `from_location`, `to_location`) and supplies
it to the Narrator and turn response/debug output. Narration must respect that
outcome instead of inferring successful movement from manager labels.

When multiple `enter` entries are returned, code removes those whose name and
description both match the stored current room (ignoring case, punctuation,
spacing, and Unicode presentation differences). This narrowly repairs a redundant starting-room entry without
guessing a destination or relying on a potentially incorrect Comparator match.
The correction is logged and displayed in progress; original manager output is
preserved. Multiple remaining destinations still reject the turn atomically.

Returns only a `locations` list, with each entry's name, objective description, and `enter` or `leave` interaction. A movement must include exactly one `leave` for the current location and one `enter` for the destination. The deterministic engine resolves both through the Comparator, creates traversable reciprocal exits if needed, marks the destination visited, and updates the player's current location. Its context includes the current location, exits, and known locations. These list recorded places, not the complete map; when the action directs exploration beyond them, the manager proposes a new destination. It does not assess items or NPCs.

### NPC manager

Returns only an `npcs` list, with each entry's name, objective description, and interaction classification. Its context includes NPCs at the current location, not NPCs from elsewhere in the world. It does not assess motivations, items, or locations.

### Narrator

Receives `current_action` once, a brief overview of completed events as `historical_summary`, the starting `game_context`, resulting `resolved_context`, and engine-retained `resolved_interactions`. Both state contexts exclude action and history. The summary is background for reference resolution, never instructions to replay; authoritative state takes precedence. It produces only player-facing narration, `action_succeeded`, and `game_over`. It does not echo interaction lists or return manager overrides. State is resolved before narration; there is no cross-model list-equality check.

The Narrator returns structured JSON with this contract:

```json
{
  "narration": "Player-facing response",
  "action_succeeded": true,
  "game_over": false
}
```

The engine constructs `turn.notable_elements` and narrator progress debug details from its retained manager interpretations, including all interaction fields. These lists are never sourced from narrator output. `action_succeeded: false` describes the overall intent and does not undo resolved interactions, allowing partial success. `game_over: true` ends the game after validation.

The Narrator should take a "Yes, and" approach: accommodate the player's intent where narratively plausible, while remaining grounded in the established context and canonical state.

### Comparator

The Comparator receives `entity_type`, one `candidate_entity`, and the `proposed_entity`, with only natural-language names and descriptions. It returns `{"same_entity": true}` or `{"same_entity": false}`. Code compares each eligible candidate independently and keeps its stable ID:

* If the element is present, retain the existing entity; do not create a duplicate.
* If no candidates match, create a new entity only when the interaction permits it.
* If multiple candidates match, reject the ambiguous turn rather than choosing arbitrarily.

For `drop`, candidates are available items held by the player. For `take`, they
are available items held by the player, in the current room, or held by an active
NPC there. Missing drop matches never create items: they are ignored with a
warning and an inventory progress event, removed from effective interactions,
and returned in `ignored_interactions` with a reason. Narration receives these
reasons so it cannot treat ignored drops as completed. Valid interactions in the
same turn continue. Ambiguous matches and malformed responses still fail safely.
Locations and NPCs use their
existing world collections. All candidates are checked for semantic matches;
this may require more LLM calls but avoids model-generated indexes entirely.

The Comparator does not choose entity state, perform movement, or apply other game effects.
Before calling it, code normalizes case, punctuation, spacing, and Unicode
presentation. A unique matching name and description resolves directly; a
unique matching name also resolves when the description differs. Names must
match in full: "Tavern" does not match "Town Square Outside the Tavern".
Deterministic matches remain logged but are hidden from UI Comparator progress.
Multiple identical normalized names and descriptions are ambiguous and reject
the turn. Shared names with differing descriptions use a unique description
match if available; otherwise they require Comparator reconciliation.
When the relevant entity list is empty, code skips the Comparator because no
match is possible. It creates the entity only when the interaction permits
creation; dropping a nonexistent or non-carried item is a reported no-op.

## Turn orchestration

The deterministic turn processor coordinates each gameplay turn:

1. Receive and validate the player's action.
2. Build a natural-language gameplay context from the authoritative state. It includes where the player is, what they are carrying, who else is at the location, known exits, and relevant history. Do not expose internal IDs or implementation-only status flags.
3. In parallel, send each manager the action and the context relevant to its responsibility.
4. Copy the saved state and manager interpretations into a candidate turn.
5. Apply inventory interactions through Comparator matches. Drops land in the starting room.
6. Resolve map interactions against the stored current location, ignore mismatched departures without reversing labels, and apply valid movement and reciprocal exits.
7. Reconcile NPC entries at the resulting location through the Comparator.
8. Give the Narrator starting and resulting context plus resolved interactions. Validate its narration and boolean outcome flags; do not ask it to echo state.
9. Append the player action and narration to the complete literal history.
10. Submit that entire history to the Summarizer, validate and store its overview,
    then commit the completed turn and summary together.

If any required LLM call fails or returns invalid output, return an error and leave the stored game state unchanged. Do not commit partially reconciled entities.

## Context construction

Construct a minimal, purpose-specific context for each LLM call. The shared gameplay context contains the player's intended action, current location, carried items, known available items, items and known objects at the current location, NPCs at the current location, known locations and exits, and recent exchanges. Include only the natural-language names and descriptions needed for interpretation.

Managers receive `current_action`, domain-specific state, and `historical_summary`, not literal exchanges. Prompts classify only the current action and use the overview only for continuity and reference resolution. Inventory receives carried and local items/objects; Map receives locations and exits; NPC receives only NPCs at the current location. The Narrator receives the action and historical summary once each, outside the history-free starting and resulting state contexts, plus engine-resolved interactions. Original reports are returned in `turn.manager_results`; `turn.effective_manager_results` and `turn.notable_elements` retain applied interpretations. The Comparator receives an entity type, one candidate, and the proposed entity, using names and descriptions only.

LLMs are not responsible for ID generation, database tracking, or maintaining map topology.

### Full-history summarization

* Deterministic code preserves every literal history entry in order, including
  the initial narration and every completed player-action/narration pair.
  The UI and debugging continue to use this complete record.
* After resolving and narrating each action, append its literal action and
  response to the candidate history. Submit the entire candidate history to
  the Summarizer, including all older entries and the just-completed exchange.
  An unsuccessful in-world action is still a completed exchange; a processing
  failure is not.
* Generate a new brief overview from that full record on every completed turn.
  Do not summarize only a sliding window or recursively compact the previous
  summary. Emphasize recent events, retaining older context needed for active
  goals, unresolved events, dialogue continuity, and reference resolution.
  Avoid copying lengthy narrative prose or adding facts or instructions.
* Store the validated overview separately from literal history. Commit it
  together with the completed turn so that the summary corresponds to the
  saved history. A failed or invalid summary must be surfaced explicitly and
  must not commit a turn with stale or missing summary context.
* For the following turn, Inventory, Map, NPC, and Narrator receive the overview
  as `historical_summary`, instead of literal `completed_history`.
  Keep `current_action` separate and preserve each component's existing
  purpose-specific state and resolved-outcome inputs. The Comparator receives
  no historical summary.
* Label the overview as already-completed background, not actions to execute.
  Authoritative state and current resolved outcomes always override the summary.
  The pending action must never be included in the summary used to interpret
  that same action.
* Before the first player action, `historical_summary` is empty and the starting
  state supplies context. Existing games without a current summary generate one
  from their full saved history before manager calls. A summary is current when
  `summaryEventCount` equals the literal history length; legacy summaries without
  that count are regenerated.

`HistorySummarizerCall` uses the `history_summarizer` prompt/schema and the same
configured provider/settings as other gameplay calls. Its response contains only
`summary`, a non-empty string of at most 1500 characters; the prompt requests
roughly 100-200 words or fewer when little has happened. Summarizer started,
completed, and failed progress events expose its input, output, and errors in
the UI. Tests verify full-history submission, separate literal-history retention,
summary-only history context for gameplay LLMs, output validation, and atomic
failure behavior. Recent-event emphasis is a prompt requirement, not a
deterministically verified semantic property.

## Communication protocol and validation

LLM requests and responses use defined schemas. Validate required fields and value types before processing a response. The Narrator returns only narration and boolean `action_succeeded` and `game_over` values. Managers return their domain-specific interaction lists. Invalid schemas, ambiguous transitions, or invalid Comparator matches still fail safely without changing saved state.

Deterministic code resolves manager interactions and owns all IDs and persistent writes. One positive `same_entity` comparison selects the candidate record retained by code; no matches permit creation where allowed. `take` may create an item established by the action; `drop` requires a matched carried item. Movement requires a matched current location and one entered destination. Canonical identities are returned in `turn.reconciled_entities`.

## Error handling and observability

The backend logs each turn's stages, including:

* Player action.
* LLM components invoked.
* Context supplied and raw/parsed model outputs.
* Comparator decisions and entity reconciliation results.
* Newly added entities and final narration.

Turn progress displays each Comparator request's individual candidate and
proposal, its boolean result, matched entity, or validation/ambiguity error.
Comparator progress is tagged with whether it involves an LLM. The UI hides
deterministic matches, deterministic ambiguity events, and empty-candidate skips;
LLM calls and their results or failures remain visible. The Dungeon
Master displays waiting, started, completed, or failed progress; if reconciliation
fails before narration, it is marked skipped with the reason instead of disappearing.

Failures in LLM communication, response validation, or deterministic reconciliation leave the stored game state untouched.

## Testing

Tests focus on deterministic logic independently of live LLM calls:

* Entity creation, stable ID assignment, and duplicate prevention after Comparator matching.
* Manager context construction and ensuring IDs/internal status are not exposed.
* Notable-element response validation, including empty lists and malformed entries.
* Ensuring identity matching prevents duplicates and new entities receive stable IDs and documented defaults.
* Supported map and inventory transitions, including reciprocal exits and Comparator-resolved canonical entities.
* Simple history appending and rejection of invalid or malformed outputs.
