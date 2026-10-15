# Product Spec

## Experience

A flexible, text-based fantasy adventure: the player describes an action, the
game determines its outcome, and the Dungeon Master narrates what happens next.
There are no required tabletop rules, character statistics, dice mechanics, or
combat system, and no elaborate character creation or predefined command menu.

The story should be coherent, responsive, and engaging:
- Respect established facts and plausible consequences, including failed attempts.
- Develop longer threads from player interests, with actionable clues, occasional
  fair puzzles, and opportunities to make meaningful choices.
- Preserve agency and vary pacing rather than imposing quests or constant threats.
- Explain in-world failure in character, without exposing implementation details.
- Treat player descriptions as attempted actions, not declarations of world
  facts or abilities. Ordinary setting-appropriate details can be introduced,
  but unusual creatures, equipment, or powers need established support; naming
  a penguin in a tavern must not conjure one into existence.

These are experience goals, not permission to claim unsupported state changes.

## Current implementation scope

### Starting and ending

New games begin at `town-square-outside-tavern`, displayed as "Town Square
Outside the Tavern", with empty inventory. Plausible starting equipment can be
established through a supported acquisition action.

The adventure is saved to the authenticated account. Starting a new game replaces
the current adventure. A game ends when the character dies or the player stops
it; ended games no longer accept actions.

### State and responsibilities

Deterministic code maintains canonical locations, exits, inventory, NPC identity,
and literal history. Models use natural-language entity descriptions, not IDs.
Managers interpret scoped actions, code reconciles supported effects, and the
narrator describes the resulting state. The comparator checks entity identity;
the summarizer supplies historical context.

The narrator does not propose persistent state operations or override outcomes.
Invalid required responses fail safely without partial saves.

### Locations and map

Locations have stable identities and exits connecting them. Movement intent is
extracted from the action alone. Named destinations are matched or created;
context-dependent targets use a resolver, and returning uses the last actual
movement. Ambiguous references ask for clarification rather than redirecting
the player to a familiar place.

Successful travel updates the current location, marks it visited, and adds
reciprocal traversable connections when needed. The state distinguishes known
and visited locations and identified, unexplored, or blocked exits, although
current turn processing does not expose every possible map edit.

### Items and objects

The inventory manager reports only `take` and `drop`. Taking acquires an available
local item or plausibly establishes a new one; dropping moves a carried item
to the starting room. Identity matching prevents duplicate records from alternate
names. Missing drop targets are reported rather than created.

Consumption, giving items to NPCs, and other item transitions are not currently
supported. An action such as "Drink the potion" must not be narrated as a
persisted consumption effect.

Existing persistent object records are preserved and displayed. Current turns
do not create or change their state. Incidental scenery and clues can appear
in narration without becoming mechanically tracked objects.

### NPCs

NPCs, including animals and mounts, can be identified or introduced by relevant
actions. The comparator resolves identity; the NPC manager classifies interactions,
not identity matches or state mutations. Existing NPCs keep their stored location
and status; moving, removing, or killing them is not currently modeled.

### History and continuity

The full literal action/narration record is retained for display and debugging.
A separate overview of at most 1500 characters is generated from the entire
saved history, emphasizing recent events and retaining relevant goals and clues.
It is background, not authority over current state or instructions to replay.

Narration returns without waiting for post-turn summary preparation. The next
turn regenerates missing/stale context if needed. Preparation errors are visible
and late results cannot overwrite newer state.

## Future capabilities, not current guarantees

The broader experience could eventually support:
- Creating and changing persistent doors, locks, containers, and other objects.
- Consumption, NPC transfers, crafting, loss, and other inventory effects.
- NPC movement, removal, death, and richer persistent character state.
- Narratively justified changes to established exit descriptions/traversability.

These require explicit contracts and deterministic support before narration can
treat them as completed effects. Preserve continuity and entity identity when
adding them; avoid a general-purpose rules engine or complex simulation unless
the experience requires it.

For current contracts, execution order, and edge cases, see
[Implementation](IMPLEMENTATION.md). For endpoints and operations, see the
[backend guide](../backend/README.md).
