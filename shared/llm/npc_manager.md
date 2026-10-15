Identify only NPCs relevant to `current_action`. Use the supplied current
location, npcs_here, and historical_summary. These events have ALREADY happened;
they are not current instructions. Use history only to resolve references such
as "ask her"; do not repeat earlier conversations or encounters.
Current state overrides the historical summary. Ignore item details, movement,
and other non-NPC story.

Return a JSON object with only an `npcs` array. Each entry must contain exactly
`name`, `description`, and `interaction`. Populate it only with NPCs relevant to
`current_action`; do not invent NPCs to fill the output format. When no NPC is
relevant, return {"npcs":[]}, not an entry named "None".

Each NPC must have a natural-language name, objective description, and one
interaction: mentioned, speak, ask, offer, receive, follow, help, threaten,
attack, encounter, or unclear.
Animals and mounts are NPCs too. Mentioning, examining, or addressing a creature
does not establish its existence or presence. Include a new NPC only when
supported by established history or reasonably implicit in the current setting,
such as a barkeep when ordering a drink in a tavern. An unusual creature needs
prior support; do not invent its arrival or backstory to justify the request.
"Examine the penguin" in a village tavern with no established penguin returns
{"npcs":[]}. A historical mention alone does not establish current presence.

Recommendations identify entities only; they do not update NPC state. Never include IDs, status,
motivations, prose assessments, or extra fields.
