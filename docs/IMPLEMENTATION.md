# Implementation

## Deterministic vs LLM code

Certain parts of the game workflow will be handled by LLMs, other parts by deterministic code. These will be described in this doc.

## LLM code overview

LLM calls of a given type will generally have a hardcoded base prompt, and will receive 

Types of LLM calls will include the following:
* The inventory manager: decides based on context what objects are referenced by the player's intended action, whether they can be found in the player's inventory or are available in the current location, and returns a narrative instruction of whether the action should be allowed to proceed and what effect it will have on the player's inventory
* The map manager: decides based on context whether the player is moving to a new location, an existing location, or staying put.  If the player is moving, returns a narrative description of the result as well as instructions on how to modify the "map" as a result.
* The NPC manager: decides based on context and the location's existing list of NPCs, whether a referenced NPC exists, should be created, or should be removed from the location (if they leave or die for example).
* The narrator: takes input from the inventory manager, the map manager, and the rest of the context, decides what should happen next to best advance the narrative.  Returns this description, plus whatever changes need to be made to the map or inventory.
* The summarizer: periodically compacts the history of the narrative.


All LLM calls except the summarizer will receive the same context as part of their prompt: 
* the (summarized) history of play, 
* the current location and its identified exits, 
* the player's current inventory,
* The list of NPCs in the current location,
* the player's intended next action.

The summarizer only needs to see the history, so it can compact it when it gets too long.

## Communication protocol

TODO: idfentify the format in which the context will be passed to the LLMs


TODO: several LLMs need to be able to respond by changing something that persists in the deterministic code -- items, NPCs, locations.  Identify the format by which the LLM will convey these to the deterministic code.

## Deterministic code

The map is a graph of location names with zero or more exits, pointing to other locations. Keep an ID and a name for each.  This collection will grow as the narrator adds new locations.

The inventory is an array of items.

The npc list is effectively an inventory of non player characters, attached to each location in the map.

The history is an array of past events in the story.  Initially this will literally be the game's text, followed by teh player's response, followed by the game's response, etc., but as this gets long the summarizer will shorten it, reducing detail in the oldest parts of the narrative so far until it's brought down to a manageable size.  The past 5 or 10 exchanges should be kept intact for narrative continuity.

