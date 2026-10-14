Identify only locations relevant to the player's action and classify the
movement intent. Use the supplied current location, exits, known_locations,
and recent_history. History is only for immediate continuity; ignore item
details, NPC motivations, dialogue, and other non-map story.

Return only this JSON object:
{"locations":[{"name":"old cellar","description":"A cellar beneath the tavern","interaction":"enter"}]}



Each location must have a natural-language name, objective description, and
one interaction: mentioned, enter, leave, 
or unclear. Return an empty list when no location is relevant.  Recommendations identify locations only; they do
not create exits or move the player. Never include IDs, exit state, prose
assessments, or extra fields.

Note that any "enter" action requires a corresponding "leave" action, and vice-versa. The player must always be in a location; leaving one implies entering another. 