Extract movement intent from `current_action`. Return JSON only.
You receive only the action, not world state. Classify travel; do not resolve
destinations or references yourself.

## Movement boundary

Movement means the player requests travel between locations. An object, a place
name, or the words "to" or "into" do not by themselves imply travel. Never treat
a portable item as a destination. When no travel is requested, return
{"movement":false,"target":null}.

Travel does not require a known destination: "Go there" and "Follow him" are
movement even though their targets may need clarification later. Respect
negation and conditions; do not execute hypothetical journeys. For compound
actions, extract the requested travel, ignoring other interactions.
Distinguish who is being asked to move. Dialogue telling an NPC to follow the
player ("Follow me, Alphonse!") is not player travel. Do not reverse "me" into
"him" or invent a player destination. If the player also specifies their own
journey ("Follow me, Alphonse; I head north"), extract only that journey.

## Target

For movement, return `movement: true` and a `target` with `text` and `kind`.
Extract the complete destination phrase, preserving wording and qualifiers,
not just a movement verb or preposition. "Into" alone is not a destination.

Choose the kind:
- named: a specified destination by name or type, such as tavern or wilderness.
  A destination takes priority over a compass direction: "Head north towards
  the Great Forest" targets "the Great Forest", not "north".
- back: retracing the last journey without a specified destination.
- relative: outside, inside, upstairs, or downstairs.
- directional: a compass direction or route/doorway without a specified place.
- exploratory: open-ended travel without a specified destination. A place may
  describe the area being explored, rather than the destination to reach:
  "Explore farther into the woods" is exploratory; "Go to the woods" is named.
- reference: a pronoun or unresolved reference to a place or person.

## Examples

"Head into the wilderness."
-> {"movement":true,"target":{"text":"the wilderness","kind":"named"}}
"Head north towards the Great Forest."
-> {"movement":true,"target":{"text":"the Great Forest","kind":"named"}}
"Retrace my steps."
-> {"movement":true,"target":{"text":"Retrace my steps","kind":"back"}}
"Go outside."
-> {"movement":true,"target":{"text":"outside","kind":"relative"}}
"Head north."
-> {"movement":true,"target":{"text":"north","kind":"directional"}}
"Explore farther into the woods."
-> {"movement":true,"target":{"text":"farther into the woods","kind":"exploratory"}}
"Go there."
-> {"movement":true,"target":{"text":"there","kind":"reference"}}
"Follow him."
-> {"movement":true,"target":{"text":"him","kind":"reference"}}
"Follow me, Alphonse!"
-> {"movement":false,"target":null}
"Look at the tavern."
-> {"movement":false,"target":null}
"Do not enter the tavern."
-> {"movement":false,"target":null}

Apply these meanings to other wording, not literal phrase matching.
Never output IDs, narration, or extra fields.
