"""Structured LLM calls used by the gameplay turn processor."""
import logging

try:
    from backend.harness.llm_calls import LLMCall, LLMResponseError
except ImportError:
    from harness.llm_calls import LLMCall, LLMResponseError


logger = logging.getLogger(__name__)

MANAGER_INTERACTIONS = {
    'inventory': ('items', ('take', 'drop', 'give', 'consume', 'break')),
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
        expected_fields = {'name', 'description', 'interaction'}
        if field == 'items' and isinstance(entry, dict) and entry.get('interaction') == 'give':
            expected_fields.add('recipient')
        if not isinstance(entry, dict) or set(entry) != expected_fields:
            raise LLMResponseError(
                f'{call_name}: {field} entry {index} must contain '
                f'{", ".join(sorted(expected_fields))}.', details=data)
        if 'recipient' in expected_fields and (
                not isinstance(entry['recipient'], str) or not entry['recipient'].strip()
                or len(entry['recipient']) > 1000):
            raise LLMResponseError(
                f'{call_name}: give requires a non-empty recipient name.', details=data)
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

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, *MANAGER_INTERACTIONS['inventory'], self.name,
        )


class MapManagerCall(LLMCall):
    name = 'map_manager'
    temperature = 0

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

    @staticmethod
    def validate_response(data):
        if not isinstance(data, dict) or set(data) != {'destination', 'clarification'}:
            raise LLMResponseError(
                'movement_resolver: expected destination and clarification only.', details=data)
        destination, clarification = data['destination'], data['clarification']
        if destination is not None and isinstance(clarification, str) and clarification.strip():
            if len(clarification) > 1000:
                raise LLMResponseError(
                    'movement_resolver: clarification exceeds 1000 characters.', details=data)
            logger.warning(
                'movement_resolver: conflicting destination and clarification; '
                'discarding destination and requesting clarification without moving.')
            return {**data, 'destination': None}
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

    def validate_response(self, data):
        return _validate_entity_interactions(
            data, *MANAGER_INTERACTIONS['npcs'], self.name,
        )


class NarratorCall(LLMCall):
    name = 'game_narrator'

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
    temperature = 0

    @staticmethod
    def validate_response(data):
        if isinstance(data, dict) and set(data) == {'outcome', 'entity_id', 'candidate_ids'}:
            if data['outcome'] == 'existing' and data['candidate_ids'] == []:
                data = {'outcome': 'existing', 'entity_id': data['entity_id']}
            elif data['outcome'] == 'new' and data['entity_id'] is None and data['candidate_ids'] == []:
                data = {'outcome': 'new'}
            elif data['outcome'] == 'ambiguous' and data['entity_id'] is None:
                data = {'outcome': 'ambiguous', 'candidate_ids': data['candidate_ids']}
        valid = False
        if isinstance(data, dict):
            outcome = data.get('outcome')
            if outcome == 'new':
                valid = set(data) == {'outcome'}
            elif outcome == 'existing':
                valid = (set(data) == {'outcome', 'entity_id'}
                         and isinstance(data['entity_id'], str)
                         and bool(data['entity_id'].strip()))
            elif outcome == 'ambiguous':
                ids = data.get('candidate_ids')
                valid = (set(data) == {'outcome', 'candidate_ids'}
                         and isinstance(ids, list) and len(ids) >= 2
                         and all(isinstance(value, str) and value.strip() for value in ids)
                         and len(set(ids)) == len(ids))
        if not valid:
            raise LLMResponseError(
                'comparator: response must be existing with entity_id, new, '
                'or ambiguous with at least two distinct candidate_ids.',
                details=data)
        return data


class HistorySummarizerCall(LLMCall):
    name = 'history_summarizer'

    @staticmethod
    def validate_response(data):
        if (not isinstance(data, dict) or set(data) != {'summary'}
                or not isinstance(data['summary'], str) or not data['summary'].strip()
                or len(data['summary']) > 24000):
            raise LLMResponseError(
                'history_summarizer: response must contain only a non-empty summary '
                'of at most 24000 characters.', details=data)
        return data


class FreeformNarratorCall(LLMCall):
    name = 'freeform_narrator'

    @staticmethod
    def validate_response(data):
        if not isinstance(data, str) or not data.strip():
            raise LLMResponseError(
                'freeform_narrator: narration must be non-empty text.', details=data)
        return data.strip()


class DirectNarratorCall(LLMCall):
    """One-pass narration that also returns the complete updated world."""
    name = 'direct_narrator'

    @staticmethod
    def validate_response(data):
        required = {'narration', 'action_succeeded', 'game_over', 'currentLocationId',
                    'locations', 'items', 'objects', 'npcs'}
        if not isinstance(data, dict) or set(data) != required:
            raise LLMResponseError(
                f'direct_narrator: response must contain exactly {", ".join(sorted(required))}.',
                details=data)
        if not isinstance(data['narration'], str) or not data['narration'].strip():
            raise LLMResponseError('direct_narrator: narration must be non-empty text.', details=data)
        if not isinstance(data['action_succeeded'], bool) or not isinstance(data['game_over'], bool):
            raise LLMResponseError(
                'direct_narrator: action_succeeded and game_over must be booleans.', details=data)
        if not isinstance(data['currentLocationId'], str):
            raise LLMResponseError('direct_narrator: currentLocationId must be a string.', details=data)
        for collection in ('locations', 'items', 'objects', 'npcs'):
            entries = data[collection]
            if not isinstance(entries, list) or not all(
                    isinstance(entry, dict)
                    and isinstance(entry.get('id'), str) and entry['id'].strip()
                    and isinstance(entry.get('name'), str) and entry['name'].strip()
                    and isinstance(entry.get('description'), str)
                    for entry in entries):
                raise LLMResponseError(
                    f'direct_narrator: every {collection} entry needs id, name, and description.',
                    details=data)
        return data
