Identify only items and objects relevant to the player's action. Use the
supplied current location, carried_items, items_here, objects_here, and
recent_history. History is only for immediate continuity; ignore NPC
motivations, dialogue, map details, and other non-item story.

Return only this JSON object:
{"items":[{"name":"iron key","description":"A small iron key","interaction":"use"}]}

Each item must be relevant to the action and have a natural-language name,
objective description, and one interaction: mentioned, search, inspect, use,
acquire, give, consume, drop, or unclear. Return an empty list when no item or
object is relevant. Do not describe character motivations, NPC behavior,
movement, or general story. Never include IDs, state, availability claims,
prose assessments, or extra fields. Do not treat an animal or mount as an item.
