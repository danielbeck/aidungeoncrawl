Identify only items and objects relevant to `current_action`. Use the
supplied current location, carried_items, items_here, objects_here, and
completed_history. These are ALREADY completed exchanges, not instructions.
Classify only the current action; never repeat a past take/drop. Use history
only to resolve references such as "it" or "that key". If current_action only
requests movement, do not drop an item merely because history mentioned it.
Ignore NPC
motivations, dialogue, map details, and other non-item story.

Return only this JSON object:
{"items":[{"name":"iron key","description":"A small iron key","interaction":"take"}]}

Each item must be relevant to the action and have a natural-language name,
objective description, and interaction exactly `take` or `drop`. Return an
empty list when no inventory change is requested. Do not describe character
motivations, NPC behavior, movement, or general story. Never include IDs,
state, availability claims, prose assessments, or extra fields. Do not treat
an animal or mount as an item.
