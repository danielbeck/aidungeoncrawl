Identify the transition requested ONLY by `current_action` using the supplied
current location, exits, known_locations, and historical_summary.
The summary describes ALREADY completed events, not instructions to execute.
Use it only to resolve references such as "go back", never to repeat a previous
movement. After returning outside, "Head north, towards the Great Forest"
means leave the current outdoor location and enter the forest, not leave the
tavern again.
Return exactly one `leave` entry
for the current location and one `enter` entry for the destination when the
player moves. Always include both sides of a transition. Use `leave` for the
location the player departs and `enter` for the location they arrive at. Do
not report locations that are merely mentioned.

`current_location` is the authoritative starting location BEFORE this action.
Do not infer the starting location from historical_summary or from where the action
will end. Interaction labels describe what the PLAYER does to each location,
not what a doorway does or which side of it the player sees:
- `leave`: the player departs FROM this location. Copy the supplied
  `current_location` name and description exactly.
- `enter`: the player arrives AT this location after departing the current one.
  This is the destination requested by the action, not the starting location.

For "enter the tavern" while current_location is the town square, the square
is `leave` and the tavern is `enter`. For "leave the tavern for the square"
while current_location is the tavern, the tavern is `leave` and the square is
`enter`. Never reverse these labels. Return the departure entry first and
the arrival entry second.

Movement must contain exactly TWO entries total, never three or more.
Do not include the starting room again as `enter`: the player is already there.
For square-to-tavern movement, return only square/leave and tavern/enter,
NOT square/leave, square/enter, tavern/enter. Do not list intermediate rooms
or duplicate the departure or destination.

Return a JSON object with only a `locations` array. Each entry must contain exactly
`name`, `description`, and `interaction`. Populate it only with the departure and
destination for movement requested by `current_action`; do not invent locations
to fill the output format. When no movement is requested, return {"locations":[]}.

Each location must have a natural-language name, objective description, and
interaction exactly `enter` or `leave`. If the player does not move, return an
empty list. Do not create exits or decide persistent map state. Never include
IDs, exit state, NPC or item details, prose assessments, or extra fields.

A common error is to reverse the `leave` and `enter` actions. Confirm that you do not have the player entering their current location.

Before responding, verify that `current_action` requests movement. If it does not, return {"locations":[]}, even if `historical_summary` describes movement. Otherwise, `leave` must match `current_location`, and `enter` must identify a different destination requested by `current_action`.