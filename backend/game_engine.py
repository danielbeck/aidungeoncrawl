"""Deterministic game state and LLM-assisted turn orchestration."""
import copy
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from backend.llm_calls.gameplay import (
        ComparatorCall,
        InventoryManagerCall,
        MapManagerCall,
        NPCManagerCall,
        NarratorCall,
    )
except ImportError:
    from llm_calls.gameplay import (
        ComparatorCall,
        InventoryManagerCall,
        MapManagerCall,
        NPCManagerCall,
        NarratorCall,
    )


START_LOCATION_ID = 'town-square-outside-tavern'
MANAGER_HISTORY_EVENTS = 2
MANAGER_HISTORY_CONTENT_LIMIT = 500
logger = logging.getLogger(__name__)


class InvalidTurnResponse(ValueError):
    """Raised when an LLM response cannot be reconciled safely."""


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
                'description': 'The town square outside the tavern.',
                'aliases': ['town square'],
                'visited': True,
                'exits': [],
            }],
            'items': [],
            'objects': [],
            'npcs': [],
        },
        'history': {
            'recentEvents': [{
                'kind': 'narration',
                'content': 'You stand in the town square outside the tavern. The tavern door is nearby, and the square is open around you.',
            }],
        },
    }


def _required_string(value, field, maximum=255):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise InvalidTurnResponse(f'{field} must be a non-empty string of at most {maximum} characters.')
    return value.strip()


def _new_id(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40] or 'entity'
    return f'{slug}-{uuid.uuid4().hex[:8]}'


def _display_entity(entity):
    return {
        'name': entity['name'],
        'description': entity.get('description', ''),
    }


def _context(state, action):
    locations = state['world']['locations']
    current = next(location for location in locations
                   if location['id'] == state['currentLocationId'])
    location_by_id = {location['id']: location for location in locations}
    exits = [{
        'description': exit_['description'],
        'destination': _display_entity(location_by_id[exit_['destinationId']])
        if exit_.get('destinationId') in location_by_id else None,
    } for exit_ in current.get('exits', [])]
    inventory = [
        _display_entity(item) for item in state['world']['items']
        if item.get('status') == 'available'
        and item.get('holder') == {'type': 'player', 'id': state['playerId']}
    ]
    npcs_here = [
        _display_entity(npc) for npc in state['world']['npcs']
        if npc.get('status') == 'active'
        and npc.get('currentLocationId') == current['id']
    ]
    npc_ids_here = {
        npc['id'] for npc in state['world']['npcs']
        if npc.get('status') == 'active' and npc.get('currentLocationId') == current['id']
    }
    items_here = [
        _display_entity(item) for item in state['world']['items']
        if item.get('status') == 'available'
        and (
            item.get('holder') == {'type': 'location', 'id': current['id']}
            or (isinstance(item.get('holder'), dict)
                and item['holder'].get('type') == 'npc'
                and item['holder'].get('id') in npc_ids_here)
        )
    ]
    objects_here = [
        _display_entity(object_) for object_ in state['world'].get('objects', [])
        if object_.get('status') == 'present'
        and object_.get('knownByPlayer')
        and object_.get('locationId') == current['id']
        and not object_.get('containedInObjectId')
    ]
    return {
        'action': action,
        'current_location': _display_entity(current),
        'exits': exits,
        'carried_items': inventory,
        'npcs_here': npcs_here,
        'history': [
            {'kind': event['kind'], 'content': event['content']}
            for event in state['history'].get('recentEvents', [])
        ],
        'recent_history': [
            {
                'kind': event['kind'],
                'content': event['content'][:MANAGER_HISTORY_CONTENT_LIMIT],
            }
            for event in state['history'].get('recentEvents', [])[-MANAGER_HISTORY_EVENTS:]
        ],
        'items_here': items_here,
        'objects_here': objects_here,
        'known_items': [
            _display_entity(item) for item in state['world']['items']
            if item.get('status') == 'available'
        ],
        'known_locations': [_display_entity(location) for location in locations],
        'known_npcs': [_display_entity(npc) for npc in state['world']['npcs']],
    }


def _run_logged(name, call, context):
    encoded_context = json.dumps(context, ensure_ascii=False)
    logger.info('Invoking LLM component %s with context: %s', name, encoded_context[:4000])
    result = call.run(encoded_context)
    logger.info(
        'LLM component %s used %s/%s and returned: %s',
        name, result.provider, result.model,
        json.dumps(result.data, ensure_ascii=False)[:4000],
    )
    return result


def _reconcile_collection(state, collection_name, notable, comparator, current_location_id):
    world = state['world']
    entities = world[collection_name]
    result_entities = []
    for proposed in notable:
        comparator_input = {
            'entity_type': collection_name,
            'existing_entities': [_display_entity(entity) for entity in entities],
            'proposed_entity': proposed,
        }
        result = _run_logged('comparator', comparator, comparator_input)
        if (not isinstance(result.data, dict)
                or set(result.data) != {'already_present'}
                or not isinstance(result.data['already_present'], bool)):
            raise InvalidTurnResponse('Comparator already_present must be a boolean.')
        present = result.data['already_present']
        if present:
            continue

        name = proposed['name']
        entity = {
            'id': _new_id(name),
            'name': name,
            'description': proposed['description'],
            'aliases': [],
        }
        if collection_name == 'locations':
            entity.update({'visited': False, 'exits': []})
        elif collection_name == 'items':
            entity.update({
                'knownByPlayer': True,
                'holder': {'type': 'player', 'id': state['playerId']},
                'status': 'available',
            })
        else:
            entity.update({
                'currentLocationId': current_location_id,
                'status': 'active',
                'state': {},
            })
        entities.append(entity)
        result_entities.append(entity)
    return result_entities


def process_turn(state, action, calls=None, settings=None, on_progress=None):
    """Resolve a turn without mutating the stored state before all calls succeed."""
    action = _required_string(action, 'action', 2000)
    calls = calls or {
        'inventory': InventoryManagerCall(settings=settings),
        'map': MapManagerCall(settings=settings),
        'npcs': NPCManagerCall(settings=settings),
        'narrator': NarratorCall(settings=settings),
        'comparator': ComparatorCall(settings=settings),
    }
    context = _context(state, action)

    def progress(component, status, **details):
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    manager_contexts = {
        'inventory': {
            **{key: context[key] for key in (
                'action', 'current_location', 'carried_items', 'items_here',
                'objects_here', 'recent_history',
            )},
        },
        'map': {
            **{key: context[key] for key in (
                'action', 'current_location', 'exits', 'known_locations', 'recent_history',
            )},
        },
        'npcs': {
            **{key: context[key] for key in (
                'action', 'current_location', 'npcs_here', 'recent_history',
            )},
        },
    }
    logger.info('Player action for game %s: %s', state['id'], action)
    for key in ('inventory', 'map', 'npcs'):
        progress(key, 'started')
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_run_logged, key, calls[key], manager_contexts[key]): key
            for key in ('inventory', 'map', 'npcs')
        }
        manager_results = {}
        for future in as_completed(futures):
            key = futures[future]
            result = future.result()
            manager_results[key] = result.data
            progress(key, 'completed', response=result.data,
                     provider=result.provider, model=result.model)

    narrator_input = {
        'game_context': context,
        'manager_results': manager_results,
    }
    progress('narrator', 'started')
    narrator_result = _run_logged('narrator', calls['narrator'], narrator_input)
    response = narrator_result.data
    progress('narrator', 'completed', response=response,
             provider=narrator_result.provider, model=narrator_result.model)

    candidate = copy.deepcopy(state)
    candidate['history'].pop('summary', None)
    current_location_id = state['currentLocationId']
    notable = response['notable_elements']
    created = {}
    for category, collection_name in (
        ('items', 'items'),
        ('locations', 'locations'),
        ('npcs', 'npcs'),
    ):
        created[category] = _reconcile_collection(
            candidate, collection_name, notable[category],
            calls['comparator'], current_location_id,
        )

    if response['game_over']:
        candidate['status'] = 'ended'
        candidate['endReason'] = 'character_died'
    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': response['narration']},
    ])
    logger.info(
        'Reconciled notable elements for game %s: %s',
        state['id'], {key: [entity['name'] for entity in values]
                      for key, values in created.items()},
    )
    return candidate, {
        'narration': response['narration'],
        'action_succeeded': response['action_succeeded'],
        'game_over': response['game_over'],
        'manager_results': manager_results,
        'provider': narrator_result.provider,
        'model': narrator_result.model,
    }
