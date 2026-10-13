"""Deterministic game state and LLM-assisted turn orchestration."""
import copy
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from backend.llm_calls.gameplay import (
        InventoryManagerCall,
        MapManagerCall,
        NPCManagerCall,
        NarratorCall,
        SummarizerCall,
    )
    from backend.llm_calls.base import LLMResponseError
except ImportError:
    from llm_calls.gameplay import (
        InventoryManagerCall,
        MapManagerCall,
        NPCManagerCall,
        NarratorCall,
        SummarizerCall,
    )
    from llm_calls.base import LLMResponseError


START_LOCATION_ID = 'town-square-outside-tavern'
RECENT_EVENT_COUNT = 16
SUMMARIZE_AFTER_EVENTS = 24
MAX_STATE_CHANGES = 20
logger = logging.getLogger(__name__)


class InvalidProposal(ValueError):
    """Raised when an LLM proposal cannot be applied safely."""


def new_game(player_id):
    return {
        'id': str(uuid.uuid4()),
        'playerId': str(player_id),
        'currentLocationId': START_LOCATION_ID,
        'status': 'active',
        'endReason': None,
        'world': {
            'locations': [{
                'id': START_LOCATION_ID,
                'name': 'Town Square Outside the Tavern',
                'aliases': ['town square'],
                'visited': True,
                'exits': [],
            }],
            'items': [],
            'objects': [],
            'npcs': [],
        },
        'history': {
            'summary': '',
            'recentEvents': [{
                'kind': 'narration',
                'content': 'You stand in the town square outside the tavern. The tavern door is nearby, and the square is open around you.',
            }],
        },
    }


def _required_string(value, field, maximum=255):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise InvalidProposal(f'{field} must be a non-empty string of at most {maximum} characters.')
    return value.strip()


def _aliases(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise InvalidProposal('aliases must be a list of at most 20 strings.')
    return [_required_string(alias, 'alias', 100) for alias in value]


def _ensure_unique_identity(entities, name, aliases, label):
    proposed = {value.casefold() for value in [name] + aliases}
    for entity in entities:
        existing = {entity['name'].casefold()}
        existing.update(alias.casefold() for alias in entity.get('aliases', []))
        if proposed.intersection(existing):
            raise InvalidProposal(f'{label} {name!r} duplicates an existing name or alias.')


def _new_id(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40] or 'entity'
    return f'{slug}-{uuid.uuid4().hex[:8]}'


def _find_entity(entities, reference, label):
    if not isinstance(reference, str) or not reference.strip():
        raise InvalidProposal(f'{label} reference is required.')
    reference = reference.strip()
    by_id = [entity for entity in entities if entity['id'] == reference]
    if by_id:
        return by_id[0]
    matches = [
        entity for entity in entities
        if reference.casefold() == entity['name'].casefold()
        or reference.casefold() in [alias.casefold() for alias in entity.get('aliases', [])]
    ]
    if len(matches) != 1:
        reason = 'ambiguous' if matches else 'unknown'
        raise InvalidProposal(f'{label} reference {reference!r} is {reason}.')
    return matches[0]


def _holder_id(state, holder_type, reference):
    if holder_type == 'player':
        return state['playerId']
    if holder_type == 'location':
        return _find_entity(state['world']['locations'], reference, 'location')['id']
    if holder_type == 'npc':
        return _find_entity(state['world']['npcs'], reference, 'NPC')['id']
    if holder_type == 'object':
        return _find_entity(state['world']['objects'], reference, 'object')['id']
    raise InvalidProposal('holder_type must be player, location, npc, or object.')


def _ensure_holder_available_here(state, holder):
    holder_type = holder['type']
    holder_id = holder['id']
    if holder_type == 'location':
        if holder_id != state['currentLocationId']:
            raise InvalidProposal('An item can only be transferred to or from the current location.')
    elif holder_type == 'npc':
        npc = next(entity for entity in state['world']['npcs'] if entity['id'] == holder_id)
        if npc['status'] != 'active' or npc['currentLocationId'] != state['currentLocationId']:
            raise InvalidProposal('An item can only be transferred to or from an NPC here.')
    elif holder_type == 'object':
        obj = next(entity for entity in state['world']['objects'] if entity['id'] == holder_id)
        if obj['status'] != 'present' or obj['locationId'] != state['currentLocationId']:
            raise InvalidProposal('An item can only be transferred to or from an object here.')


def _resolve_location(state, reference):
    return _find_entity(state['world']['locations'], reference, 'location')


def _apply_operation(state, operation):
    if not isinstance(operation, dict) or set(operation) != {'type', 'payload'}:
        raise InvalidProposal('Each state change must contain only type and payload.')
    kind = _required_string(operation['type'], 'state change type', 40)
    try:
        payload = json.loads(operation['payload']) if isinstance(operation['payload'], str) else operation['payload']
    except (json.JSONDecodeError, TypeError) as error:
        raise InvalidProposal(f'{kind} payload must be valid JSON.') from error
    if not isinstance(payload, dict):
        raise InvalidProposal(f'{kind} payload must be an object.')

    world = state['world']
    if kind == 'create_location':
        name = _required_string(payload.get('name'), 'location name', 100)
        aliases = _aliases(payload.get('aliases'))
        _ensure_unique_identity(world['locations'], name, aliases, 'Location')
        location = {
            'id': _new_id(name),
            'name': name,
            'aliases': aliases,
            'visited': False,
            'exits': [],
        }
        world['locations'].append(location)
        return

    if kind == 'add_exit':
        origin_ref = payload.get('from_location_id', 'current')
        origin = _resolve_location(state, state['currentLocationId'] if origin_ref == 'current' else origin_ref)
        destination_ref = payload.get('destination_location_id')
        destination = _resolve_location(state, destination_ref) if destination_ref else None
        description = _required_string(payload.get('description'), 'exit description', 200)
        traversable = payload.get('traversable', True)
        if not isinstance(traversable, bool):
            raise InvalidProposal('traversable must be a boolean.')
        if destination and any(exit_['destinationId'] == destination['id'] for exit_ in origin['exits']):
            return
        if destination is None and any(
                exit_['destinationId'] is None
                and exit_['description'].strip().casefold() == description.casefold()
                for exit_ in origin['exits']):
            return
        origin['exits'].append({
            'id': str(uuid.uuid4()),
            'destinationId': destination['id'] if destination else None,
            'description': description,
            'traversable': traversable,
            'identified': True,
        })
        return

    if kind == 'move_player':
        destination = _resolve_location(state, payload.get('destination_location_id'))
        current = _resolve_location(state, state['currentLocationId'])
        exit_ = next((candidate for candidate in current['exits']
                      if candidate['destinationId'] == destination['id']), None)
        if exit_ is None or not exit_['traversable']:
            raise InvalidProposal('Player movement must use an established, traversable exit.')
        state['currentLocationId'] = destination['id']
        destination['visited'] = True
        return

    if kind == 'set_exit_traversable':
        location = _resolve_location(state, payload.get('location_id', 'current')
                                     if payload.get('location_id', 'current') != 'current'
                                     else state['currentLocationId'])
        exit_id = payload.get('exit_id')
        destination_ref = payload.get('destination_location_id')
        exit_ = next((candidate for candidate in location['exits']
                      if candidate['id'] == exit_id), None) if exit_id else None
        if exit_ is None and destination_ref:
            destination = _resolve_location(state, destination_ref)
            exit_ = next((candidate for candidate in location['exits']
                          if candidate['destinationId'] == destination['id']), None)
        if exit_ is None or not isinstance(payload.get('traversable'), bool):
            raise InvalidProposal('Exit update needs an existing exit and boolean traversable value.')
        exit_['traversable'] = payload['traversable']
        return

    if kind == 'create_item':
        name = _required_string(payload.get('name'), 'item name', 100)
        aliases = _aliases(payload.get('aliases'))
        _ensure_unique_identity(world['items'], name, aliases, 'Item')
        holder_type = payload.get('holder_type')
        known_by_player = payload.get('known_by_player', True)
        if not isinstance(known_by_player, bool) or (holder_type == 'player' and not known_by_player):
            raise InvalidProposal('An item held by the player must be known to the player.')
        item = {
            'id': _new_id(name),
            'name': name,
            'aliases': aliases,
            'knownByPlayer': known_by_player,
            'holder': {
                'type': holder_type,
                'id': _holder_id(state, holder_type, payload.get('holder_id')),
            },
            'status': 'available',
        }
        world['items'].append(item)
        return

    if kind == 'transfer_item':
        item = _find_entity(world['items'], payload.get('item_id'), 'item')
        if item['status'] != 'available':
            raise InvalidProposal('Only available items can be transferred.')
        holder_type = payload.get('holder_type')
        new_holder = {
            'type': holder_type,
            'id': _holder_id(state, holder_type, payload.get('holder_id')),
        }
        if item['holder']['type'] != 'player' or holder_type != 'player':
            _ensure_holder_available_here(state, item['holder'])
            _ensure_holder_available_here(state, new_holder)
        item['holder'] = new_holder
        return

    if kind == 'set_item_status':
        item = _find_entity(world['items'], payload.get('item_id'), 'item')
        status = payload.get('status')
        if item['status'] != 'available' or status not in ('consumed', 'destroyed'):
            raise InvalidProposal('Item status must transition from available to consumed or destroyed.')
        item['status'] = status
        item['holder'] = None
        return

    if kind == 'create_object':
        name = _required_string(payload.get('name'), 'object name', 100)
        aliases = _aliases(payload.get('aliases'))
        _ensure_unique_identity(world['objects'], name, aliases, 'Object')
        container_ref = payload.get('contained_in_object_id')
        container = _find_entity(world['objects'], container_ref, 'container') if container_ref else None
        location_ref = payload.get('location_id', 'current')
        location = _resolve_location(
            state,
            location_ref if location_ref != 'current' else state['currentLocationId'],
        )
        if container and container['locationId'] != location['id']:
            raise InvalidProposal('A contained object must share its container location.')
        object_state = payload.get('state', {})
        if not isinstance(object_state, dict):
            raise InvalidProposal('object state must be an object.')
        world['objects'].append({
            'id': _new_id(name),
            'name': name,
            'aliases': aliases,
            'locationId': location['id'],
            'containedInObjectId': container['id'] if container else None,
            'knownByPlayer': payload.get('known_by_player', True) is True,
            'state': object_state,
            'status': 'present',
        })
        return

    if kind == 'update_object':
        obj = _find_entity(world['objects'], payload.get('object_id'), 'object')
        new_state = payload.get('state', {})
        if not isinstance(new_state, dict):
            raise InvalidProposal('object state must be an object.')
        obj['state'].update(new_state)
        if 'location_id' in payload:
            obj['locationId'] = _resolve_location(state, payload['location_id'])['id']
            obj['containedInObjectId'] = None
        if 'status' in payload:
            if payload['status'] not in ('present', 'destroyed'):
                raise InvalidProposal('Object status must be present or destroyed.')
            obj['status'] = payload['status']
        return

    if kind == 'create_npc':
        name = _required_string(payload.get('name'), 'NPC name', 100)
        aliases = _aliases(payload.get('aliases'))
        _ensure_unique_identity(world['npcs'], name, aliases, 'NPC')
        location = _resolve_location(state, payload.get('current_location_id', 'current')
                                     if payload.get('current_location_id', 'current') != 'current'
                                     else state['currentLocationId'])
        npc_state = payload.get('state', {})
        if not isinstance(npc_state, dict):
            raise InvalidProposal('NPC state must be an object.')
        world['npcs'].append({
            'id': _new_id(name),
            'name': name,
            'aliases': aliases,
            'currentLocationId': location['id'],
            'status': 'active',
            'state': npc_state,
        })
        return

    if kind == 'update_npc':
        npc = _find_entity(world['npcs'], payload.get('npc_id'), 'NPC')
        status = payload.get('status', npc['status'])
        if status not in ('active', 'offstage', 'dead', 'removed'):
            raise InvalidProposal('NPC status must be active, offstage, dead, or removed.')
        if status != 'active':
            npc['currentLocationId'] = None
        elif 'current_location_id' in payload:
            npc['currentLocationId'] = _resolve_location(state, payload['current_location_id'])['id']
        if status == 'active' and not npc['currentLocationId']:
            raise InvalidProposal('An active NPC must have a location.')
        npc['status'] = status
        npc_state = payload.get('state', {})
        if not isinstance(npc_state, dict):
            raise InvalidProposal('NPC state must be an object.')
        npc['state'].update(npc_state)
        return

    raise InvalidProposal(f'Unsupported state change type: {kind}.')


def apply_state_changes(state, operations):
    if not isinstance(operations, list) or len(operations) > MAX_STATE_CHANGES:
        raise InvalidProposal(f'state_changes must be a list of at most {MAX_STATE_CHANGES} operations.')
    updated = copy.deepcopy(state)
    for index, operation in enumerate(operations):
        try:
            _apply_operation(updated, operation)
        except InvalidProposal as error:
            logger.warning(
                'Rejected state change %s for game %s: %s; operation=%r',
                index + 1, state['id'], error, operation,
            )
            raise
    return updated


def _context(state, action):
    current = next(location for location in state['world']['locations']
                   if location['id'] == state['currentLocationId'])
    return {
        'action': action,
        'current_location': current,
        'world': state['world'],
        'inventory': [item for item in state['world']['items']
                      if item['status'] == 'available'
                      and item['holder'] == {'type': 'player', 'id': state['playerId']}],
        'narrative_summary': state['history']['summary'],
        'recent_events': state['history']['recentEvents'],
    }


def _run_logged(name, call, context):
    encoded_context = json.dumps(context, ensure_ascii=False)
    logger.info('Invoking LLM component %s with context: %s', name, encoded_context[:4000])
    result = call.run(encoded_context)
    encoded_output = json.dumps(result.data, ensure_ascii=False)
    logger.info(
        'LLM component %s used %s/%s and returned: %s',
        name, result.provider, result.model, encoded_output[:4000],
    )
    return result


def process_turn(state, action, calls=None, settings=None, on_progress=None):
    """Resolve a turn without mutating the stored state until every LLM step succeeds."""
    action = _required_string(action, 'action', 2000)
    calls = calls or {
        'inventory': InventoryManagerCall(settings=settings),
        'map': MapManagerCall(settings=settings),
        'npcs': NPCManagerCall(settings=settings),
        'narrator': NarratorCall(settings=settings),
        'summarizer': SummarizerCall(settings=settings),
    }
    context = _context(state, action)
    current_location = context['current_location']
    manager_contexts = {
        'inventory': {
            'action': action,
            'current_location': current_location,
            'inventory': context['inventory'],
            'known_items': [
                item for item in state['world']['items'] if item['knownByPlayer']
            ],
            'objects_here': [
                obj for obj in state['world']['objects']
                if obj['locationId'] == current_location['id'] and obj['status'] == 'present'
            ],
            'narrative_summary': state['history']['summary'],
            'recent_events': state['history']['recentEvents'],
        },
        'map': {
            'action': action,
            'current_location': current_location,
            'locations': state['world']['locations'],
            'narrative_summary': state['history']['summary'],
            'recent_events': state['history']['recentEvents'],
        },
        'npcs': {
            'action': action,
            'current_location': current_location,
            'known_npcs': state['world']['npcs'],
            'narrative_summary': state['history']['summary'],
            'recent_events': state['history']['recentEvents'],
        },
    }
    logger.info('Player action for game %s: %s', state['id'], action)
    def progress(component, status, **details):
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    for key in ('inventory', 'map', 'npcs'):
        progress(key, 'started')
    with ThreadPoolExecutor(max_workers=3) as executor:
        manager_futures = {
            key: executor.submit(
                _run_logged, key, calls[key], manager_contexts[key])
            for key in ('inventory', 'map', 'npcs')
        }
        names = {future: key for key, future in manager_futures.items()}
        manager_results = {}
        for future in as_completed(names):
            key = names[future]
            result = future.result()
            manager_results[key] = result.data['assessment']
            progress(key, 'completed', response=result.data,
                     provider=result.provider, model=result.model)

    narrator_input = {
        'action': action,
        'authoritative_state': context,
        'manager_assessments': manager_results,
    }
    for attempt in range(2):
        response = None
        try:
            progress('narrator', 'started', attempt=attempt + 1)
            result = _run_logged('narrator', calls['narrator'], narrator_input)
            response = result.data
            progress('narrator', 'completed', response=response,
                     provider=result.provider, model=result.model)
            narration = _required_string(response.get('narration'), 'narration', 10000)
            rejected = narrator_input.get('rejected_response')
            if (attempt and isinstance(rejected, dict)
                    and rejected.get('state_changes')
                    and response.get('state_changes') == []
                    and response.get('action_succeeded') is True
                    and narration == rejected.get('narration')):
                raise InvalidProposal(
                    'Narrator correction discarded all state changes while keeping '
                    'the same successful narration. Return the corrected operations '
                    'or revise the narration to reflect an outcome without those changes.')
            candidate = apply_state_changes(state, response.get('state_changes'))
            break
        except (LLMResponseError, InvalidProposal) as error:
            if attempt == 1:
                raise
            logger.warning('Retrying narrator for game %s after validation failure: %s', state['id'], error)
            progress('narrator', 'retrying', error=str(error))
            narrator_input['validation_error'] = str(error)
            narrator_input['rejected_response'] = (
                response if response is not None else error.details
            )
    logger.info('Accepted state changes for game %s: %s', state['id'], response['state_changes'])
    if response.get('game_over') is True and candidate['status'] != 'ended':
        candidate['status'] = 'ended'
        candidate['endReason'] = 'character_died'
    if candidate['status'] == 'ended' and not response.get('game_over'):
        raise InvalidProposal('The narrator must set game_over when ending the game.')

    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': narration},
    ])
    if len(candidate['history']['recentEvents']) > SUMMARIZE_AFTER_EVENTS:
        old_events = candidate['history']['recentEvents'][:-RECENT_EVENT_COUNT]
        summary_input = {
            'existing_summary': candidate['history']['summary'],
            'events_to_compact': old_events,
        }
        progress('summarizer', 'started')
        summary_result = _run_logged('summarizer', calls['summarizer'], summary_input)
        progress('summarizer', 'completed', response=summary_result.data,
                 provider=summary_result.provider, model=summary_result.model)
        candidate['history']['summary'] = _required_string(
            summary_result.data.get('summary'), 'summary', 12000)
        candidate['history']['recentEvents'] = candidate['history']['recentEvents'][-RECENT_EVENT_COUNT:]

    logger.info('Final narration for game %s: %s', state['id'], narration[:4000])
    return candidate, {
        'narration': narration,
        'action_succeeded': response.get('action_succeeded', True) is True,
        'provider': result.provider,
        'model': result.model,
    }
