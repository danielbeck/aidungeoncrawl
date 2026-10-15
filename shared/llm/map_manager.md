Extract movement intent from `current_action`. Return JSON only.
You receive only the action. World state is deliberately withheld to avoid
substituting a familiar place for the requested destination.

Movement means the player asks to change location. It does NOT require a known
destination. "Go there" and "follow him" ARE movement even though their targets
need clarification later. Do not resolve references yourself.

Extract the complete destination phrase, NOT a movement verb or preposition.
A place explicitly specified by name OR type is `named`: tavern, wilderness,
forest, North Gate. "Into" by itself is never a destination.
An explicit place ALWAYS takes priority over a compass direction in the same
action: for "Head north towards the Great Forest", extract "the Great Forest"
as named, NOT "north" as directional. Do not discard the destination after
"towards", "toward", "to", or "into".

Kinds:
- named: explicitly specified place or type of place.
- back: retracing the last journey, without an explicit destination.
- relative: outside, inside, upstairs, downstairs.
- directional: compass direction or a route/doorway, without a specified place.
- exploratory: open-ended exploration, rather than travel to a specified place.
- reference: a pronoun or unresolved reference to a place or person.

Examples (action -> complete response):
"Head into the wilderness."
-> {"movement":true,"target":{"text":"the wilderness","kind":"named"}}
"Make my way to the tavern."
-> {"movement":true,"target":{"text":"the tavern","kind":"named"}}
"Head north towards the Great Forest."
-> {"movement":true,"target":{"text":"the Great Forest","kind":"named"}}
"Enter North Gate."
-> {"movement":true,"target":{"text":"North Gate","kind":"named"}}
"Return to the square."
-> {"movement":true,"target":{"text":"the square","kind":"named"}}
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
"Look at the tavern."
-> {"movement":false,"target":null}
"Do not enter the tavern."
-> {"movement":false,"target":null}
"Drop the key."
-> {"movement":false,"target":null}

For other wording, apply these meanings, not literal phrase matching.
Respect negation and conditions; do not execute hypothetical journeys.
For compound actions, extract the requested movement, not incidental places.
Preserve destination wording and qualifiers. Never choose a different place,
invent a destination, produce leave/enter entries, or output IDs or narration.
