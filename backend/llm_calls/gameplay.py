"""Structured LLM calls used by the gameplay turn processor."""
try:
    from backend.llm_calls.base import LLMCall, LLMResponseError
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError


def _validate_entity_interactions(data, field, allowed_interactions, call_name):
    if not isinstance(data, dict) or set(data) != {field}:
        raise LLMResponseError(
            f'{call_name}: response must contain only {field}.', details=data)

    interactions = data[field]
    if not isinstance(interactions, list) or len(interactions) > 20:
        raise LLMResponseError(
            f'{call_name}: {field} must be a list of at most 20 entities.', details=data)

    for index, entry in enumerate(interactions, 1):
        if not isinstance(entry, dict) or set(entry) != {'name', 'description', 'interaction'}:
            raise LLMResponseError(
                f'{call_name}: {field} entry {index} must contain only name, description, '
                'and interaction.', details=data)
        if any(not isinstance(entry[key], str) or not entry[key].strip()
               or len(entry[key].strip()) > 1000 for key in ('name', 'description')):
            raise LLMResponseError(
                f'{call_name}: {field} entry {index} needs non-empty name and description.',
                details=data)
        if entry['interaction'] not in allowed_interactions:
            raise LLMResponseError(
                f'{call_name}: {field} entry {index} has an invalid interaction.',
                details=data)
    return data


class InventoryManagerCall(LLMCall):
    name = 'inventory_manager'

    def format_user_input(self, user_input):
        return f'Classify only item and object interactions in this game context:\n{user_input}'

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, 'items',
            ('mentioned', 'search', 'inspect', 'use', 'acquire', 'give', 'consume', 'drop', 'unclear'),
            self.name,
        )


class MapManagerCall(LLMCall):
    name = 'map_manager'

    def format_user_input(self, user_input):
        return f'Classify only location and movement interactions in this game context:\n{user_input}'

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, 'locations',
            ('mentioned', 'remain', 'approach', 'enter', 'leave', 'travel', 'discover', 'unclear'),
            self.name,
        )


class NPCManagerCall(LLMCall):
    name = 'npc_manager'

    def format_user_input(self, user_input):
        return f'Classify only interactions with NPCs in this game context:\n{user_input}'

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, 'npcs',
            ('mentioned', 'speak', 'ask', 'offer', 'receive', 'follow', 'help',
             'threaten', 'attack', 'encounter', 'unclear'),
            self.name,
        )


class NarratorCall(LLMCall):
    name = 'game_narrator'

    def format_user_input(self, user_input):
        return f'Resolve the player action and return narration and notable entities:\n{user_input}'

    def validate_response(self, data):
        if set(data) != {'narration', 'action_succeeded', 'game_over', 'notable_elements'}:
            raise LLMResponseError(
                'game_narrator: response must contain only narration, action_succeeded, '
                'game_over, and notable_elements.', details=data)
        if not isinstance(data['narration'], str) or not data['narration'].strip():
            raise LLMResponseError('game_narrator: narration must be non-empty text.', details=data)
        if not isinstance(data['action_succeeded'], bool) or not isinstance(data['game_over'], bool):
            raise LLMResponseError('game_narrator: action_succeeded and game_over must be booleans.', details=data)
        notable = data['notable_elements']
        if not isinstance(notable, dict) or set(notable) != {'items', 'locations', 'npcs'}:
            raise LLMResponseError(
                'game_narrator: notable_elements must contain only items, locations, and npcs.',
                details=data)
        for category, elements in notable.items():
            if not isinstance(elements, list) or len(elements) > 20:
                raise LLMResponseError(
                    f'game_narrator: {category} must be a list of at most 20 entities.',
                    details=data)
            for index, element in enumerate(elements, 1):
                if not isinstance(element, dict) or set(element) != {'name', 'description'}:
                    raise LLMResponseError(
                        f'game_narrator: {category} entry {index} must contain only name and description.',
                        details=data)
                if any(not isinstance(element[field], str) or not element[field].strip()
                       or len(element[field].strip()) > 1000
                       for field in ('name', 'description')):
                    raise LLMResponseError(
                        f'game_narrator: {category} entry {index} needs non-empty name and description.',
                        details=data)
        return data


class ComparatorCall(LLMCall):
    name = 'comparator'

    def format_user_input(self, user_input):
        return f'Determine whether the proposed entity is already represented:\n{user_input}'

    def validate_response(self, data):
        if set(data) != {'already_present'} or not isinstance(data['already_present'], bool):
            raise LLMResponseError(
                'comparator: response must contain only boolean already_present.', details=data)
        return data
