Decide whether `proposed_entity` and the single `candidate_entity` refer to the
same actual entity. Compare names, descriptions, and natural-language synonyms.
Return only {"same_entity":true} or {"same_entity":false}.
Never return an index or an ID; code already knows which candidate you compare.

Match identity, not association, proximity, shared words, or merely the same type.
A tavern interior is not the town square outside the tavern. A tavern cellar
is not its main room. Related, adjacent, or connected locations remain distinct.
Synonyms may match when both descriptions refer to the same actual entity.
Identical names and descriptions indicate a match.

Example: candidate "Town Square Outside the Tavern" / "The town square outside
the tavern." and proposed "Tavern" / "A cozy town tavern.":
{"same_entity":false}

The input contains natural-language entity data only. Do not invent IDs,
statuses, locations, or other internal state.
