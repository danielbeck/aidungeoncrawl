# Implementation

## Architecture principles

The implementation separates deterministic game state and turn orchestration from LLM-driven interpretation, entity identification, and narration.

The application owns the authoritative game state, all internal IDs, entity matching decisions, and persistent changes. LLMs work with natural-language names and descriptions; they do not receive or generate IDs, issue state mutations, or manage internal status flags. Their structured manager reports and notable-element lists are inputs to deterministic reconciliation, not commands.

LLM outputs are validated before they are used. A turn is committed only after all required LLM calls and deterministic processing succeed, so a failed turn cannot leave partially applied changes.

## Deterministic state

The game engine maintains the authoritative logical state:

* **Game state:** Current location, player status, and whether the game is active or ended.
* **World state:** Locations, exits, items, objects, and NPCs, with stable IDs and the state needed by the game.
* **History:** A simple ordered record of player actions and narrator responses. Do not add automatic summaries or other history structure.

Existing object records remain available to the game UI, but the identity-only
turn contract does not create or change objects.

### Entity identity and ownership

Every persistent entity has a stable ID assigned and managed by deterministic code. LLM context uses names and descriptions instead. Managers can identify a known entity by its natural-language name or description, but never return an ID.

The Comparator is the only LLM component that decides whether a proposed notable element is already represented in the relevant entity list. It receives that list and one proposed element, and returns whether a match is already present. If present, deterministic code keeps the existing entity and does not add a duplicate. If absent, deterministic code creates a new entity and assigns its ID. New items are added to the player's inventory, new NPCs to the current location, and new locations as unvisited locations with no exits.

This refactor reconciles entity identity only. A notable location does not move the player or create exits; a notable NPC does not change an existing NPC's location or status; and a match does not transfer, consume, or otherwise change an existing item. Those gameplay effects are not applied by this turn pipeline. The Narrator must not describe an unsupported persistent effect as completed; actions that require one are unsuccessful until a separate deterministic rule supports them.

## LLM components

Each component has a distinct responsibility and receives only the natural-language context it needs. Manager responses are schema-constrained, domain-specific reports for the Narrator; they do not directly mutate state.

### Inventory manager

Returns only an `items` list. Each entry has a natural-language name and objective description, plus an `interaction` classification such as `use`, `search`, or `acquire`. It receives carried items and items or known objects in the current location, and does not assess NPC behavior or movement.

### Map manager

Returns only a `locations` list, with each entry's name, objective description, and movement interaction classification. Its context includes the current location, exits, and known locations. It does not assess items or NPCs.

### NPC manager

Returns only an `npcs` list, with each entry's name, objective description, and interaction classification. Its context includes NPCs at the current location, not NPCs from elsewhere in the world. It does not assess motivations, items, or locations.

### Narrator

Receives the gameplay context and the three structured manager results. It produces the player-facing narration and the final notable-element lists for this turn. The original manager results are retained separately in the turn response for deterministic processing. The Narrator should preserve their recommendations unless an established fact or an explicit story choice requires a clear deviation; it must not silently paraphrase or omit them.

The Narrator returns structured JSON with this contract:

```json
{
  "narration": "Player-facing response",
  "action_succeeded": true,
  "game_over": false,
  "notable_elements": {
    "items": [
      { "name": "iron key", "description": "A small iron key" }
    ],
    "locations": [
      { "name": "old cellar", "description": "A damp cellar beneath the tavern" }
    ],
    "npcs": [
      { "name": "Mira", "description": "The tavern keeper" }
    ]
  }
}
```

All three notable-element arrays are required and may be empty. Each entry has a non-empty natural-language `name` and `description`. Include entities relevant to the turn, whether already known or newly introduced. These lists identify entities; they are not state-change instructions and must not contain IDs, status flags, or operation payloads. The Narrator must not describe a persistent change in the narration while omitting the corresponding relevant notable element.

The Narrator should take a "Yes, and" approach: accommodate the player's intent where narratively plausible, while remaining grounded in the established context and canonical state.

### Comparator

The Comparator is a dedicated, lightweight LLM endpoint. It receives the relevant entity list and one notable element, and returns a boolean indicating whether the element—or a synonymous reference to it—is already represented. Deterministic code uses that result only for identity reconciliation:

* If the element is present, retain the existing entity; do not create a duplicate.
* If the element is absent, create a new entity in the appropriate collection and assign its stable ID.

The Comparator does not choose entity state, perform movement, or apply other game effects.

## Turn orchestration

The deterministic turn processor coordinates each gameplay turn:

1. Receive and validate the player's action.
2. Build a natural-language gameplay context from the authoritative state. It includes where the player is, what they are carrying, who else is at the location, known exits, and relevant history. Do not expose internal IDs or implementation-only status flags.
3. In parallel, send each manager the action and the context relevant to its responsibility.
4. Pass the gameplay context and all structured manager reports to the Narrator.
5. Validate the Narrator's response and its notable-element lists.
6. For each notable element, use the Comparator against the appropriate authoritative entity list. Keep an existing match or create a new entity with an ID when no match exists.
7. Add unmatched notable items, locations, and NPCs to their respective collections with deterministic IDs and the documented default placement.
8. Append the player action and narration to history, then commit the completed turn.

If any required LLM call fails or returns invalid output, return an error and leave the stored game state unchanged. Do not commit partially reconciled entities.

## Context construction

Construct a minimal, purpose-specific context for each LLM call. The shared gameplay context contains the player's intended action, current location, carried items, known available items, items and known objects at the current location, NPCs at the current location, known locations and exits, and recent exchanges. Include only the natural-language names and descriptions needed for interpretation.

Managers receive only their domain context and the two most recent history events, capped at 500 characters each. Inventory receives carried and local items/objects; Map receives locations and exits; NPC receives only NPCs at the current location. The Narrator receives the broader gameplay context and all three structured reports. The exact original manager reports are also returned in `turn.manager_results`, independently of the Narrator's output, so deterministic code can use them without relying on a model echo. The Comparator receives an entity type, the relevant entity list, and the single proposed element being checked; all entity data is limited to names and descriptions.

LLMs are not responsible for ID generation, database tracking, or maintaining map topology.

## Communication protocol and validation

LLM requests and responses use defined schemas. Validate required fields and value types before processing a response. The Narrator's output must include narration, boolean `action_succeeded` and `game_over` values, and the three notable-element arrays defined above.

Deterministic code resolves notable elements through the Comparator and owns all IDs and persistent writes. A positive match means no duplicate is added; a negative match means the element is added as a new entity. No other state effects are applied by this pipeline.

## Error handling and observability

The backend logs each turn's stages, including:

* Player action.
* LLM components invoked.
* Context supplied and raw/parsed model outputs.
* Comparator decisions and entity reconciliation results.
* Newly added entities and final narration.

Failures in LLM communication, response validation, or deterministic reconciliation leave the stored game state untouched.

## Testing

Tests focus on deterministic logic independently of live LLM calls:

* Entity creation, stable ID assignment, and duplicate prevention after Comparator matching.
* Manager context construction and ensuring IDs/internal status are not exposed.
* Notable-element response validation, including empty lists and malformed entries.
* Ensuring identity matching prevents duplicates and new entities receive stable IDs and documented defaults.
* Ensuring identity reconciliation does not move the player or change existing item or NPC state.
* Simple history appending and rejection of invalid or malformed outputs.
