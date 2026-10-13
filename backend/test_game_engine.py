import copy
import json
import threading
import unittest
from types import SimpleNamespace

from backend.game_engine import InvalidProposal, apply_state_changes, new_game, process_turn
from backend.llm_calls.gameplay import NarratorCall
from backend.llm_provider import LLMError


class FakeCall:
    def __init__(self, data):
        self.data = data
        self.inputs = []

    def run(self, user_input):
        self.inputs.append(user_input)
        return SimpleNamespace(data=self.data, provider='test', model='test-model')


def fake_calls(state_changes=None, narration='The door swings open.', summarize=None):
    return {
        'inventory': FakeCall({'assessment': 'No relevant item.'}),
        'map': FakeCall({'assessment': 'The player approaches a door.'}),
        'npcs': FakeCall({'assessment': 'No NPC is involved.'}),
        'narrator': FakeCall({
            'narration': narration,
            'action_succeeded': True,
            'game_over': False,
            'state_changes': state_changes or [],
        }),
        'summarizer': FakeCall({'summary': summarize or 'The player is exploring the town.'}),
    }


class GameEngineTests(unittest.TestCase):
    def test_duplicate_exit_additions_preserve_existing_exit(self):
        game = new_game(7)
        game = apply_state_changes(game, [
            {'type': 'create_location', 'payload': {'name': 'Tavern'}},
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current', 'destination_location_id': 'Tavern',
                'description': 'Locked tavern door', 'traversable': False,
            }},
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current', 'destination_location_id': None,
                'description': 'Side passage', 'traversable': False,
            }},
        ])
        updated = apply_state_changes(game, [
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current',
                'destination_location_id': game['world']['locations'][1]['id'],
                'description': 'Open tavern door', 'traversable': True,
            }},
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current', 'destination_location_id': None,
                'description': 'side PASSAGE', 'traversable': True,
            }},
        ])
        self.assertEqual(updated, game)
        distinct = apply_state_changes(game, [{
            'type': 'add_exit', 'payload': {
                'from_location_id': 'current', 'destination_location_id': None,
                'description': 'Cellar stairs',
            },
        }])
        self.assertEqual(len(distinct['world']['locations'][0]['exits']), 3)

    def test_correction_cannot_erase_beer_creation_and_repeat_successful_narration(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        calls = fake_calls()
        first = dict(calls['narrator'].data,
                     narration='The bartender hands you a beer.',
                     state_changes=[
                         {'type': 'create_item', 'payload': {'name': 'Beer', 'holder_type': 'player'}},
                         {'type': 'update_npc', 'payload': {'npc_id': None, 'status': 'active'}},
                     ])
        requests = []
        corrected = dict(first, state_changes=[])

        def transport(messages, schema, schema_name):
            requests.append(messages)
            return {'provider': 'test', 'model': 'test',
                    'response': json.dumps(first if len(requests) == 1 else corrected)}

        calls['narrator'] = NarratorCall(transport=transport)
        with self.assertRaisesRegex(InvalidProposal, 'discarded all state changes'):
            process_turn(game, 'Order a beer.', calls)
        self.assertEqual(game, original)
        self.assertEqual(len(requests), 2)
        self.assertIn('operation 2 (update_npc)', requests[1][-1]['content'])

    def test_manager_progress_arrives_before_slower_manager_finishes(self):
        calls = fake_calls()
        release = threading.Event()
        inventory_waiting = threading.Event()

        class SlowInventory(FakeCall):
            def run(self, user_input):
                inventory_waiting.set()
                if not release.wait(5):
                    raise AssertionError('Progress did not release the slower manager.')
                return super().run(user_input)

        calls['inventory'] = SlowInventory({'assessment': 'Inventory assessed.'})
        events = []

        def on_progress(event):
            events.append(event)
            if event['component'] == 'map' and event['status'] == 'completed':
                self.assertTrue(inventory_waiting.wait(2))
                self.assertFalse(release.is_set())
                release.set()

        process_turn(new_game(7), 'Look around.', calls, on_progress=on_progress)
        completed = [event['component'] for event in events if event['status'] == 'completed']
        self.assertLess(completed.index('map'), completed.index('inventory'))
        self.assertEqual(set(completed), {'inventory', 'map', 'npcs', 'narrator'})
        assessment = next(event for event in events
                          if event['component'] == 'inventory' and event['status'] == 'completed')
        self.assertEqual(assessment['response'], {'assessment': 'Inventory assessed.'})
        self.assertEqual(assessment['model'], 'test-model')

    def test_ordered_beer_is_created_in_inventory_after_correction(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        calls = fake_calls()
        rejected = dict(calls['narrator'].data, state_changes=[
            {'type': 'create_location', 'payload': {'name': 'Beer', 'aliases': ['a mug of beer']}},
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current', 'destination_location_id': None,
                'description': 'The bar', 'traversable': True,
            }},
            {'type': 'transfer_item', 'payload': {
                'item_id': 'Beer', 'current_location_id': game['currentLocationId'], 'npc_id': 'barker',
            }},
            {'type': 'set_item_status', 'payload': {'item_id': 'Beer', 'status': 'consumed'}},
        ])
        corrected = dict(calls['narrator'].data,
                         narration='The bartender hands you a mug of beer.',
                         state_changes=[{
                             'type': 'create_item', 'payload': {
                                 'name': 'Beer', 'aliases': ['a mug of beer'],
                                 'holder_type': 'player', 'known_by_player': True,
                             },
                         }])
        requests = []

        def transport(messages, schema, schema_name):
            requests.append(messages)
            return {'provider': 'test', 'model': 'test', 'response': json.dumps(
                rejected if len(requests) == 1 else corrected)}

        calls['narrator'] = NarratorCall(transport=transport)
        updated, result = process_turn(game, 'Order a beer.', calls)
        self.assertEqual(len(requests), 2)
        feedback = json.loads(requests[1][-1]['content'].split('\n', 1)[1])
        self.assertIn('operation 3 (transfer_item)', feedback['validation_error'])
        self.assertIn('holder_type', feedback['validation_error'])
        self.assertEqual(feedback['rejected_response'], rejected)
        self.assertEqual(game, original)
        self.assertEqual(updated['world']['locations'], original['world']['locations'])
        beer = updated['world']['items'][0]
        self.assertEqual(beer['name'], 'Beer')
        self.assertEqual(beer['holder'], {'type': 'player', 'id': game['playerId']})
        self.assertEqual(beer['status'], 'available')
        self.assertTrue(beer['knownByPlayer'])
        self.assertEqual(result['narration'], corrected['narration'])
        next_calls = fake_calls()
        process_turn(updated, 'Look at my inventory.', next_calls)
        self.assertEqual(json.loads(next_calls['inventory'].inputs[0])['inventory'], [beer])

    def test_narrator_enforces_operation_specific_payload_contracts(self):
        narrator = NarratorCall(transport=lambda *args: {})
        for operation in (
            {'type': 'transfer_item', 'payload': {'item_id': 'Beer'}},
            {'type': 'transfer_item', 'payload': {
                'item_id': 'Beer', 'holder_type': 'player', 'npc_id': 'barker',
            }},
            {'type': 'create_item', 'payload': {'name': 'Beer'}},
            {'type': 'set_item_status', 'payload': {'item_id': 'Beer', 'status': 'active'}},
            {'type': 'create_location', 'payload': {'name': 'Beer', 'holder_type': 'player'}},
            {'type': 'move_player', 'payload': {'destination_location_id': None}},
            {'type': 'set_exit_traversable', 'payload': {'location_id': 'current', 'traversable': True}},
            {'type': 'invent_operation', 'payload': {}},
        ):
            response = dict(fake_calls()['narrator'].data, state_changes=[operation])
            with self.subTest(operation=operation), self.assertRaises(LLMError):
                narrator.validate_response(response)
        for variant in narrator.response_format['properties']['state_changes']['items']['anyOf']:
            kind = variant['properties']['type']['enum'][0]
            contract = variant['properties']['payload']
            examples = {
                'name': 'Beer', 'from_location_id': 'current',
                'destination_location_id': 'Tavern', 'description': 'Door',
                'location_id': 'current', 'traversable': True,
                'holder_type': 'player', 'item_id': 'Beer', 'status': 'consumed',
                'object_id': 'bar', 'current_location_id': 'current', 'npc_id': 'bartender',
            }
            payload = {key: examples[key] for key in contract['required']}
            if kind == 'set_exit_traversable':
                payload['exit_id'] = 'door'
            response = dict(fake_calls()['narrator'].data,
                            state_changes=[{'type': kind, 'payload': payload}])
            with self.subTest(kind=kind):
                self.assertEqual(narrator.validate_response(response), response)

    def test_retries_logged_malformed_response_with_object_payloads(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        calls = fake_calls()
        malformed = dict(calls['narrator'].data, state_changes=[{
            'type': 'add_exit',
            'payload': '{"from_location_id":"town-square-outside-tavern","destination_location_id":null}"',
        }])
        corrected = dict(calls['narrator'].data, state_changes=[
            {'type': 'create_location', 'payload': {'name': 'Tavern Interior'}},
            {'type': 'add_exit', 'payload': {
                'from_location_id': 'current',
                'destination_location_id': 'Tavern Interior',
                'description': 'Tavern door',
                'traversable': True,
            }},
            {'type': 'move_player', 'payload': {'destination_location_id': 'Tavern Interior'}},
        ])
        requests = []

        def transport(messages, schema, schema_name):
            requests.append(messages)
            self.assertEqual(
                schema['properties']['state_changes']['items']['properties']['payload']['type'],
                'object',
            )
            return {'provider': 'test', 'model': 'test', 'response': json.dumps(
                malformed if len(requests) == 1 else corrected)}

        calls['narrator'] = NarratorCall(transport=transport)
        updated, _ = process_turn(game, 'Dismount my horse and enter the tavern.', calls)
        self.assertEqual(len(requests), 2)
        self.assertIn('validation_error', requests[1][-1]['content'])
        self.assertIn('JSON object, not a string', requests[1][-1]['content'])
        self.assertNotEqual(updated['currentLocationId'], game['currentLocationId'])
        self.assertEqual(game, original)
        self.assertEqual(len(calls['map'].inputs), 1)

    def test_retries_semantic_error_and_rejects_second_invalid_proposal(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        calls = fake_calls([{
            'type': 'update_npc', 'payload': {'npc_id': 'horse', 'status': 'offstage'},
        }])
        with self.assertRaisesRegex(InvalidProposal, 'unknown'):
            process_turn(game, 'Dismount.', calls)
        self.assertEqual(len(calls['narrator'].inputs), 2)
        correction = json.loads(calls['narrator'].inputs[1])
        self.assertIn('horse', correction['validation_error'])
        self.assertEqual(correction['rejected_response'], calls['narrator'].data)
        self.assertEqual(game, original)

    def test_provider_error_does_not_trigger_correction_retry(self):
        calls = fake_calls()

        class UnreachableCall(FakeCall):
            def run(self, user_input):
                self.inputs.append(user_input)
                raise LLMError('provider unavailable')

        narrator = UnreachableCall({})
        calls['narrator'] = narrator
        with self.assertRaisesRegex(LLMError, 'provider unavailable'):
            process_turn(new_game(7), 'Enter the tavern.', calls)
        self.assertEqual(len(narrator.inputs), 1)

    def test_narrator_rejects_null_state_and_string_payloads(self):
        narrator = NarratorCall(transport=lambda *args: {})
        for operation in (
            {'type': 'update_npc', 'payload': {'npc_id': 'horse', 'state': None}},
            {'type': 'move_player', 'payload': '{"destination_location_id":"Tavern"}'},
        ):
            response = dict(fake_calls()['narrator'].data, state_changes=[operation])
            with self.subTest(operation=operation), self.assertRaises(LLMError):
                narrator.validate_response(response)

    def test_new_game_has_fixed_start_and_empty_inventory(self):
        game = new_game(7)
        self.assertEqual(game['currentLocationId'], 'town-square-outside-tavern')
        self.assertEqual(game['status'], 'active')
        self.assertEqual(game['world']['items'], [])
        self.assertEqual(game['world']['locations'][0]['id'], game['currentLocationId'])

    def test_turn_creates_location_exit_and_moves_player(self):
        game = new_game(7)
        calls = fake_calls([
            {'type': 'create_location', 'payload': '{"name":"Tavern Interior"}'},
            {'type': 'add_exit', 'payload': json.dumps({
                'from_location_id': 'current',
                'destination_location_id': 'Tavern Interior',
                'description': 'The tavern door',
            })},
            {'type': 'move_player', 'payload': '{"destination_location_id":"Tavern Interior"}'},
        ])

        updated, result = process_turn(game, 'Enter the tavern.', calls)

        self.assertNotEqual(updated['currentLocationId'], game['currentLocationId'])
        self.assertTrue(updated['world']['locations'][1]['visited'])
        self.assertEqual(result['narration'], 'The door swings open.')
        self.assertEqual(len(game['world']['locations']), 1)
        self.assertEqual(len(calls['map'].inputs), 1)

    def test_player_cannot_move_through_missing_or_blocked_exit(self):
        game = new_game(7)
        with self.assertRaisesRegex(InvalidProposal, 'established, traversable exit'):
            apply_state_changes(game, [{
                'type': 'create_location', 'payload': '{"name":"Tavern Interior"}',
            }, {
                'type': 'move_player', 'payload': '{"destination_location_id":"Tavern Interior"}',
            }])
        self.assertEqual(len(game['world']['locations']), 1)

    def test_item_transfer_and_consumption_are_deterministic(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'potion-1', 'name': 'Healing potion', 'aliases': [],
            'knownByPlayer': True, 'holder': {'type': 'location', 'id': game['currentLocationId']},
            'status': 'available',
        })
        updated = apply_state_changes(game, [{
            'type': 'transfer_item',
            'payload': '{"item_id":"potion-1","holder_type":"player"}',
        }, {
            'type': 'set_item_status',
            'payload': '{"item_id":"potion-1","status":"consumed"}',
        }])
        self.assertEqual(updated['world']['items'][0]['status'], 'consumed')
        self.assertIsNone(updated['world']['items'][0]['holder'])
        self.assertEqual(game['world']['items'][0]['status'], 'available')

    def test_exit_traversability_controls_movement(self):
        game = new_game(7)
        game['world']['locations'].append({
            'id': 'tavern', 'name': 'Tavern', 'aliases': [], 'visited': False, 'exits': [],
        })
        game['world']['locations'][0]['exits'].append({
            'id': 'door', 'destinationId': 'tavern', 'description': 'Tavern door',
            'traversable': False, 'identified': True,
        })
        with self.assertRaisesRegex(InvalidProposal, 'traversable exit'):
            apply_state_changes(game, [{
                'type': 'move_player', 'payload': '{"destination_location_id":"tavern"}',
            }])
        updated = apply_state_changes(game, [{
            'type': 'set_exit_traversable',
            'payload': '{"location_id":"current","exit_id":"door","traversable":true}',
        }, {
            'type': 'move_player', 'payload': '{"destination_location_id":"tavern"}',
        }])
        self.assertEqual(updated['currentLocationId'], 'tavern')
        self.assertTrue(updated['world']['locations'][1]['visited'])

    def test_object_state_persists_and_npc_moves_then_leaves(self):
        game = new_game(7)
        game['world']['locations'].append({
            'id': 'alley', 'name': 'Alley', 'aliases': [], 'visited': False, 'exits': [],
        })
        updated = apply_state_changes(game, [{
            'type': 'create_object',
            'payload': '{"name":"Chest","location_id":"current","state":{"locked":true}}',
        }, {
            'type': 'update_object',
            'payload': '{"object_id":"Chest","state":{"locked":false}}',
        }, {
            'type': 'create_npc',
            'payload': '{"name":"Barkeep","current_location_id":"current"}',
        }, {
            'type': 'update_npc',
            'payload': '{"npc_id":"Barkeep","current_location_id":"alley"}',
        }, {
            'type': 'update_npc',
            'payload': '{"npc_id":"Barkeep","status":"offstage"}',
        }])
        self.assertEqual(updated['world']['objects'][0]['state']['locked'], False)
        self.assertEqual(updated['world']['npcs'][0]['status'], 'offstage')
        self.assertIsNone(updated['world']['npcs'][0]['currentLocationId'])

    def test_duplicate_entity_and_invalid_state_change_are_rejected(self):
        game = new_game(7)
        with self.assertRaisesRegex(InvalidProposal, 'duplicates'):
            apply_state_changes(game, [
                {'type': 'create_location', 'payload': '{"name":"Tavern"}'},
                {'type': 'create_location', 'payload': '{"name":"Tavern"}'},
            ])
        with self.assertRaisesRegex(InvalidProposal, 'duplicates'):
            apply_state_changes(game, [
                {'type': 'create_item', 'payload': '{"name":"Iron key","holder_type":"player"}'},
                {'type': 'create_item', 'payload': '{"name":"Hallway key","aliases":["Iron key"],"holder_type":"player"}'},
            ])
        with self.assertRaisesRegex(InvalidProposal, 'Unsupported'):
            apply_state_changes(game, [{'type': 'invent', 'payload': '{}'}])

    def test_logs_malformed_operation_payload_without_changing_state(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        operation = {'type': 'move_player', 'payload': 'not JSON'}
        with self.assertLogs('backend.game_engine', level='WARNING') as logs:
            with self.assertRaisesRegex(InvalidProposal, 'payload must be valid JSON'):
                apply_state_changes(game, [operation])
        self.assertIn('Rejected state change 1', logs.output[0])
        self.assertIn(game['id'], logs.output[0])
        self.assertIn('move_player', logs.output[0])
        self.assertIn('not JSON', logs.output[0])
        self.assertEqual(game, original)

    def test_failed_llm_step_does_not_mutate_persisted_state(self):
        game = new_game(7)

        class BrokenCall:
            def run(self, user_input):
                raise RuntimeError('provider unavailable')

        calls = fake_calls()
        calls['npcs'] = BrokenCall()
        original = copy.deepcopy(game)
        with self.assertRaises(RuntimeError):
            process_turn(game, 'Talk to the barkeep.', calls)
        self.assertEqual(game, original)

    def test_summarizes_old_history_and_keeps_recent_exchanges(self):
        game = new_game(7)
        game['history']['recentEvents'] = [
            {'kind': 'narration', 'content': f'event {index}'}
            for index in range(24)
        ]
        calls = fake_calls()

        updated, _ = process_turn(game, 'Look around.', calls)

        self.assertEqual(len(updated['history']['recentEvents']), 16)
        self.assertEqual(updated['history']['summary'], 'The player is exploring the town.')
        summary_input = json.loads(calls['summarizer'].inputs[0])
        self.assertEqual(len(summary_input['events_to_compact']), 10)


if __name__ == '__main__':
    unittest.main()
