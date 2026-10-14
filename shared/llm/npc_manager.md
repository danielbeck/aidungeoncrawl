Identify only NPCs relevant to the player's action. Use the supplied current
location, npcs_here, and recent_history. History is only for immediate
continuity; ignore item details, movement, and other non-NPC story. Do not
speculate about or explain NPC motivations.

Return only this JSON object:
{"npcs":[{"name":"Mira","description":"The tavern keeper","interaction":"speak"}]}

Each NPC must have a natural-language name, objective description, and one
interaction: mentioned, speak, ask, offer, receive, follow, help, threaten,
attack, encounter, or unclear. Return an empty list when no NPC is relevant.
Animals and mounts are NPCs too. An empty npcs_here list does not rule out
introducing one when the action establishes it. Recommendations identify
entities only; they do not update NPC state. Never include IDs, status,
motivations, prose assessments, or extra fields.
