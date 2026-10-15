"""Structured LLM calls used by the gameplay turn processor."""
import logging

try:
    from backend.llm_calls.base import LLMCall, LLMResponseError
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError


logger = logging.getLogger(__name__)

MANAGER_INTERACTIONS = {
    'inventory': ('items', ('take', 'drop')),
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
    temperature = 0

    def format_user_input(self, user_input):
        return f'Extract movement intent from this action, without choosing a map location:\n{user_input}'

    @staticmethod
    def validate_response(data):
        if (not isinstance(data, dict) or set(data) != {'movement', 'target'}
                or not isinstance(data['movement'], bool)):
            raise LLMResponseError('map_manager: expected movement boolean and target.', details=data)
        target = data['target']
        if not data['movement']:
            if target is not None:
                logger.warning('map_manager: discarded non-null target because movement is false.')
                return {**data, 'target': None}
        elif (not isinstance(target, dict) or set(target) != {'kind', 'text'}
              or target['kind'] not in (
                  'named', 'back', 'relative', 'directional', 'exploratory', 'reference')
              or not isinstance(target['text'], str) or not target['text'].strip()
              or len(target['text']) > 2000):
            raise LLMResponseError('map_manager: invalid movement target.', details=data)
        return data


class MovementResolverCall(LLMCall):
    name = 'movement_resolver'
    temperature = 0

    def format_user_input(self, user_input):
        return f'Resolve this already-extracted movement intent without changing its target:\n{user_input}'

    @staticmethod
    def validate_response(data):
        if not isinstance(data, dict) or set(data) != {'destination', 'clarification'}:
            raise LLMResponseError(
                'movement_resolver: expected destination and clarification only.', details=data)
        destination, clarification = data['destination'], data['clarification']
        if destination is None:
            if clarification is None:
                logger.warning(
                    'movement_resolver: missing destination and clarification; '
                    'requesting destination clarification without moving.')
                return {**data, 'clarification': 'Where would you like to go?'}
            if (not isinstance(clarification, str) or not clarification.strip()
                    or len(clarification) > 1000):
                raise LLMResponseError(
                    'movement_resolver: unresolved movement requires clarification.', details=data)
        elif (clarification is not None or not isinstance(destination, dict)
              or set(destination) != {'name', 'description'}
              or any(not isinstance(destination[field], str) or not destination[field].strip()
                     or len(destination[field]) > 1000 for field in ('name', 'description'))):
            raise LLMResponseError('movement_resolver: invalid destination.', details=data)
        return data


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


class HistorySummarizerCall(LLMCall):
    name = 'history_summarizer'

    def format_user_input(self, user_input):
        return f'Summarize these already-completed exchanges, emphasizing recent events:\n{user_input}'

    @staticmethod
    def validate_response(data):
        if (not isinstance(data, dict) or set(data) != {'summary'}
                or not isinstance(data['summary'], str) or not data['summary'].strip()
                or len(data['summary']) > 1500):
            raise LLMResponseError(
                'history_summarizer: response must contain only a non-empty summary '
                'of at most 1500 characters.', details=data)
        return data
