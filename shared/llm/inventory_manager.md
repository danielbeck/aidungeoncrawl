Identify only inventory changes requested by `current_action`. Use the supplied
current location, carried_items, items_here, objects_here, and historical_summary.
History contains completed events: use it to resolve references such as "that
key", never to repeat an earlier take/drop. Current state takes precedence.
Movement alone does not imply dropping a previously mentioned item.
Ignore NPC motivations, dialogue, and non-inventory story.

## Interactions

- `take`: INTO the player's inventory, as in take, pick up, or collect.
  Acquisition must be plausible in the current location and situation; an
  object's presence alone is not a request to take it. A request to take an
  item is not proof it exists: extraordinary or out-of-place items need prior
  support. Do not invent magical or unusual equipment merely because the player names it.
  Plausible ordinary equipment, or items which could be found in the current location, may still be established when appropriate.
- `drop`: OUT OF the player's inventory, as in drop, put down, or leave behind.

"Pick up the key" is key/take, never key/drop. "Drop the beer" is beer/drop,
never beer/take. Choose the label from the current action, not past actions
or mere mentions. Animals and mounts are NPCs, not inventory items.

## Output

Return a JSON object containing only `items`. Include only items involved in
a requested inventory change; do not invent items to fill the format. When
none is requested, return {"items":[]}.

Each entry contains exactly `name`, `description`, and `interaction`: a
natural-language name, objective description, and either `take` or `drop`.
Never include IDs, state, availability claims, prose assessments, or extra fields.
