"""Deterministic game state and LLM-assisted turn orchestration."""
import copy
import json
import logging
import re
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from backend.harness.llm_calls import LLMResponseError
    from backend.harness.llm_provider import LLMError
    from backend.llm_calls.gameplay import (
        ComparatorCall,
        InventoryManagerCall,
        MapManagerCall,
        MovementResolverCall,
        NPCManagerCall,
        NarratorCall,
        HistorySummarizerCall,
    )
except ImportError:
    from harness.llm_calls import LLMResponseError
    from harness.llm_provider import LLMError
    from llm_calls.gameplay import (
        ComparatorCall,
        InventoryManagerCall,
        MapManagerCall,
        MovementResolverCall,
        NPCManagerCall,
        NarratorCall,
        HistorySummarizerCall,
    )


START_LOCATION_ID = 'town-square-outside-tavern'
STARTING_POINTS = {
    'town': {
        'id': START_LOCATION_ID,
        'name': 'Town Square Outside the Tavern',
        'description': 'The town square outside the tavern.',
        'aliases': ['town square'],
        'narration': 'You stand in the town square outside the tavern. The tavern door is nearby, and the square is open around you.',
    },
    'wilderness': {
        'id': 'wilderness-trail',
        'name': 'Wilderness Trail',
        'description': 'A winding trail through untamed wilderness, far from town.',
        'aliases': ['wilderness'],
        'narration': 'You are exploring the wilderness along a winding trail. Trees and tangled undergrowth surround you, and the path continues into the unknown.',
    },
    'dungeon': {
        'id': 'dungeon-entrance',
        'name': 'Dungeon Entrance',
        'description': 'The entrance to a dungeon or lair, opening into a rocky hillside.',
        'aliases': ['lair entrance'],
        'narration': 'You stand at the entrance to a dungeon or lair. A dark opening leads into the rocky hillside before you, while the open countryside lies behind you.',
    },
}
logger = logging.getLogger(__name__)


class InvalidTurnResponse(ValueError):
    """Raised when an LLM response cannot be reconciled safely."""


def new_game(player_id, starting_point='town'):
    if not isinstance(starting_point, str) or starting_point not in STARTING_POINTS:
        raise ValueError(f'starting_point must be one of: {", ".join(STARTING_POINTS)}.')
    start = STARTING_POINTS[starting_point]
    return {
        'id': str(uuid.uuid4()),
        'playerId': str(player_id),
        'currentLocationId': start['id'],
        'status': 'active',
        'endReason': None,
        'world': {
            'locations': [{
                'id': start['id'],
                'name': start['name'],
                'description': start['description'],
                'aliases': list(start['aliases']),
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
                'content': start['narration'],
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
            if name and any(_normalized_text(value) == name
                            for value in (entity['name'], *entity.get('aliases', [])))
        ]
        exact_matches = [
            entity for entity in candidates
            if all(_normalized_text(entity.get(field, ''))
                   == _normalized_text(proposed[field])
                   for field in ('name', 'description'))
        ]
        if len(exact_matches) > 1:
            error = f'Ambiguous {collection_name} identity: multiple exact matches.'
            progress('started', llm=False, request={
                'entity_type': collection_name,
                'proposed_entity': _display_entity(proposed),
            })
            progress('failed', llm=False, error=error, response={
                'matched_entities': [_display_entity(entity) for entity in exact_matches],
            })
            raise InvalidTurnResponse(error)
        text_matches = name_matches if len(name_matches) == 1 else (
            exact_matches if not name_matches else [])
        if len(text_matches) == 1:
            entity = text_matches[0]
            reason = ('Unique normalized name and description match; no LLM call needed.'
                      if exact_matches else 'Unique normalized name or alias match; no LLM call needed.')
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
                'candidate_entity': {
                    **_display_entity(entity),
                    **({'aliases': list(entity['aliases'])} if entity.get('aliases') else {}),
                },
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
            progress('started', llm=False, request={
                'entity_type': collection_name,
                'proposed_entity': _display_entity(proposed),
            })
            progress('failed', llm=False, error=error, response={
                'matched_entities': [_display_entity(entity) for entity in matches],
            })
            raise InvalidTurnResponse(error)
        if matches:
            entity = matches[0]
            if (any(entity is canonical for canonical in entities)
                    and name and all(
                        _normalized_text(value) != name
                        for value in (entity['name'], *entity.get('aliases', [])))):
                entity.setdefault('aliases', []).append(proposed['name'].strip())
            return entity
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


def _resolve_movement(state, intent, context, calls, progress):
    MapManagerCall.validate_response(intent)
    if not intent['movement']:
        progress('movement_resolver', 'skipped', llm=False,
                 request={'movement_intent': intent},
                 reason='No travel requested; destination resolution is unnecessary.',
                 response={'destination': None, 'clarification': None})
        return [], [], None

    current_id = state['currentLocationId']
    locations = state['world']['locations']
    current = next(location for location in locations if location['id'] == current_id)
    by_id = {location['id']: location for location in locations}
    last_movement = state['history'].get('lastMovement')
    previous = (by_id.get(last_movement['fromLocationId'])
                if last_movement and last_movement['toLocationId'] == current_id else None)
    target = intent['target']
    destination = None
    if target['kind'] == 'named':
        text = target['text'].strip()
        resolution = {
            'destination': {'name': text, 'description': f'A place known as {text}.'},
            'clarification': None,
        }
        progress('movement_resolver', 'skipped', llm=False,
                 request={'movement_intent': intent},
                 reason='Named destination goes directly to identity matching.', response=resolution)
    elif target['kind'] == 'back':
        destination = previous
        resolution = {
            'destination': _display_entity(destination) if destination else None,
            'clarification': None if previous else 'Which location would you like to return to?',
        }
        progress('movement_resolver', 'skipped', llm=False,
                 request={
                     'movement_intent': intent,
                     'last_movement': {
                         'from_location': _display_entity(previous),
                         'to_location': context['current_location'],
                     } if previous else None,
                 },
                 reason='Return destination comes from the last actual movement.', response=resolution)
    else:
        request = {
            'movement_intent': intent,
            'current_location': context['current_location'],
            'available_exits': [
                display for exit_, display in zip(current.get('exits', []), context['exits'])
                if exit_.get('traversable', True)
            ],
            'blocked_exits': [
                display for exit_, display in zip(current.get('exits', []), context['exits'])
                if not exit_.get('traversable', True)
            ],
            'known_locations': [_display_entity(location) for location in locations
                                if location['id'] != current_id],
            'last_movement': {
                'from_location': _display_entity(previous),
                'to_location': context['current_location'],
            } if previous else None,
        }
        if target['kind'] in ('relative', 'reference'):
            request['recent_exchanges'] = state['history'].get('recentEvents', [])[-6:]
            request['npcs_here'] = context['npcs_here']
        progress('movement_resolver', 'started', request=request)
        try:
            result = _run_logged('movement_resolver', calls['movement_resolver'], request)
            resolution = MovementResolverCall.validate_response(result.data)
        except LLMError as error:
            progress('movement_resolver', 'failed', error=str(error),
                     response=getattr(error, 'details', None))
            raise
        progress('movement_resolver', 'completed', response=resolution,
                 provider=result.provider, model=result.model)

    proposed = resolution['destination']
    if proposed is not None:
        if destination is None:
            if _normalized_text(proposed['name']) == _normalized_text(current['name']):
                destination = current
            else:
                destination = _resolve_entity(
                    state, 'locations', proposed, calls['comparator'], current_id, on_progress=progress,
                    candidates=[location for location in locations if location['id'] != current_id])
        if any(exit_.get('destinationId') == destination['id']
               and not exit_.get('traversable', True) for exit_ in current.get('exits', [])):
            resolution = {'destination': None, 'clarification': 'The requested route is blocked.'}
        else:
            resolution = {'destination': _display_entity(destination), 'clarification': None}

    if resolution['destination'] is None:
        reason = resolution['clarification']
        logger.warning('Unresolved movement in game %s: %s', state['id'], reason)
        progress('map', 'ignored', response=intent, reason=reason)
        return [], [], resolution

    if destination['id'] == current_id:
        return [], [], resolution

    _add_exit(current, destination)
    _add_exit(destination, current)
    destination['visited'] = True
    state['currentLocationId'] = destination['id']
    state['history']['lastMovement'] = {
        'fromLocationId': current_id, 'toLocationId': destination['id'],
    }
    resolved = [{'id': location['id'], **_display_entity(location)}
                for location in (current, destination)]
    interactions = [
        {**_display_entity(current), 'interaction': 'leave'},
        {**_display_entity(destination), 'interaction': 'enter'},
    ]
    return resolved, interactions, resolution


def _explicit_inventory_intent(action):
    match = re.fullmatch(
        r'\s*(?:please\s+)?(?:(?:I\s+)?(?:want\s+to\s+)?)'
        r'(pick\s+up|take|collect|drop|put\s+down|leave\s+behind|consume|eat|drink|'
        r'break|smash|destroy)\s+'
        r'([\w\s\'-]+?)[.!]?\s*', action, flags=re.IGNORECASE)
    if match is None:
        return None
    target = _normalized_text(match.group(2))
    if (not target or any(word in target.split() for word in (
            'and', 'or', 'then', 'if', 'unless', 'not', 'never', 'it', 'them',
            'everything', 'all', 'from', 'to', 'on', 'in', 'with'))):
        return None
    target = re.sub(r'^(?:the|a|an|my)\s+', '', target)
    verb = _normalized_text(match.group(1))
    operation = (
        'drop' if verb in ('drop', 'put down', 'leave behind') else
        'consume' if verb in ('consume', 'eat', 'drink') else
        'break' if verb in ('break', 'smash', 'destroy') else 'take'
    )
    return operation, target


def _apply_inventory_interactions(state, manager_result, comparator, current_location_id,
                                  on_progress=None, action='', npc_proposals=()):
    if not isinstance(manager_result, dict) or set(manager_result) != {'items'}:
        raise InvalidTurnResponse('Inventory manager must return only an items list.')
    items = manager_result['items']
    if not isinstance(items, list) or len(items) > 20:
        raise InvalidTurnResponse('Inventory manager items must be a list.')

    npc_candidates = {
        _normalized_text(npc['name']): npc
        for npc in (*npc_proposals, *state['world']['npcs'])
    }
    current_npc_ids = {
        npc['id'] for npc in state['world']['npcs']
        if npc.get('status') == 'active' and npc.get('currentLocationId') == current_location_id
    }
    resolved = []
    applied = []
    explicit_intent = _explicit_inventory_intent(action)
    for proposed in items:
        expected_fields = {'name', 'description', 'interaction'}
        if isinstance(proposed, dict) and proposed.get('interaction') == 'give':
            expected_fields.add('recipient')
        if (not isinstance(proposed, dict)
                or set(proposed) != expected_fields
                or not isinstance(proposed.get('name'), str) or not proposed['name'].strip()
                or not isinstance(proposed.get('description'), str) or not proposed['description'].strip()
                or proposed.get('interaction') not in ('take', 'drop', 'give', 'consume', 'break')
                or ('recipient' in expected_fields and (
                    not isinstance(proposed['recipient'], str)
                    or not proposed['recipient'].strip()
                    or len(proposed['recipient']) > 1000))):
            raise InvalidTurnResponse('Inventory manager returned an invalid item interaction.')
        if explicit_intent is not None:
            intended_operation, target = explicit_intent
            name = re.sub(r'^(?:the|a|an|my)\s+', '', _normalized_text(proposed['name']))
            target_match = name == target
            if not target_match:
                target_match = _resolve_entity(
                    state, 'items', proposed, comparator, current_location_id,
                    create_if_missing=False, on_progress=on_progress,
                    candidates=[{'name': target, 'description': target}],
                ) is not None
            if not target_match:
                reason = 'Item does not match the target of the explicit inventory command.'
                logger.warning('Ignoring inventory proposal for %s: %s', proposed['name'], reason)
                if on_progress is not None:
                    on_progress('inventory', 'ignored', proposed_entity=copy.deepcopy(proposed),
                                reason=reason)
                continue
            if proposed['interaction'] != intended_operation:
                if proposed['interaction'] == 'give':
                    proposed.pop('recipient')
                original_interaction = proposed['interaction']
                proposed['interaction'] = intended_operation
                reason = f'Corrected {original_interaction} to {intended_operation} from current_action.'
                logger.warning('%s Item %s in game %s.', reason, proposed['name'], state['id'])
                if on_progress is not None:
                    on_progress('inventory', 'corrected',
                                proposed_entity=copy.deepcopy(proposed), reason=reason)
        interaction = proposed['interaction']
        npc = _resolve_entity(
            state, 'npcs', proposed, comparator, current_location_id,
            create_if_missing=False, on_progress=on_progress,
            candidates=list(npc_candidates.values()),
        ) if npc_candidates else None
        if npc is not None:
            reason = 'Inventory proposal identifies an NPC, not an inventory item.'
            logger.warning('Ignoring NPC inventory proposal for %s in game %s: %s',
                           proposed['name'], state['id'], reason)
            if on_progress is not None:
                on_progress('inventory', 'ignored', proposed_entity=copy.deepcopy(proposed),
                            reason=reason)
            continue
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
                    and (accessible if interaction == 'take' else holder == player_holder)):
                candidates.append(existing)
        item = _resolve_entity(
            state, 'items', proposed, comparator, current_location_id,
            create_if_missing=proposed['interaction'] == 'take',
            on_progress=on_progress,
            candidates=candidates,
        )
        if item is None:
            reason = f'No available carried item matches this {interaction}; no item was changed.'
            logger.warning('Ignoring unmatched %s of %s in game %s: %s',
                           interaction, proposed['name'], state['id'], reason)
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
        elif interaction == 'give':
            recipient = _resolve_entity(
                state, 'npcs',
                {'name': proposed['recipient'], 'description': proposed['recipient']},
                comparator, current_location_id, create_if_missing=False,
                on_progress=on_progress,
                candidates=[npc for npc in state['world']['npcs']
                            if npc['id'] in current_npc_ids],
            )
            if recipient is None:
                reason = 'No present NPC matches the recipient; the item remains carried.'
                logger.warning('Ignoring give of %s in game %s: %s',
                               proposed['name'], state['id'], reason)
                if on_progress is not None:
                    on_progress('inventory', 'ignored', proposed_entity=copy.deepcopy(proposed),
                                reason=reason)
                continue
            item['holder'] = {'type': 'npc', 'id': recipient['id']}
            item['knownByPlayer'] = True
        elif interaction in ('consume', 'break'):
            state['world']['items'].remove(item)
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
        'movement_resolver': MovementResolverCall(settings=settings),
        'npcs': NPCManagerCall(settings=settings),
        'narrator': NarratorCall(settings=settings),
        'comparator': ComparatorCall(settings=settings),
        'summarizer': HistorySummarizerCall(settings=settings),
    }
    context = _context(state)
    completed_history = [
        {'kind': event['kind'], 'content': event['content']}
        for event in state['history'].get('recentEvents', [])
    ]
    ignored_interactions = []

    def progress(component, status, **details):
        if status == 'ignored':
            ignored_interactions.append({'component': component, **copy.deepcopy(details)})
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    def summarize(history):
        request = {'completed_history': copy.deepcopy(history)}
        progress('summarizer', 'started', request=request)
        try:
            result = _run_logged('summarizer', calls['summarizer'], request)
            summary = HistorySummarizerCall.validate_response(result.data)['summary']
        except LLMError as error:
            progress('summarizer', 'failed', error=str(error),
                     response=getattr(error, 'details', None))
            raise
        progress('summarizer', 'completed', response={'summary': summary},
                 provider=result.provider, model=result.model)
        return summary

    history = state['history']
    if ('summary' in history
            and history.get('summaryEventCount') == len(completed_history)):
        historical_summary = HistorySummarizerCall.validate_response(
            {'summary': history['summary']})['summary']
    elif len(completed_history) > 1:
        historical_summary = summarize(completed_history)
    else:
        historical_summary = ''
    manager_turn_context = {
        'current_action': action,
        'historical_summary': historical_summary,
    }

    manager_contexts = {
        'inventory': {
            **manager_turn_context,
            **{key: context[key] for key in (
                'current_location', 'carried_items', 'items_here', 'objects_here', 'npcs_here',
            )},
        },
        'map': {
            'current_action': action,
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
        progress(key, 'started', request=manager_contexts[key])
    progress('movement_resolver', 'waiting',
             reason='Waiting for movement intent and inventory reconciliation.')
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_run_logged, key, calls[key], manager_contexts[key]): key
            for key in ('inventory', 'map', 'npcs')
        }
        manager_results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()
                if key == 'map':
                    MapManagerCall.validate_response(result.data)
            except LLMError as error:
                details = {'response': error.details} if isinstance(error, LLMResponseError) else {}
                progress(key, 'failed', error=str(error), **details)
                progress('movement_resolver', 'skipped', reason='A manager call failed.')
                progress('narrator', 'skipped', reason='A manager call failed.')
                raise
            manager_results[key] = result.data
            progress(key, 'completed', response=result.data,
                     provider=result.provider, model=result.model)

    effective_manager_results = copy.deepcopy(manager_results)
    progress('narrator', 'waiting', reason='Waiting for deterministic state reconciliation.')
    candidate = copy.deepcopy(state)
    current_location_id = state['currentLocationId']
    try:
        manager_items = _apply_inventory_interactions(
            candidate, effective_manager_results['inventory'], calls['comparator'],
            current_location_id, on_progress=progress, action=action,
            npc_proposals=effective_manager_results['npcs']['npcs'])
        manager_locations, location_interactions, movement_resolution = _resolve_movement(
            candidate, manager_results['map'], context, calls, progress)
        effective_manager_results['map'] = {'locations': location_interactions}
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
    resolved_context = {
        key: value for key, value in _context(candidate).items()
        if key in (
            'current_location', 'exits', 'carried_items', 'npcs_here',
            'items_here', 'objects_here',
        )
    }
    movement_outcome = {
        'moved': candidate['currentLocationId'] != state['currentLocationId'],
        'from_location': context['current_location'],
        'to_location': resolved_context['current_location'],
    }
    narrator_input = {
        'current_action': action,
        'historical_summary': historical_summary,
        'resolved_context': resolved_context,
        'movement_outcome': movement_outcome,
        'movement_resolution': movement_resolution,
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
        **response, 'notable_elements': notable, 'movement_outcome': movement_outcome,
        'movement_resolution': movement_resolution},
             provider=narrator_result.provider, model=narrator_result.model)

    if response['game_over']:
        candidate['status'] = 'ended'
        candidate['endReason'] = 'character_died'
    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': response['narration']},
    ])
    candidate['history']['summary'] = historical_summary
    candidate['history']['summaryEventCount'] = len(completed_history)
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
        'movement_resolution': movement_resolution,
        'ignored_interactions': ignored_interactions,
        'provider': narrator_result.provider,
        'model': narrator_result.model,
    }
