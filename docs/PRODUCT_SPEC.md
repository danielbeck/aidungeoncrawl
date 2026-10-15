# Product Spec

## Overview

This is a simulated, text-based Dungeons & Dragons–style adventure. The LLM acts as the Dungeon Master, describing the world, interpreting player actions, and advancing an interesting, internally consistent story.

The user interface is simple:

1. The game describes the player's current situation.
2. The player enters a free-text description of what they want to do.
3. The game determines what the player intended, whether the action is possible, and what consequences follow.
4. The game updates its state and narrates the outcome.
5. This cycle continues until the player stops playing or the character dies.

The game should be flexible enough to support creative, unanticipated player actions. It should not require the player to choose from a predefined set of commands.

Some decisions will be LLM-driven; others will be handled by deterministic code. The LLM narrator proposes changes to game state; the deterministic code persists them and provides that data for future context.

The supplied repository contains sample backend code for connecting to an LLM and receiving a response. Use this as the starting point for the implementation, preserving existing working functionality where practical.

## Gameplay

### General principles

* There are no required tabletop gaming rules, character statistics, dice mechanics, or combat systems.
* The narrator should create an interesting, coherent, responsive storyline based on the player's actions.
* The player may attempt any action they can describe in natural language.
* Actions should have plausible consequences consistent with the established world and previous events.
* The game should distinguish between what is possible, what is plausible but uncertain, and what is impossible given established facts.
* The game should not arbitrarily reject creative actions simply because they were not anticipated by the implementation.
* Failed actions should still advance the narrative where appropriate. The narrator should explain what happened in character rather than returning a generic error.
* The game should not silently change established facts merely to accommodate a new action.
* The game should maintain continuity across turns, including the identity and state of previously encountered locations, items, and NPCs.

### Starting state

Every new game starts in the location identified by the stable ID `town-square-outside-tavern`, displayed as "Town Square Outside the Tavern".

The initial inventory is empty. The game may establish reasonable starting equipment as it becomes relevant to the narrative. For example, when the player attempts to draw their sword, the game may establish that the character possesses a sword, add it to inventory, and continue the action.

The game should not require an elaborate character-creation process.

### Narrative freedom and consistency

The LLM may introduce new locations, objects, NPCs, and events when appropriate. Once introduced and accepted into the game state, these entities must be tracked by deterministic code.

The LLM may elaborate on established facts but should not contradict them without an explicit narrative event that explains the change.

Managers interpret actions, and deterministic code resolves their results
directly against stored state before narration. The current location identifies
the departure; mismatched departures are reported and ignored, never automatically
reversed. The narrator
describes the resolved outcome; it does not echo or override state, and a turn
does not depend on matching entity lists between LLMs. Original and effective
manager results are retained in the turn response.

For example, a previously established locked door should not become an open archway simply because the narrator generates a different description of the room.

## Responsibilities

### Locations and map

The game begins at a fixed starting location.

The map is a graph of locations and their exits. Each location has a stable ID and a display name. Exits connect locations using stable IDs rather than display names.

New locations may be introduced as the story progresses. The LLM can propose a new location when an action implies entering a previously unidentified place, such as entering the tavern from the town square.

The deterministic game engine maintains the map and applies approved changes to it.

The map should distinguish between:

* Locations that have been identified by the game.
* Locations the player has visited.
* Exits that have been identified.
* Exits that are currently traversable.
* Exits whose destinations have not yet been identified.

An exit should generally continue pointing to the same location once established. Its description and traversability may change when justified by narrative events.

The same location may be described differently on different turns. Its narrative description is not the authoritative representation of its identity, exits, or persistent object state.

The map manager interprets movement using only the player's action, without
seeing the current room, known destinations, or historical journeys. Named
destinations are matched against existing places or created when new; relative,
directional, exploratory, and referential targets are resolved separately using
the relevant world context. "Go back" uses the last actual movement rather than
prose history. Ambiguous references prompt clarification instead of redirecting
the player to a familiar place. Deterministic code supplies the departure,
creates reciprocal traversable exits if needed, marks the destination visited,
and updates the current location.

### Items and inventory

The inventory is an array of identified items currently carried by the player.

Items are not predefined. They may be introduced by the narrative, discovered in locations, acquired from NPCs, created, consumed, lost, or otherwise changed as the story progresses.

The inventory manager reports only `take` and `drop` interactions. Deterministic
code resolves item identity through the Comparator. Taking moves a matched item
available in the current location (or held by a present NPC) into inventory; an
item newly established by the action may be created directly in inventory.
Dropping requires a matched item currently carried by the player and moves it
to the current location.

Other item effects remain unsupported by the current deterministic rules:

For example:

* "Draw my sword" may establish a sword in the player's inventory if no sword has been identified yet.
* "Pick up the silver key" may acquire an existing item in the current location.
* "Give the guard my torch" may transfer an item out of the player's inventory.
* "Drink the potion" may consume a potion if one is present.
* "Use the key I found earlier" may refer to an existing item by its established identity.

The game distinguishes between an item that exists in the world, an item the
player knows about, and an item the player possesses.

The manager classifies the intended interaction; deterministic code applies
only supported `take` and `drop` transitions. Transfer to NPCs, consumption,
loss, and other changes are not supported.

An item should retain its identity across turns even if the narrator uses different words to describe it. For example, "iron key", "the key", and "the key I found in the hallway" may all refer to the same item.

When a reference is ambiguous, the game should resolve it from context when possible and ask for clarification when necessary.

The game should not create duplicate items simply because the player or narrator uses different names for the same item.

### Objects in locations

Locations may contain objects that are not in the player's inventory. These include furniture, containers, doors, scenery, treasures, and other interactive objects.

Objects may be introduced as part of a location's initial description or discovered through player actions.

The game should track relevant persistent object state. Examples include:

* A chest that is locked or unlocked.
* A door that is open or closed.
* A container whose contents have been revealed.
* An object that has been taken, destroyed, or moved.

Objects do not all need to be individually modeled. The implementation should track objects that affect future actions or narrative consistency and allow the narrator to describe incidental scenery without persisting every detail.

An object discovered in a location should retain its identity and state when the player leaves and returns.

### NPCs

Each location may contain zero or more identified NPCs.

NPCs can be introduced when the narrative calls for them. For example, ordering a beer in the tavern may introduce a barkeep.

NPCs should be associated with stable IDs and tracked in deterministic code. Their location, identity, and relevant persistent state should be maintained across turns.

NPCs may move between locations, leave the game world, or die as a result of narrative events.

The NPC manager determines whether a referenced character corresponds to an existing NPC, whether a new NPC should be introduced, and whether an NPC should be moved or removed.

An NPC who leaves a location should not automatically cease to exist. Removal from a location and removal from the game world are distinct operations.

The game should avoid creating duplicate NPCs when a character is referred to by a different name or description.

### Narrative history

The game maintains a record of player actions, narrative responses, and significant world events.

This history provides context for interpreting ambiguous references and maintaining narrative continuity. It is not the authoritative source of current inventory, location, or object state.

Deterministic code retains the complete, literal, ordered history of player
actions and narrator responses. Summarization must never replace, truncate, or
rewrite this record; the full conversation remains available for display and
debugging.

After each completed action, including an unsuccessful in-world attempt, the
engine submits the entire literal history, including that action and its
narration, to a Summarizer. The Summarizer produces a brief overview of past
events, emphasizing the most recent events while retaining older facts needed
for continuity. It summarizes completed events, not pending instructions.
Each overview is generated from the full literal history, not from a previous
summary alone.

On the next turn, managers and the narrator receive this summarized history
instead of literal exchanges. The current action is supplied separately and is
the only instruction to respond to. The summary is background context for
continuity and reference resolution, never authority over deterministic world
state. The Comparator continues to receive only the entities it compares.

The overview is stored separately and limited to 1500 characters. If
summarization fails, the completed turn is not saved; the error is surfaced
without altering the previously saved history or state.

### Character death and game termination

The game may end when the narrative establishes that the player character has died, or when the player chooses to stop playing.

There is no requirement for a detailed combat or health system. Character death is a narrative state that must be recorded deterministically once accepted by the game engine.

When the game ends, the interface should display the final narrative response and stop accepting further gameplay actions for that session unless a future feature explicitly supports restarting or continuing.

## Initial implementation scope

The initial implementation should prioritize a working end-to-end gameplay loop over comprehensive world simulation.

The first version should support:

* Starting a new game in the fixed starting location.
* Free-text player input.
* LLM-driven interpretation and narration.
* Deterministic identity reconciliation for inventory items, locations, and NPCs.
* Creation and reuse of entity records without LLM-generated IDs.
* A complete literal history of player actions and narrator responses, with a
  separate recent-focused summary for LLM context.
* Preservation and display of existing persistent object records.
* Graceful handling of failed or invalid model responses.

Avoid implementing combat statistics, dice mechanics, complex NPC simulation, or a general-purpose rules engine.
