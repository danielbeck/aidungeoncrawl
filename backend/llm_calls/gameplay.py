"""Structured LLM calls used by the gameplay turn processor."""
try:
    from backend.llm_calls.base import LLMCall, LLMResponseError
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError


MANAGER_INTERACTIONS = {
    'inventory': ('items', ('take', 'drop')),
    'map': ('locations', ('enter', 'leave')),
    'npcs': ('npcs', ('mentioned', 'speak', 'ask', 'offer', 'receive', 'follow', 'help',
                      'threaten', 'attack', 'encounter', 'unclear')),
}


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
            data, *MANAGER_INTERACTIONS['inventory'], self.name,
        )


class MapManagerCall(LLMCall):
    name = 'map_manager'

    def format_user_input(self, user_input):
        return f'Classify only location and movement interactions in this game context:\n{user_input}'

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, *MANAGER_INTERACTIONS['map'], self.name,
        )


class NPCManagerCall(LLMCall):
    name = 'npc_manager'

    def format_user_input(self, user_input):
        return f'Classify only interactions with NPCs in this game context:\n{user_input}'

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, *MANAGER_INTERACTIONS['npcs'], self.name,
        )


class NarratorCall(LLMCall):
    name = 'game_narrator'

    def format_user_input(self, user_input):
        return f'Narrate the player action using the outcome already resolved by the game engine:\n{user_input}'

    @staticmethod
    def validate_response(data):
        if not isinstance(data, dict) or set(data) != {
                'narration', 'action_succeeded', 'game_over'}:
            raise LLMResponseError(
                'game_narrator: response must contain only narration, action_succeeded, '
                'and game_over.', details=data)
        if not isinstance(data['narration'], str) or not data['narration'].strip():
            raise LLMResponseError('game_narrator: narration must be non-empty text.', details=data)
        if not isinstance(data['action_succeeded'], bool) or not isinstance(data['game_over'], bool):
            raise LLMResponseError('game_narrator: action_succeeded and game_over must be booleans.', details=data)
        return data


class ComparatorCall(LLMCall):
    name = 'comparator'

    def format_user_input(self, user_input):
        return f'Determine whether the proposed entity and this one candidate are the same entity:\n{user_input}'

    def validate_response(self, data):
        if (not isinstance(data, dict)
                or set(data) != {'same_entity'}
                or not isinstance(data['same_entity'], bool)):
            raise LLMResponseError(
                'comparator: response must contain boolean same_entity only.',
                details=data)
        return data
