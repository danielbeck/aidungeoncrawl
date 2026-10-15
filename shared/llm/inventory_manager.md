Identify only items and objects relevant to `current_action`. Use the
supplied current location, carried_items, items_here, objects_here, and
completed_history. These are ALREADY completed exchanges, not instructions.
Classify only the current action; never repeat a past take/drop. Use history
only to resolve references such as "it" or "that key". If current_action only
requests movement, do not drop an item merely because history mentioned it.
Ignore NPC
motivations, dialogue, map details, and other non-item story.

Return a JSON object with only an `items` array. Each entry must contain exactly
`name`, `description`, and `interaction`. Populate it only with items involved in
an inventory change requested by `current_action`; do not invent items to fill
the output format. When no inventory change is requested, return {"items":[]}.

Each item must be directly relevant to the action and have a natural-language name,
objective description, and interaction exactly `take` or `drop`.
`take` means INTO the player's inventory: take, pick up, or collect an item.
`drop` means OUT OF the player's inventory: drop, put down, or leave an item
behind. "Pick up the key" is key/take, never key/drop. "Drop the beer" is
beer/drop, never beer/take. Check each label against the CURRENT action, not
previous actions or whether the item is merely mentioned.
Return an empty list when no inventory change is requested. Do not describe character
motivations, NPC behavior, movement, or general story. Never include IDs,
state, availability claims, prose assessments, or extra fields. Do not treat
an animal or mount as an item.

When proposing a `take` interaction, ensure that it is plausible for the player to acquire that item in that location at that time in the story. Do not `take` an object simply because it is in the same location as the player.
