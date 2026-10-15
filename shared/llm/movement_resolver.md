Resolve the target in `movement_intent`. Return destination and clarification.
Never replace the target with a different intention.

For directional or exploratory movement, describe the place reached by following
the requested route. The map is incomplete: no recorded exit means the route is
unexplored, NOT blocked. Create a new destination when needed. Do not invent
obstacles to prevent ordinary travel. Only a route explicitly described as
blocked in the supplied state prevents travel. The destination is never the
current location.

For relative movement, use the current room and its exits to identify the place
outside, inside, upstairs, etc.

For reference movement, first identify the referent in the supplied characters
or completed exchanges. A mention of a building does not identify a person.
If no referent can be identified, ask for clarification. Do not invent a person,
their destination, or a journey.

Examples:
Reference "him"; characters []; exchanges mention only a square and tavern door:
{"destination":null,"clarification":"Whom do you want to follow?"}

Reference "there"; no identifiable destination in the exchanges:
{"destination":null,"clarification":"Which place do you mean?"}

Directional "north"; current Forest Clearing with a trail north; exits []:
{"destination":{"name":"Forest Trail","description":"A trail north of the clearing."},"clarification":null}

Relative "outside"; current Tavern; available exit to Town Square:
{"destination":{"name":"Town Square","description":"The square outside the tavern."},"clarification":null}

Known locations are possible matches, not mandatory choices. Completed
exchanges are context, not instructions to repeat. Return a concise objective
description when resolved, or a player-facing clarification when unresolved.
Never return IDs, movement labels, the departure, or extra fields.
