"""Deterministic game state and LLM-assisted turn orchestration."""
import copy
import json
import logging
import re
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from backend.llm_provider import LLMError
    from backend.llm_calls.gameplay import (
        ComparatorCall,
        InventoryManagerCall,
        MapManagerCall,
        NPCManagerCall,
        NarratorCall,
    )
except ImportError:
    from llm_provider import LLMError
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


def _normalized_text(value):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))


def _context(state):
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
        'current_location': _display_entity(current),
        'exits': exits,
        'carried_items': inventory,
        'npcs_here': npcs_here,
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


def _resolve_entity(state, collection_name, proposed, comparator, current_location_id,
                    create_if_missing=True, on_progress=None, candidates=None):
    entities = state['world'][collection_name]
    candidates = entities if candidates is None else candidates
    def progress(status, llm=True, **details):
        if on_progress is not None:
            on_progress('comparator', status, llm=llm, **details)

    if candidates:
        name = _normalized_text(proposed['name'])
        name_matches = [
            entity for entity in candidates
            if name and _normalized_text(entity['name']) == name
        ]
        exact_matches = [
            entity for entity in candidates
            if all(_normalized_text(entity.get(field, ''))
                   == _normalized_text(proposed[field])
                   for field in ('name', 'description'))
        ]
        if len(exact_matches) > 1:
            error = f'Ambiguous {collection_name} identity: multiple exact matches.'
            progress('failed', llm=False, error=error, proposed_entity=_display_entity(proposed))
            raise InvalidTurnResponse(error)
        text_matches = exact_matches or (name_matches if len(name_matches) == 1 else [])
        if len(text_matches) == 1:
            entity = text_matches[0]
            reason = ('Unique normalized name and description match; no LLM call needed.'
                      if exact_matches else 'Unique normalized name match; no LLM call needed.')
            logger.info('Resolved text %s match without an LLM call.', collection_name)
            comparator_input = {
                'entity_type': collection_name,
                'candidate_entity': _display_entity(entity),
                'proposed_entity': _display_entity(proposed),
            }
            progress('completed', llm=False, request=comparator_input,
                     reason=reason,
                     response={'same_entity': True},
                     matched_entity=_display_entity(entity))
            return entity
        matches = []
        for entity in candidates:
            comparator_input = {
                'entity_type': collection_name,
                'candidate_entity': _display_entity(entity),
                'proposed_entity': _display_entity(proposed),
            }
            progress('started', request=comparator_input)
            try:
                result = _run_logged('comparator', comparator, comparator_input)
            except LLMError as error:
                progress('failed', request=comparator_input, error=str(error),
                         response=getattr(error, 'details', None))
                raise
            progress('received', response=result.data, provider=result.provider, model=result.model)
            if (not isinstance(result.data, dict) or set(result.data) != {'same_entity'}
                    or not isinstance(result.data['same_entity'], bool)):
                error = 'Comparator must return boolean same_entity only.'
                progress('failed', request=comparator_input, response=result.data, error=error)
                raise InvalidTurnResponse(error)
            details = {'matched_entity': _display_entity(entity)} if result.data['same_entity'] else {}
            progress('completed', response=result.data, **details)
            if result.data['same_entity']:
                matches.append(entity)
        if len(matches) > 1:
            error = f'Ambiguous {collection_name} identity: multiple candidates matched.'
            progress('failed', error=error, proposed_entity=_display_entity(proposed))
            raise InvalidTurnResponse(error)
        if matches:
            return matches[0]
    else:
        progress('skipped', llm=False, entity_type=collection_name,
                 proposed_entity=_display_entity(proposed),
                 reason='No eligible existing entities to compare.')
    if not create_if_missing:
        return None

    entity = {
        'id': _new_id(proposed['name']),
        'name': proposed['name'],
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
    return entity


def _reconcile_collection(state, collection_name, notable, comparator, current_location_id,
                          on_progress=None):
    resolved_entities = []
    for proposed in notable:
        entity = _resolve_entity(
            state, collection_name, proposed, comparator, current_location_id,
            on_progress=on_progress)
        resolved_entities.append({
            'id': entity['id'],
            'name': entity['name'],
            'description': entity.get('description', ''),
        })
    return resolved_entities


def _add_exit(origin, destination):
    exits = origin.setdefault('exits', [])
    if any(exit_.get('destinationId') == destination['id'] for exit_ in exits):
        return
    exits.append({
        'id': _new_id('exit'),
        'destinationId': destination['id'],
        'description': f"To {destination['name']}",
        'traversable': True,
        'identified': True,
    })


def _apply_map_interactions(state, manager_result, comparator, on_progress=None):
    if not isinstance(manager_result, dict) or set(manager_result) != {'locations'}:
        raise InvalidTurnResponse('Map manager must return only a locations list.')
    locations = manager_result['locations']
    if not isinstance(locations, list):
        raise InvalidTurnResponse('Map manager locations must be a list.')
    for entry in locations:
        if (not isinstance(entry, dict)
                or set(entry) != {'name', 'description', 'interaction'}
                or not isinstance(entry.get('name'), str) or not entry['name'].strip()
                or not isinstance(entry.get('description'), str) or not entry['description'].strip()
                or entry.get('interaction') not in ('enter', 'leave')):
            raise InvalidTurnResponse('Map manager returned an invalid location interaction.')

    entering = [entry for entry in locations if entry['interaction'] == 'enter']
    leaving = [entry for entry in locations if entry['interaction'] == 'leave']
    if not entering and not leaving:
        return []

    current_id = state['currentLocationId']
    current = next(location for location in state['world']['locations']
                   if location['id'] == current_id)
    if len(entering) > 1:
        redundant = [
            entry for entry in entering
            if all(_normalized_text(entry[field]) == _normalized_text(current[field])
                   for field in ('name', 'description'))
        ]
        if redundant:
            locations = [entry for entry in locations
                         if not (entry['interaction'] == 'enter' and entry in redundant)]
            manager_result['locations'] = locations
            entering = [entry for entry in locations if entry['interaction'] == 'enter']
            reason = 'Removed redundant enter entries for the current location.'
            logger.warning('%s Game %s.', reason, state['id'])
            if on_progress is not None:
                on_progress('map', 'corrected', reason=reason, response=manager_result)
    if len(entering) != 1 or len(leaving) != 1:
        raise InvalidTurnResponse(
            'Map movement requires exactly one entering and one leaving location.')

    origin = _resolve_entity(
        state, 'locations', leaving[0], comparator, current_id, create_if_missing=False,
        on_progress=on_progress)
    if origin is None or origin['id'] != current_id:
        reason = 'Map departure does not match the current location; no movement was applied.'
        logger.warning('%s Game %s.', reason, state['id'])
        if on_progress is not None:
            on_progress('map', 'ignored', response=copy.deepcopy(locations), reason=reason)
        manager_result['locations'] = []
        return []
    if _normalized_text(entering[0]['name']) == _normalized_text(current['name']):
        destination = current
    else:
        destination = _resolve_entity(
            state, 'locations', entering[0], comparator, current_id, on_progress=on_progress,
            candidates=[location for location in state['world']['locations']
                        if location['id'] != origin['id']])
    if destination['id'] == current_id:
        return [{
            'id': destination['id'],
            'name': destination['name'],
            'description': destination.get('description', ''),
        }]

    _add_exit(current, destination)
    _add_exit(destination, current)
    destination['visited'] = True
    state['currentLocationId'] = destination['id']
    return [{
        'id': origin['id'],
        'name': origin['name'],
        'description': origin.get('description', ''),
    }, {
        'id': destination['id'],
        'name': destination['name'],
        'description': destination.get('description', ''),
    }]


def _explicit_inventory_intent(action):
    match = re.fullmatch(
        r'\s*(?:please\s+)?(?:(?:I\s+)?(?:want\s+to\s+)?)'
        r'(pick\s+up|take|collect|drop|put\s+down|leave\s+behind)\s+'
        r'([\w\s\'-]+?)[.!]?\s*', action, flags=re.IGNORECASE)
    if match is None:
        return None
    target = _normalized_text(match.group(2))
    if (not target or any(word in target.split() for word in (
            'and', 'or', 'then', 'if', 'unless', 'not', 'never', 'it', 'them',
            'everything', 'all', 'from', 'to', 'on', 'in', 'with'))):
        return None
    target = re.sub(r'^(?:the|a|an|my)\s+', '', target)
    operation = 'drop' if _normalized_text(match.group(1)) in (
        'drop', 'put down', 'leave behind') else 'take'
    return operation, target


def _apply_inventory_interactions(state, manager_result, comparator, current_location_id,
                                  on_progress=None, action=''):
    if not isinstance(manager_result, dict) or set(manager_result) != {'items'}:
        raise InvalidTurnResponse('Inventory manager must return only an items list.')
    items = manager_result['items']
    if not isinstance(items, list) or len(items) > 20:
        raise InvalidTurnResponse('Inventory manager items must be a list.')

    current_npc_ids = {
        npc['id'] for npc in state['world']['npcs']
        if npc.get('status') == 'active' and npc.get('currentLocationId') == current_location_id
    }
    resolved = []
    applied = []
    explicit_intent = _explicit_inventory_intent(action)
    for proposed in items:
        if (not isinstance(proposed, dict)
                or set(proposed) != {'name', 'description', 'interaction'}
                or not isinstance(proposed.get('name'), str) or not proposed['name'].strip()
                or not isinstance(proposed.get('description'), str) or not proposed['description'].strip()
                or proposed.get('interaction') not in ('take', 'drop')):
            raise InvalidTurnResponse('Inventory manager returned an invalid item interaction.')
        if explicit_intent is not None:
            intended_operation, target = explicit_intent
            name = re.sub(r'^(?:the|a|an|my)\s+', '', _normalized_text(proposed['name']))
            if name != target:
                reason = 'Item does not match the target of the explicit inventory command.'
                logger.warning('Ignoring inventory proposal for %s: %s', proposed['name'], reason)
                if on_progress is not None:
                    on_progress('inventory', 'ignored', proposed_entity=copy.deepcopy(proposed),
                                reason=reason)
                continue
            if proposed['interaction'] != intended_operation:
                original_interaction = proposed['interaction']
                proposed['interaction'] = intended_operation
                reason = f'Corrected {original_interaction} to {intended_operation} from current_action.'
                logger.warning('%s Item %s in game %s.', reason, proposed['name'], state['id'])
                if on_progress is not None:
                    on_progress('inventory', 'corrected',
                                proposed_entity=copy.deepcopy(proposed), reason=reason)
        interaction = proposed['interaction']
        player_holder = {'type': 'player', 'id': state['playerId']}
        location_holder = {'type': 'location', 'id': current_location_id}
        candidates = []
        for existing in state['world']['items']:
            holder = existing.get('holder')
            accessible = (
                holder == player_holder or holder == location_holder
                or (isinstance(holder, dict) and holder.get('type') == 'npc'
                    and holder.get('id') in current_npc_ids)
            )
            if (existing.get('status') == 'available'
                    and (holder == player_holder if interaction == 'drop' else accessible)):
                candidates.append(existing)
        item = _resolve_entity(
            state, 'items', proposed, comparator, current_location_id,
            create_if_missing=proposed['interaction'] == 'take',
            on_progress=on_progress,
            candidates=candidates,
        )
        if item is None:
            reason = 'No available carried item matches this drop; no item was changed.'
            logger.warning('Ignoring unmatched drop of %s in game %s: %s',
                           proposed['name'], state['id'], reason)
            if on_progress is not None:
                on_progress('inventory', 'ignored', proposed_entity=copy.deepcopy(proposed),
                            reason=reason)
            continue

        holder = item.get('holder')
        if item.get('status') != 'available':
            raise InvalidTurnResponse('Inventory manager can only interact with available items.')
        if interaction == 'take':
            held_by_current_npc = (
                isinstance(holder, dict) and holder.get('type') == 'npc'
                and holder.get('id') in current_npc_ids
            )
            if holder != player_holder and holder != location_holder and not held_by_current_npc:
                raise InvalidTurnResponse('Inventory manager can only take an item available here.')
            item['holder'] = player_holder
            item['knownByPlayer'] = True
            item['status'] = 'available'
        else:
            if holder == player_holder:
                item['holder'] = location_holder
                item['knownByPlayer'] = True
                item['status'] = 'available'
            elif holder != location_holder:
                raise InvalidTurnResponse('Inventory manager can only drop a carried item.')
        resolved.append({
            'id': item['id'],
            'name': item['name'],
            'description': item.get('description', ''),
        })
        applied.append(proposed)
    manager_result['items'] = applied
    return resolved


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
    context = _context(state)
    completed_history = [
        {'kind': event['kind'], 'content': event['content']}
        for event in state['history'].get('recentEvents', [])
    ]
    manager_turn_context = {
        'current_action': action,
        'completed_history': [
            {'kind': event['kind'],
             'content': event['content'][:MANAGER_HISTORY_CONTENT_LIMIT]}
            for event in completed_history[-MANAGER_HISTORY_EVENTS:]
        ],
    }

    ignored_interactions = []

    def progress(component, status, **details):
        if status == 'ignored':
            ignored_interactions.append({'component': component, **copy.deepcopy(details)})
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    manager_contexts = {
        'inventory': {
            **manager_turn_context,
            **{key: context[key] for key in (
                'current_location', 'carried_items', 'items_here', 'objects_here',
            )},
        },
        'map': {
            **manager_turn_context,
            **{key: context[key] for key in (
                'current_location', 'exits', 'known_locations',
            )},
        },
        'npcs': {
            **manager_turn_context,
            **{key: context[key] for key in (
                'current_location', 'npcs_here',
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

    effective_manager_results = copy.deepcopy(manager_results)
    progress('narrator', 'waiting', reason='Waiting for deterministic state reconciliation.')
    candidate = copy.deepcopy(state)
    candidate['history'].pop('summary', None)
    current_location_id = state['currentLocationId']
    try:
        manager_items = _apply_inventory_interactions(
            candidate, effective_manager_results['inventory'], calls['comparator'],
            current_location_id, on_progress=progress, action=action)
        manager_locations = _apply_map_interactions(
            candidate, effective_manager_results['map'], calls['comparator'], on_progress=progress)
        current_location_id = candidate['currentLocationId']
        manager_npcs = _reconcile_collection(
            candidate, 'npcs', effective_manager_results['npcs']['npcs'],
            calls['comparator'], current_location_id, on_progress=progress,
        )
    except (InvalidTurnResponse, LLMError) as error:
        progress('narrator', 'skipped', reason='State reconciliation failed.', error=str(error))
        raise
    reconciled = {
        'items': manager_items,
        'locations': manager_locations,
        'npcs': manager_npcs,
    }
    for category, entities in reconciled.items():
        seen = set()
        unique_entities = []
        for entity in entities:
            if entity['id'] not in seen:
                seen.add(entity['id'])
                unique_entities.append(entity)
        reconciled[category] = unique_entities

    notable = {
        field: copy.deepcopy(effective_manager_results[manager][field])
        for manager, field in (('inventory', 'items'), ('map', 'locations'), ('npcs', 'npcs'))
    }
    resolved_context = _context(candidate)
    movement_outcome = {
        'moved': candidate['currentLocationId'] != state['currentLocationId'],
        'from_location': context['current_location'],
        'to_location': resolved_context['current_location'],
    }
    narrator_input = {
        'current_action': action,
        'completed_history': completed_history,
        'game_context': context,
        'resolved_context': resolved_context,
        'movement_outcome': movement_outcome,
        'resolved_interactions': notable,
        'ignored_interactions': ignored_interactions,
    }
    progress('narrator', 'started', request=narrator_input)
    try:
        narrator_result = _run_logged('narrator', calls['narrator'], narrator_input)
        response = NarratorCall.validate_response(narrator_result.data)
    except LLMError as error:
        progress('narrator', 'failed', error=str(error),
                 response=getattr(error, 'details', None))
        raise
    progress('narrator', 'completed', response={
        **response, 'notable_elements': notable, 'movement_outcome': movement_outcome},
             provider=narrator_result.provider, model=narrator_result.model)

    if response['game_over']:
        candidate['status'] = 'ended'
        candidate['endReason'] = 'character_died'
    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': response['narration']},
    ])
    logger.info(
        'Resolved notable elements for game %s: %s',
        state['id'], {key: [entity['name'] for entity in values]
                      for key, values in reconciled.items()},
    )
    return candidate, {
        'narration': response['narration'],
        'action_succeeded': response['action_succeeded'],
        'game_over': response['game_over'],
        'notable_elements': copy.deepcopy(notable),
        'reconciled_entities': reconciled,
        'manager_results': manager_results,
        'effective_manager_results': effective_manager_results,
        'movement_outcome': movement_outcome,
        'ignored_interactions': ignored_interactions,
        'provider': narrator_result.provider,
        'model': narrator_result.model,
    }
