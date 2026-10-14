# Implementation

## Architecture principles

The implementation separates deterministic game state and turn orchestration from LLM-driven interpretation, entity identification, and narration.

The application owns the authoritative game state, all internal IDs, entity matching decisions, and persistent changes. LLMs work with natural-language names and descriptions; they do not receive or generate IDs, issue state mutations, or manage internal status flags. Their assessments and notable-element lists are inputs to deterministic reconciliation, not commands.

LLM outputs are validated before they are used. A turn is committed only after all required LLM calls and deterministic processing succeed, so a failed turn cannot leave partially applied changes.

## Deterministic state

The game engine maintains the authoritative logical state:

* **Game state:** Current location, player status, and whether the game is active or ended.
* **World state:** Locations, exits, items, objects, and NPCs, with stable IDs and the state needed by the game.
* **History:** A simple ordered record of player actions and narrator responses. Do not add automatic summaries or other history structure.

### Entity identity and ownership

Every persistent entity has a stable ID assigned and managed by deterministic code. LLM context uses names and descriptions instead. Managers can identify a known entity by its natural-language name or description, but never return an ID.

The Comparator is the only LLM component that decides whether a proposed notable element is already represented in the relevant entity list. It receives that list and one proposed element, and returns whether a match is already present. If present, deterministic code keeps the existing entity and does not add a duplicate. If absent, deterministic code creates a new entity and assigns its ID.

Identity matching is separate from applying turn effects. For example, recognizing an existing location does not itself move the player there; deterministic movement rules still apply. Likewise, recognizing an existing item does not by itself transfer, consume, or otherwise change it.

## LLM components

Each component has a distinct responsibility and receives only the natural-language context it needs. Manager responses are concise assessments for the Narrator; they do not directly mutate state.

### Inventory manager

Assesses items relevant to the player's action, including whether they are carried, present in the current context, or newly implied. It identifies item candidates by natural-language name and description, without IDs. It focuses on items and does not manage locations or NPCs.

### Map manager

Assesses movement, locations, and exits. Its context includes the current location and known exits, expressed without IDs or internal status flags. It identifies relevant existing or newly implied locations and explains whether the action follows an established exit or discovers a connection. It does not manage items or NPCs.

### NPC manager

Assesses NPCs relevant to the action and the current location. It identifies existing or newly implied NPCs by natural-language name and description, and distinguishes relevant narrative circumstances such as an NPC being present, moving, or leaving. It does not manage items or locations.

### Narrator

Receives the gameplay context and all manager assessments. It produces the player-facing narration and the final notable-element lists for this turn. It may pass through, omit, or modify manager recommendations to fit the coherent outcome of the action.

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
4. Pass the gameplay context and all manager assessments to the Narrator.
5. Validate the Narrator's response and its notable-element lists.
6. For each notable element, use the Comparator against the appropriate authoritative entity list. Keep an existing match or create a new entity with an ID when no match exists.
7. Apply any turn effects through deterministic game logic, using the action, context, manager assessments, narration, and reconciled entities. In particular, identity matching alone must not imply movement, item transfer, consumption, or NPC state changes.
8. Append the player action and narration to history, then commit the completed turn.

If any required LLM call fails or returns invalid output, return an error and leave the stored game state unchanged. Do not commit partially reconciled entities or turn effects.

## Context construction

Construct a minimal, purpose-specific context for each LLM call. The shared gameplay context contains the player's intended action, current location, carried items, NPCs at the current location, relevant known exits, and relevant past exchanges. Include only the natural-language names and descriptions needed for interpretation.

Managers receive the portions needed for their work: the Inventory Manager receives item context, the Map Manager receives location and exit context, and the NPC Manager receives NPC context. The Narrator receives the gameplay context and the managers' assessments. The Comparator receives only the relevant entity list and the single proposed element being checked.

LLMs are not responsible for ID generation, database tracking, or maintaining map topology.

## Communication protocol and validation

LLM requests and responses use defined schemas. Validate required fields and value types before processing a response. The Narrator's output must include narration, boolean `action_succeeded` and `game_over` values, and the three notable-element arrays defined above.

Deterministic code resolves notable elements through the Comparator and owns all IDs and persistent writes. A positive match means no duplicate is added; a negative match means the element is added as a new entity. Other state effects are handled separately by deterministic game logic, not by accepting model-supplied mutations.

## Error handling and observability

The backend logs each turn's stages, including:

* Player action.
* LLM components invoked.
* Context supplied and raw/parsed model outputs.
* Comparator decisions and entity reconciliation results.
* Final state changes and narration.

Failures in LLM communication, response validation, or deterministic reconciliation leave the stored game state untouched.

## Testing

Tests focus on deterministic logic independently of live LLM calls:

* Entity creation, stable ID assignment, and duplicate prevention after Comparator matching.
* Manager context construction and ensuring IDs/internal status are not exposed.
* Notable-element response validation, including empty lists and malformed entries.
* Map navigation, exit handling, and location transitions.
* Item and NPC effects applied by deterministic logic rather than identity matching.
* Simple history appending and rejection of invalid or malformed outputs.
