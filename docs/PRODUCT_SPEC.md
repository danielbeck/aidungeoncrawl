This is a simulated game of Dungeons and Dragons.

The user interface is simple. The game tells the 
player where they are and what is happening. The player tells the game, in text, what they want to do.  The game decides whether that's possible, decides what should happen, and tells the user what happened.  This cycle continues until the player's character dies or the player stops playing.

Some of this will be LLM driven.  The supplied repo contains sample backend code for connecting to an LLM and receiving a response; use those as a model for implementing this game.


## Responsibilities

### Locations
The game should always start at the same location, "town square outside the tavern". 

New locations can be added narratively, if the LLM decides to allow it.  (For example, "enter the tavern" from the default location outside the tavern should obviously take the user to a new location.)

The "map" of existing locations should be kept track of in deterministic code.

The LLM needs a way to communicate to the deterministic code when the map should change (e.g. by adding a new location, or by an exit closing off for narrative reasons.)

The descriptions of locations shouldn't necessarily be preserved verbatim; the same location may be described differently depending on the current point in the narrative, but the previously identified exits should still generally persist and should continue pointing to the same named location.

### Items
The player's inventory is not defined at first, but should grow as a result of the narrative.  The player is assumed to be a reasonably equipped low level character at first, so commands like "draw my sword" should result in the sword being added to the player's inventory list for later reference.

An LLM should be responsible for determining, based on the player's existing inventory, location, and the narrative as a whole, whether any given item should be added to or removed from the player's inventory. 

The inventory list itself, the array of already-identified items, should be managed in deterministic code.

The LLM needs a way to communicate to the deterministic code when the inventory list should change (by adding or removing an item).

### NPCs
Each location might have zero or more NPC characters in it.  As with items and locations, these can be conjured into existence if the narrative calls for it. (For example if a player is in the tavern and orders a beer, the narrative might create a barkeep or serving wench who should persist at least as long as the player remains in that location. Unless they're removed by other narrative means, of course.)

As with items, these are not defined at first but should be kept track of in deterministic code after the narrative has identified them.