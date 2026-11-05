import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.game_engine import new_game
from backend.harness.llm_provider import LLMError
from backend.tool_mode import GameMasterCall, WorldTools, process_tool_turn, scene


def tool(tool_name, **arguments):
    return {'id': tool_name, 'type': 'function',
            'function': {'name': tool_name, 'arguments': arguments}}


def reply(content='', calls=None):
    return {'provider': 'fake', 'model': 'fake',
            'message': {'role': 'assistant', 'content': content, 'tool_calls': calls or []}}


class ToolModeTests(unittest.TestCase):
    def setUp(self):
        self.state = new_game(1)
        self.events = []
        self.master = GameMasterCall(transport=Mock())
        self.comparator = Mock()
        self.comparator.run.return_value = SimpleNamespace(
            data={'outcome': 'new'}, provider='fake', model='fake')
        self.tools = WorldTools(self.state, self.master.tools, self.comparator,
                                lambda component, status, **data: self.events.append(
                                    {'component': component, 'status': status, **data}))

    def execute(self, tool_name, **args):
        return self.tools.execute(tool(tool_name, **args))

    def test_all_world_operations(self):
        origin = self.state['currentLocationId']
        location = self.execute('move_to_new_location', name='Tavern',
                                description='A tavern.')['entity']
        tavern = location['id']
        self.assertEqual(self.state['currentLocationId'], tavern)
        self.assertTrue(location['visited'])
        self.assertEqual(self.state['history']['lastMovement']['fromLocationId'], origin)
        self.assertEqual(self.state['world']['locations'][0]['exits'][0]['destinationId'], tavern)
        self.execute('move_to_location', destination_id=origin)
        self.assertEqual(self.state['currentLocationId'], origin)
        item = self.execute('create_item', name='Apple', description='An apple.',
                            location_id=origin)['entity']
        self.assertEqual(item['holder'], {'type': 'location', 'id': origin})
        self.execute('pick_up_item', item_id=item['id'])
        self.assertEqual(scene(self.state)['inventory'][0]['id'], item['id'])
        self.execute('drop_item', item_id=item['id'])
        self.assertEqual(scene(self.state)['items_here'][0]['id'], item['id'])
        self.execute('destroy_item', item_id=item['id'])
        self.assertEqual(self.state['world']['items'], [])
        npc = self.execute('create_npc', name='Mira', description='A keeper.',
                           location_id=origin)['entity']
        self.execute('move_npc', npc_id=npc['id'], destination_id=tavern)
        self.assertEqual(self.state['currentLocationId'], origin)
        self.assertEqual(self.state['world']['npcs'][0]['currentLocationId'], tavern)
        self.execute('move_to_location', destination_id=tavern)
        held_item = {'id': 'held', 'name': 'Key', 'description': 'Iron.',
                     'status': 'available', 'holder': {'type': 'npc', 'id': npc['id']}}
        self.state['world']['items'].append(held_item)
        self.execute('destroy_npc', npc_id=npc['id'])
        self.assertEqual(self.state['world']['npcs'][0]['status'], 'dead')
        self.assertEqual(held_item['holder'], {'type': 'location', 'id': tavern})

    def test_creation_reuses_semantic_match_and_retains_holder(self):
        original_item = {'id': 'key', 'name': 'Iron key', 'description': 'Iron.',
                         'holder': {'type': 'player', 'id': '1'}, 'status': 'available'}
        self.state['world']['items'].append(original_item)
        self.comparator.run.return_value.data = {'outcome': 'existing', 'entity_id': 'key'}
        result = self.execute('create_item', name='rusty key', description='Old iron key.',
                              location_id=self.state['currentLocationId'])
        self.assertFalse(result['created'])
        self.assertEqual(result['entity']['holder'], original_item['holder'])
        self.assertEqual(len(self.state['world']['items']), 1)
        self.comparator.run.assert_called_once()
        self.assertEqual([event['component'] for event in self.events],
                         ['tool', 'comparator', 'comparator', 'comparator', 'tool'])
        self.assertEqual(self.events[1]['parent_operation_id'], self.events[0]['operation_id'])

    def test_repeated_creation_uses_draft_and_exact_match(self):
        origin = self.state['currentLocationId']
        first = self.execute('create_item', name='Key', description='Iron.', location_id=origin)
        second = self.execute('create_item', name='Key', description='Iron.', location_id=origin)
        self.assertEqual(first['entity']['id'], second['entity']['id'])
        self.assertFalse(second['created'])
        self.comparator.run.assert_not_called()

    def test_offered_tools_and_id_enums_follow_draft_state(self):
        definitions = copy.deepcopy(self.master.tools)
        initial = self.tools.available_tools(definitions)
        self.assertEqual({tool['function']['name'] for tool in initial},
                         {'move_to_new_location', 'create_item', 'create_npc'})
        item = self.execute('create_item', name='Key', description='Iron.',
                            location_id=self.state['currentLocationId'])['entity']
        available = {tool['function']['name']: tool['function']['parameters']
                     for tool in self.tools.available_tools(definitions)}
        self.assertEqual(available['pick_up_item']['properties']['item_id']['enum'], [item['id']])
        self.assertNotIn('drop_item', available)
        self.execute('pick_up_item', item_id=item['id'])
        available = {tool['function']['name']: tool['function']['parameters']
                     for tool in self.tools.available_tools(definitions)}
        self.assertNotIn('pick_up_item', available)
        self.assertEqual(available['drop_item']['properties']['item_id']['enum'], [item['id']])
        self.assertEqual(self.master.tools, definitions)

    def test_invalid_tools_return_errors_without_mutation(self):
        origin = self.state['currentLocationId']
        self.state['world']['items'].append({
            'id': 'distant', 'name': 'Key', 'description': 'Iron.', 'status': 'available',
            'holder': {'type': 'location', 'id': 'elsewhere'},
        })
        for call in (tool('unknown'), tool('create_item', name='key'),
                     tool('create_item', name='Key', description='Iron.', location_id='missing'),
                     tool('pick_up_item', item_id='distant'), tool('destroy_item', item_id='distant'),
                     tool('drop_item', item_id='missing'),
                     tool('move_to_location', destination_id='missing'),
                     tool('create_item', name=7, description='Iron.', location_id=origin)):
            original = copy.deepcopy(self.state)
            with self.subTest(call=call):
                result = self.tools.execute(call)
                self.assertIn('error', result)
                self.assertEqual(self.state, original)

    def test_failed_movement_rolls_back_alias_and_blocked_route(self):
        origin = self.state['world']['locations'][0]
        destination = {'id': 'tavern', 'name': 'Tavern', 'description': 'A tavern.',
                       'aliases': [], 'visited': False, 'exits': []}
        self.state['world']['locations'].append(destination)
        origin['exits'].append({'id': 'blocked', 'destinationId': 'tavern',
                                'description': 'Locked door', 'traversable': False})
        self.comparator.run.return_value.data = {'outcome': 'existing', 'entity_id': 'tavern'}
        original = copy.deepcopy(self.state)
        result = self.execute('move_to_new_location', name='the inn', description='A tavern.')
        self.assertIn('error', result)
        self.assertEqual(self.state, original)

    def test_loop_returns_tool_results_then_story_without_summary_delay(self):
        original = copy.deepcopy(self.state)
        requests = []

        def respond(messages, tools):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                return reply(calls=[tool('create_item', name='Key', description='Iron.',
                                         location_id=self.state['currentLocationId'])])
            if len(requests) == 2:
                outcome = json.loads(next(message for message in reversed(messages)
                                          if message['role'] == 'tool')['content'])
                return reply(calls=[tool('pick_up_item', item_id=outcome['entity']['id'])])
            return reply('You pick up the key.')

        self.master.transport = respond
        updated, result = process_tool_turn(
            self.state, 'Take a key.', call=self.master, comparator=self.comparator,
            on_progress=self.events.append)
        self.assertEqual(self.state, original)
        self.assertEqual(len(requests), 3)
        tool_message = next(message for message in requests[1] if message['role'] == 'tool')
        self.assertEqual(tool_message['tool_call_id'], 'create_item')
        self.assertIn('Tool execution checkpoint', requests[1][-1]['content'])
        self.assertEqual(updated['world']['items'][0]['holder'], {'type': 'player', 'id': '1'})
        self.assertEqual(result['narration'], 'You pick up the key.')
        self.assertNotIn('summaryEventCount', updated['history'])
        self.assertFalse(any(event['component'] == 'summarizer' for event in self.events))
        context = json.loads(requests[0][-1]['content'])
        self.assertEqual(context['player_action'], 'Take a key.')
        self.assertIn('items_here', context)
        self.assertIn('npcs_here', context)
        self.assertEqual(requests[0][1]['content'], original['history']['recentEvents'][0]['content'])

    def test_tool_rejection_is_visible_to_game_master(self):
        self.master.transport.side_effect = [
            reply(calls=[tool('drop_item', item_id='unknown')]), reply('You have no such item.'),
        ]
        updated, result = process_tool_turn(self.state, 'Drop a key.', call=self.master)
        messages = self.master.transport.call_args[0][0]
        self.assertIn('error', json.loads(next(message for message in messages
                                             if message['role'] == 'tool')['content']))
        self.assertEqual(updated['world'], self.state['world'])
        self.assertIn('error', result['tool_results'][0]['result'])

    def test_provider_failure_after_tool_does_not_mutate_original(self):
        original = copy.deepcopy(self.state)
        self.master.transport.side_effect = [
            reply(calls=[tool('create_item', name='Key', description='Iron.',
                              location_id=self.state['currentLocationId'])]),
            LLMError('offline'),
        ]
        with self.assertRaises(LLMError):
            process_tool_turn(self.state, 'Take a key.', call=self.master)
        self.assertEqual(self.state, original)

    def test_story_returns_without_running_summarizer(self):
        self.master.transport.return_value = reply('You wait.')
        with patch('backend.llm_calls.gameplay.HistorySummarizerCall.run',
                   side_effect=AssertionError('Summarizer must not delay story')) as summarize:
            updated, result = process_tool_turn(self.state, 'Wait.', call=self.master)
        summarize.assert_not_called()
        self.assertEqual(result['narration'], 'You wait.')
        self.assertEqual(len(updated['history']['recentEvents']), 3)

    def test_empty_final_and_limits_leave_original_unchanged(self):
        original = copy.deepcopy(self.state)
        for responses in (
                [reply()],
                [reply('{"name":"drop_item","parameters":{"item_id":"key"}}')],
                [reply(calls=[tool('drop_item', item_id='missing')])] * 2):
            self.master.transport.side_effect = responses
            with self.subTest(responses=responses), self.assertRaises(LLMError):
                process_tool_turn(self.state, 'Wait.', call=self.master)
            self.assertEqual(self.state, original)
        with patch('backend.tool_mode.MAX_ROUNDS', 1):
            self.master.transport.side_effect = [reply(calls=[tool('drop_item', item_id='missing')])]
            with self.assertRaisesRegex(LLMError, 'round limit'):
                process_tool_turn(self.state, 'Wait.', call=self.master)
        with patch('backend.tool_mode.MAX_TOOL_CALLS', 0):
            self.master.transport.side_effect = [reply(calls=[tool('drop_item', item_id='missing')])]
            with self.assertRaisesRegex(LLMError, 'tool-call limit'):
                process_tool_turn(self.state, 'Wait.', call=self.master)
        self.assertEqual(self.state, original)
