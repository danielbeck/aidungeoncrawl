Identify only NPCs relevant to `current_action`. Use the supplied current
location, npcs_here, and historical_summary. These events have ALREADY happened;
they are not current instructions. Use history only to resolve references such
as "ask her"; do not repeat earlier conversations or encounters.
Current state overrides the historical summary. History is only for immediate
continuity; ignore item details, movement, and other non-NPC story. Do not
speculate about or explain NPC motivations.

Return a JSON object with only an `npcs` array. Each entry must contain exactly
`name`, `description`, and `interaction`. Populate it only with NPCs relevant to
`current_action`; do not invent NPCs to fill the output format. When no NPC is
relevant, return {"npcs":[]}, not an entry named "None".

Each NPC must have a natural-language name, objective description, and one
interaction: mentioned, speak, ask, offer, receive, follow, help, threaten,
attack, encounter, or unclear. Return an empty list when no NPC is relevant.
Animals and mounts are NPCs too. An empty npcs_here list does not rule out
introducing one when the action establishes it. Recommendations identify
entities only; they do not update NPC state. Never include IDs, status,
motivations, prose assessments, or extra fields.
