"""Direct-narration turns: one LLM call narrates and returns the whole world.

Code validates only structure and referential integrity. Storytelling mistakes
(forgotten items, inconsistent geography, narration that disagrees with state)
are kept on purpose, so this mode can be compared with the managed engine.
"""
import copy
import json
import logging

try:
    from backend.game_engine import InvalidTurnResponse, _context, _required_string
    from backend.harness.llm_calls import LLMResponseError
    from backend.harness.llm_provider import LLMError
    from backend.llm_calls.gameplay import DirectNarratorCall
except ImportError:
    from game_engine import InvalidTurnResponse, _context, _required_string
    from harness.llm_calls import LLMResponseError
    from harness.llm_provider import LLMError
    from llm_calls.gameplay import DirectNarratorCall


logger = logging.getLogger(__name__)
COMPONENT = 'direct_narrator'
_FIELDS = {
    'locations': ('id', 'name', 'description', 'aliases', 'visited', 'exits'),
    'items': ('id', 'name', 'description', 'aliases', 'knownByPlayer', 'holder'),
    'objects': ('id', 'name', 'description', 'aliases', 'locationId', 'knownByPlayer',
                'containedInObjectId'),
    'npcs': ('id', 'name', 'description', 'aliases', 'currentLocationId', 'status'),
}
_EXIT_FIELDS = ('id', 'description', 'destinationId', 'traversable', 'identified')


def _project(state):
    """The world in the same shape the direct narrator must return."""
    world = state['world']
    locations = [{
        **{field: copy.deepcopy(location.get(field)) for field in _FIELDS['locations']},
        'aliases': list(location.get('aliases', [])),
        'visited': bool(location.get('visited')),
        'exits': [{
            'id': exit_.get('id', ''),
            'description': exit_.get('description', ''),
            'destinationId': exit_.get('destinationId'),
            'traversable': exit_.get('traversable', True),
            'identified': exit_.get('identified', True),
        } for exit_ in location.get('exits', [])],
    } for location in world['locations']]
    items = [{
        **{field: copy.deepcopy(item.get(field)) for field in _FIELDS['items']},
        'aliases': list(item.get('aliases', [])),
        'knownByPlayer': bool(item.get('knownByPlayer', True)),
    } for item in world['items'] if item.get('status') == 'available']
    objects = [{
        **{field: copy.deepcopy(object_.get(field)) for field in _FIELDS['objects']},
        'aliases': list(object_.get('aliases', [])),
        'knownByPlayer': bool(object_.get('knownByPlayer')),
    } for object_ in world.get('objects', []) if object_.get('status') == 'present']
    npcs = [{
        **{field: copy.deepcopy(npc.get(field)) for field in _FIELDS['npcs']},
        'aliases': list(npc.get('aliases', [])),
    } for npc in world['npcs']]
    return {
        'currentLocationId': state['currentLocationId'],
        'locations': locations, 'items': items, 'objects': objects, 'npcs': npcs,
    }


def _apply(state, data):
    """Replace the world with the narrator's version after integrity checks.

    Returns warnings for references that were safely downgraded rather than rejected.
    """
    warnings = []
    ids = {}
    for collection in _FIELDS:
        seen = set()
        for entry in data[collection]:
            missing = [field for field in _FIELDS[collection] if field not in entry]
            if missing:
                raise InvalidTurnResponse(
                    f'{collection} entry {entry["id"]!r} is missing {", ".join(missing)}.')
            if entry['id'] in seen:
                raise InvalidTurnResponse(f'Duplicate {collection} id {entry["id"]!r}.')
            seen.add(entry['id'])
        ids[collection] = seen

    def check(condition, message):
        if not condition:
            raise InvalidTurnResponse(message)

    check(data['currentLocationId'] in ids['locations'],
          'currentLocationId does not match a returned location.')
    for location in data['locations']:
        check(isinstance(location['exits'], list), f'Exits of {location["id"]!r} must be a list.')
        for exit_ in location['exits']:
            check(isinstance(exit_, dict) and all(field in exit_ for field in _EXIT_FIELDS),
                  f'An exit of {location["id"]!r} is malformed.')
            if exit_['destinationId'] is not None and exit_['destinationId'] not in ids['locations']:
                warning = (f'Exit {exit_["id"]!r} of {location["id"]!r} led to unknown location '
                           f'{exit_["destinationId"]!r}; kept as unexplored.')
                logger.warning('Direct narrator in game %s: %s', state['id'], warning)
                warnings.append(warning)
                exit_['destinationId'] = None
    holder_ids = {'player': {state['playerId']}, 'location': ids['locations'], 'npc': ids['npcs']}
    for item in data['items']:
        holder = item['holder']
        check(isinstance(holder, dict) and holder.get('type') in holder_ids
              and holder.get('id') in holder_ids[holder['type']],
              f'Item {item["id"]!r} has an unknown holder.')
    for object_ in data['objects']:
        check(object_['locationId'] in ids['locations'],
              f'Object {object_["id"]!r} is in an unknown location.')
        check(object_['containedInObjectId'] is None
              or object_['containedInObjectId'] in ids['objects'],
              f'Object {object_["id"]!r} is inside an unknown object.')
    for npc in data['npcs']:
        check(npc['currentLocationId'] in ids['locations'],
              f'NPC {npc["id"]!r} is in an unknown location.')
        check(npc['status'] in ('active', 'departed', 'dead'),
              f'NPC {npc["id"]!r} has an invalid status.')

    world = state['world']
    previous_npcs = {npc['id']: npc for npc in world['npcs']}
    world['locations'] = [
        {**{field: copy.deepcopy(location[field]) for field in _FIELDS['locations']},
         'exits': [{field: exit_[field] for field in _EXIT_FIELDS} for exit_ in location['exits']]}
        for location in data['locations']
    ]
    # Records the direct narrator never sees (e.g. non-available items) are kept as-is.
    world['items'] = [
        {**{field: copy.deepcopy(item[field]) for field in _FIELDS['items']}, 'status': 'available'}
        for item in data['items']
    ] + [item for item in world['items'] if item.get('status') != 'available']
    world['objects'] = [
        {**{field: copy.deepcopy(object_[field]) for field in _FIELDS['objects']},
         'status': 'present'}
        for object_ in data['objects']
    ] + [object_ for object_ in world.get('objects', []) if object_.get('status') != 'present']
    world['npcs'] = [
        {**{field: copy.deepcopy(npc[field]) for field in _FIELDS['npcs']},
         'state': copy.deepcopy(previous_npcs.get(npc['id'], {}).get('state', {}))}
        for npc in data['npcs']
    ]
    if data['currentLocationId'] != state['currentLocationId']:
        state['history']['lastMovement'] = {
            'fromLocationId': state['currentLocationId'],
            'toLocationId': data['currentLocationId'],
        }
    state['currentLocationId'] = data['currentLocationId']
    return warnings


def process_direct_turn(state, action, settings=None, on_progress=None, call=None):
    """Resolve a turn with a single narrator call that owns all state changes."""
    action = _required_string(action, 'action', 2000)
    call = call or DirectNarratorCall(settings=settings)

    def progress(status, **details):
        if on_progress is not None:
            on_progress({'component': COMPONENT, 'status': status, **details})

    request = {
        'current_action': action,
        'history': [{'kind': event['kind'], 'content': event['content']}
                    for event in state['history'].get('recentEvents', [])],
        'player_id': state['playerId'],
        'state': _project(state),
    }
    logger.info('Direct turn for game %s: %s', state['id'], action)
    progress('started', request=request)
    try:
        result = call.run(json.dumps(request, ensure_ascii=False))
    except LLMError as error:
        details = {'response': error.details} if isinstance(error, LLMResponseError) else {}
        progress('failed', error=str(error), **details)
        raise
    data = copy.deepcopy(result.data)
    candidate = copy.deepcopy(state)
    from_location = _context(state)['current_location']
    try:
        warnings = _apply(candidate, data)
    except InvalidTurnResponse as error:
        progress('failed', error=str(error), response=data)
        raise
    to_location = _context(candidate)['current_location']
    movement_outcome = {
        'moved': candidate['currentLocationId'] != state['currentLocationId'],
        'from_location': from_location,
        'to_location': to_location,
    }
    progress('completed', response=result.data, provider=result.provider, model=result.model,
             **({'reason': ' '.join(warnings)} if warnings else {}))

    if data['game_over']:
        candidate['status'] = 'ended'
        candidate['endReason'] = 'character_died'
    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': data['narration']},
    ])
    return candidate, {
        'narration': data['narration'],
        'action_succeeded': data['action_succeeded'],
        'game_over': data['game_over'],
        'movement_outcome': movement_outcome,
        'provider': result.provider,
        'model': result.model,
    }
