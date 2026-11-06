"""Game-master conversation with native tools over a transactional world draft."""
import copy
import json
import logging
import os
import socket
import time
import uuid

try:
    from backend.game_engine import (
        InvalidTurnResponse, _add_exit, _required_string, _resolve_entity, current_summary,
    )
    from backend.harness.llm_calls import LLMCall, LLMResponseError
    from backend.harness.llm_provider import LLMError, chat
    from backend.llm_calls.gameplay import ComparatorCall, HistorySummarizerCall
except ImportError:
    from game_engine import (
        InvalidTurnResponse, _add_exit, _required_string, _resolve_entity, current_summary,
    )
    from harness.llm_calls import LLMCall, LLMResponseError
    from harness.llm_provider import LLMError, chat
    from llm_calls.gameplay import ComparatorCall, HistorySummarizerCall

logger = logging.getLogger(__name__)
MAX_ROUNDS = 12
MAX_TOOL_CALLS = 32


def scene(state):
    location_id = state['currentLocationId']
    world = state['world']
    npcs = [npc for npc in world['npcs']
            if npc['currentLocationId'] == location_id and npc['status'] == 'active']
    npc_holders = [{'type': 'npc', 'id': npc['id']} for npc in npcs]
    return copy.deepcopy({
        'current_location': next(location for location in world['locations']
                                 if location['id'] == location_id),
        'items_here': [item for item in world['items'] if item.get('status') == 'available'
                       and (item.get('holder') == {'type': 'location', 'id': location_id}
                            or item.get('holder') in npc_holders)],
        'npcs_here': npcs,
        'inventory': [item for item in world['items'] if item.get('status') == 'available'
                      and item.get('holder') == {'type': 'player', 'id': state['playerId']}],
        'known_locations': [{key: location[key] for key in ('id', 'name', 'description')}
                            for location in world['locations']],
    })


class GameMasterCall(LLMCall):
    name = 'game_master'

    def __init__(self, settings=None, transport=None):
        super().__init__(settings=settings)
        with open(os.path.join(self.definitions_dir, 'game_master.tools.json'),
                  encoding='utf-8') as definitions:
            self.tools = json.load(definitions)
        self.transport = transport or (
            lambda messages, tools: chat(messages, tools=tools, settings=settings, temperature=0))

    def respond(self, messages, tools=None):
        started = time.monotonic()
        tools = self.tools if tools is None else tools
        logger.info('LLM game_master request: %s',
                    json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
        try:
            result = self.transport(messages, tools)
            logger.info('LLM game_master response after %.3fs from %s/%s: %s',
                        time.monotonic() - started, result.get('provider'), result.get('model'),
                        json.dumps(result.get('message'), ensure_ascii=False))
            return result
        except (LLMError, TimeoutError, socket.timeout) as error:
            logger.exception('LLM game_master failed after %.3fs', time.monotonic() - started)
            raise LLMError(
                f'Game master request failed: {error}',
                details=getattr(error, 'details', None),
                code=getattr(error, 'code', None),
            ) from error


class WorldTools:
    def __init__(self, state, definitions, comparator, progress):
        self.state = state
        self.definitions = {tool['function']['name']: tool['function']['parameters']
                            for tool in definitions}
        self.comparator = comparator
        self.progress = progress

    def available_tools(self, definitions):
        current = scene(self.state)
        locations = [location['id'] for location in current['known_locations']]
        local_items = [item['id'] for item in current['items_here']]
        carried_items = [item['id'] for item in current['inventory']]
        npcs = [npc['id'] for npc in current['npcs_here']]
        choices = {
            'move_to_location': {'destination_id': [
                location_id for location_id in locations
                if location_id != self.state['currentLocationId']]},
            'create_item': {'location_id': locations},
            'create_npc': {'location_id': locations},
            'pick_up_item': {'item_id': local_items},
            'drop_item': {'item_id': carried_items},
            'destroy_item': {'item_id': local_items + carried_items},
            'destroy_npc': {'npc_id': npcs},
            'move_npc': {'npc_id': npcs, 'destination_id': locations},
        }
        available = []
        for definition in copy.deepcopy(definitions):
            function = definition['function']
            fields = choices.get(function['name'], {})
            if any(not ids for ids in fields.values()):
                continue
            for field, ids in fields.items():
                function['parameters']['properties'][field]['enum'] = ids
            available.append(definition)
        return available

    def entity(self, collection, entity_id):
        entity = next((entity for entity in self.state['world'][collection]
                       if entity['id'] == entity_id), None)
        if entity is None:
            raise InvalidTurnResponse(f'Unknown {collection} ID: {entity_id}.')
        return entity

    def move(self, origin_id, destination_id):
        origin = self.entity('locations', origin_id)
        destination = self.entity('locations', destination_id)
        if any(exit_.get('destinationId') == destination_id
               and not exit_.get('traversable', True) for exit_ in origin.get('exits', [])):
            raise InvalidTurnResponse('The requested route is blocked.')
        if origin_id != destination_id:
            _add_exit(origin, destination)
            _add_exit(destination, origin)
        return destination

    def establish(self, collection, args, location_id, progress):
        before = {entity['id'] for entity in self.state['world'][collection]}
        entity = _resolve_entity(
            self.state, collection, {key: args[key] for key in ('name', 'description')},
            self.comparator, location_id, on_progress=progress)
        created = entity['id'] not in before
        if created and collection == 'items':
            entity['holder'] = {'type': 'location', 'id': location_id}
        return entity, created

    def accessible_item(self, item):
        holder = item.get('holder')
        location_id = self.state['currentLocationId']
        if item.get('status') != 'available':
            raise InvalidTurnResponse('The item is not available.')
        if holder in ({'type': 'player', 'id': self.state['playerId']},
                      {'type': 'location', 'id': location_id}):
            return
        if isinstance(holder, dict) and holder.get('type') == 'npc':
            npc = self.entity('npcs', holder.get('id'))
            if npc['status'] == 'active' and npc['currentLocationId'] == location_id:
                return
        raise InvalidTurnResponse('The item is not accessible in the current scene.')

    def present_npc(self, npc):
        if npc['status'] != 'active' or npc['currentLocationId'] != self.state['currentLocationId']:
            raise InvalidTurnResponse('The NPC must be active and present in the current scene.')

    def apply(self, name, args, comparison_progress):
        location_id = self.state['currentLocationId']
        if name in ('move_to_location', 'move_to_new_location'):
            if name == 'move_to_new_location':
                destination, created = self.establish('locations', args, location_id,
                                                      comparison_progress)
            else:
                destination = self.entity('locations', args['destination_id'])
                created = False
            self.move(location_id, destination['id'])
            destination['visited'] = True
            self.state['currentLocationId'] = destination['id']
            if location_id != destination['id']:
                self.state['history']['lastMovement'] = {
                    'fromLocationId': location_id, 'toLocationId': destination['id'],
                }
            return {'entity': copy.deepcopy(destination), 'created': created}
        if name in ('create_item', 'create_npc'):
            self.entity('locations', args['location_id'])
            collection = 'items' if name == 'create_item' else 'npcs'
            entity, created = self.establish(collection, args, args['location_id'],
                                             comparison_progress)
            return {'entity': copy.deepcopy(entity), 'created': created}
        if name in ('pick_up_item', 'drop_item', 'destroy_item'):
            item = self.entity('items', args['item_id'])
            self.accessible_item(item)
            if name == 'pick_up_item':
                item['holder'] = {'type': 'player', 'id': self.state['playerId']}
                item['knownByPlayer'] = True
            elif name == 'drop_item':
                if item['holder'] != {'type': 'player', 'id': self.state['playerId']}:
                    raise InvalidTurnResponse('Only carried items can be dropped.')
                item['holder'] = {'type': 'location', 'id': location_id}
                item['knownByPlayer'] = True
            else:
                self.state['world']['items'].remove(item)
            return {'entity': copy.deepcopy(item), 'destroyed': name == 'destroy_item'}
        npc = self.entity('npcs', args['npc_id'])
        self.present_npc(npc)
        if name == 'move_npc':
            self.move(npc['currentLocationId'], args['destination_id'])
            npc['currentLocationId'] = args['destination_id']
        else:
            npc['status'] = 'dead'
            for item in self.state['world']['items']:
                if item.get('holder') == {'type': 'npc', 'id': npc['id']}:
                    item['holder'] = {'type': 'location', 'id': location_id}
        return {'entity': copy.deepcopy(npc)}

    def execute(self, call):
        name = call['function']['name']
        args = call['function']['arguments']
        operation_id = uuid.uuid4().hex
        details = {'operation_id': operation_id, 'tool_name': name,
                   'tool_call_id': call['id']}
        self.progress('tool', 'started', request=args, **details)
        logger.info('Tool %s [%s] started: %s', name, call['id'], json.dumps(args))
        snapshot = copy.deepcopy(self.state)
        comparison_id = None

        def comparison_progress(component, status, **event):
            nonlocal comparison_id
            if status == 'started' or comparison_id is None:
                comparison_id = uuid.uuid4().hex
            self.progress(component, status, operation_id=comparison_id,
                          parent_operation_id=operation_id, **event)

        try:
            schema = self.definitions.get(name)
            if schema is None:
                raise InvalidTurnResponse(f'Unknown tool: {name}.')
            if not isinstance(args, dict) or set(args) != set(schema['required']):
                raise InvalidTurnResponse(f'{name} requires exactly: {", ".join(schema["required"])}.')
            args = {key: _required_string(value, key, 2000 if key == 'description' else 255)
                    for key, value in args.items()}
            outcome = self.apply(name, args, comparison_progress)
        except (InvalidTurnResponse, LLMResponseError) as error:
            self.state.clear()
            self.state.update(snapshot)
            logger.warning('Tool %s [%s] rejected: %s', name, call['id'], error)
            outcome = {'error': str(error)}
            self.progress('tool', 'failed', error=str(error), **details)
        except LLMError as error:
            self.state.clear()
            self.state.update(snapshot)
            logger.exception('Tool %s [%s] provider failure', name, call['id'])
            self.progress('tool', 'failed', error=str(error), **details)
            raise
        else:
            logger.info('Tool %s [%s] completed: %s', name, call['id'], json.dumps(outcome))
            self.progress('tool', 'completed', response=outcome, **details)
        return {**outcome, 'scene': scene(self.state)}


def process_tool_turn(state, action, settings=None, on_progress=None, call=None,
                      comparator=None, summarizer=None):
    action = _required_string(action, 'action', 2000)
    call = call or GameMasterCall(settings=settings)
    comparator = comparator or ComparatorCall(settings=settings)
    summarizer = summarizer or HistorySummarizerCall(settings=settings)
    candidate = copy.deepcopy(state)

    def progress(component, status, **details):
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    context = {
        'player_action': action,
        'historical_summary': current_summary(candidate, summarizer, progress),
        **scene(candidate),
    }
    messages = [
        {'role': 'system', 'content': call.build_system_prompt()},
        {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)},
    ]
    executor = WorldTools(candidate, call.tools, comparator, progress)
    seen_ids = set()
    tool_results = []
    for round_number in range(1, MAX_ROUNDS + 1):
        operation_id = uuid.uuid4().hex
        offered_tools = executor.available_tools(call.tools)
        progress('game_master', 'started', operation_id=operation_id,
                 attempt=round_number, request={
                     'messages': copy.deepcopy(messages), 'tools': copy.deepcopy(offered_tools),
                 })
        try:
            result = call.respond(messages, tools=offered_tools)
            message = result.get('message')
            if (not isinstance(message, dict) or message.get('role') != 'assistant'
                    or not isinstance(message.get('content'), str)
                    or not isinstance(message.get('tool_calls'), list)):
                raise LLMError('The game master returned an invalid conversation response.')
            progress('game_master', 'completed', operation_id=operation_id,
                     response=copy.deepcopy(message), provider=result.get('provider'),
                     model=result.get('model'))
            messages.append(copy.deepcopy(message))
            if not message['tool_calls']:
                narration = message['content'].strip()
                if not narration:
                    raise LLMError('The game master returned neither story text nor tool calls.')
                try:
                    structured = json.loads(narration)
                except json.JSONDecodeError:
                    structured = None
                if isinstance(structured, (dict, list)):
                    raise LLMError('The game master returned JSON instead of final story text. '
                                   'Use a model that supports native tool conversations.')
                break
            for tool_call in message['tool_calls']:
                if tool_call['id'] in seen_ids:
                    raise LLMError('The game master reused a tool call ID.')
                if len(seen_ids) >= MAX_TOOL_CALLS:
                    raise LLMError('The game master exceeded the tool-call limit.')
                seen_ids.add(tool_call['id'])
                outcome = executor.execute(tool_call)
                tool_results.append({'name': tool_call['function']['name'],
                                     'id': tool_call['id'], 'result': copy.deepcopy(outcome)})
                messages.append({
                    'role': 'tool', 'tool_call_id': tool_call['id'],
                    'name': tool_call['function']['name'],
                    'content': json.dumps(outcome, ensure_ascii=False),
                })
            messages.append({
                'role': 'user',
                'content': (
                    'Tool execution checkpoint, not a new player action. The tool '
                    'results above contain the authoritative updated scene. '
                    'Continue resolving the original action. Rejected tools changed '
                    'nothing. Correct errors with tools or explain the obstacle; never '
                    'narrate rejected or unperformed changes as successful. Establish '
                    'missing entities with creation tools before using their returned IDs.'
                ),
            })
        except LLMError as error:
            progress('game_master', 'failed', operation_id=operation_id, error=str(error),
                     response=error.details)
            raise
    else:
        logger.error('Game master exceeded the conversation-round limit')
        progress('game_master', 'failed', operation_id=operation_id,
                 error='The game master exceeded the conversation-round limit.')
        raise LLMError('The game master exceeded the conversation-round limit.')
    history = candidate['history']
    history['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': narration},
    ])
    return candidate, {
        'narration': narration, 'tool_results': tool_results,
        'provider': result.get('provider'), 'model': result.get('model'),
        'movement_outcome': {
            'moved': candidate['currentLocationId'] != state['currentLocationId'],
            'from_location': scene(state)['current_location'],
            'to_location': scene(candidate)['current_location'],
        },
    }
