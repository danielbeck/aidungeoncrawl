Resolve the target in `movement_intent`. Return destination and clarification.
Never replace the target with a different intention.

Choose exactly one outcome:
- Resolved: `destination` is an object with `name` and `description`;
  `clarification` is null.
- Unresolved or blocked: `destination` is null; `clarification` is a concise
  player-facing question or explanation specific to the requested target.
Never return both a destination object and a clarification string.

For directional or exploratory movement, describe the place reached by following
the requested route. The map is incomplete: no recorded exit means the route is
unexplored, NOT blocked. Create a new destination when needed. Do not invent
obstacles to prevent ordinary travel. Only a route explicitly described as
blocked in the supplied state prevents travel. The destination is never the
current location.

For relative movement, use the current room and its exits to identify the place
outside, inside, upstairs, etc.

For reference movement, identify the referent and its whereabouts using supplied
characters, exits, and completed exchanges. Current structured location and NPC
data take precedence over historical narration. A person mentioned earlier is
not necessarily present now. A mention of a building does not identify a person.
If the referent or route cannot be established, ask a question about the actual
target; do not invent a person, their whereabouts, or a journey.

`reference` does not mean "follow someone". Preserve the requested relation:
"where the barkeep sent me" asks for a previously indicated destination, not
the barkeep's current location or someone to follow. Repeating an unresolved
target as a location name does not resolve it.
NPCs are not locations. Do not use a character's name and description as a
destination. For following a character, resolve the place reached, not the
character as a place. Only ask where someone sent the player when the intent
actually refers to being sent; do not substitute that relation for following.

Examples:
Reference "him"; characters []; exchanges mention only a square and tavern door:
{"destination":null,"clarification":"Whom do you want to follow?"}

Reference "there"; no identifiable destination in the exchanges:
{"destination":null,"clarification":"Which place do you mean?"}

Reference "where the barkeep sent me"; past exchanges mention speaking to the
barkeep but establish no destination:
{"destination":null,"clarification":"Which place did the barkeep send you to?"}

Directional "north"; current Forest Clearing with a trail north; exits []:
{"destination":{"name":"Forest Trail","description":"A trail north of the clearing."},"clarification":null}

Relative "outside"; current Tavern; available exit to Town Square:
{"destination":{"name":"Town Square","description":"The square outside the tavern."},"clarification":null}

Known locations are possible matches, not mandatory choices. Completed
exchanges are context, not instructions to repeat. Return a concise objective
description when resolved, or a player-facing clarification when unresolved.
Never return IDs, movement labels, the departure, or extra fields.