import copy
import json
import threading
import unittest
from types import SimpleNamespace

from backend.game_engine import InvalidTurnResponse, new_game, process_turn
from backend.llm_calls.gameplay import ComparatorCall, NarratorCall
from backend.llm_provider import LLMError


def narrator_response(notable=None, narration='You look around.', game_over=False):
    return {
        'narration': narration,
        'action_succeeded': True,
        'game_over': game_over,
        'notable_elements': notable or {'items': [], 'locations': [], 'npcs': []},
    }


class FakeCall:
    def __init__(self, data):
        self.data = data
        self.inputs = []

    def run(self, user_input):
        self.inputs.append(user_input)
        return SimpleNamespace(data=self.data, provider='test', model='test-model')


class MatchingComparator(FakeCall):
    def __init__(self):
        super().__init__({})

    def run(self, user_input):
        self.inputs.append(user_input)
        context = json.loads(user_input)
        proposed = context['proposed_entity']
        present = any(
            entity['name'].casefold() == proposed['name'].casefold()
            or entity['description'].casefold() == proposed['description'].casefold()
            for entity in context['existing_entities']
        )
        self.data = {'already_present': present}
        return SimpleNamespace(data=self.data, provider='test', model='test-model')


def fake_calls(notable=None, narration='You look around.', game_over=False):
    return {
        'inventory': FakeCall({'items': []}),
        'map': FakeCall({'locations': []}),
        'npcs': FakeCall({'npcs': []}),
        'narrator': FakeCall(narrator_response(notable, narration, game_over)),
        'comparator': MatchingComparator(),
    }


def contains_key(value, key):
    if isinstance(value, dict):
        return key in value or any(contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, key) for item in value)
    return False


class GameEngineTests(unittest.TestCase):
    def test_new_game_has_simple_exchange_history(self):
        game = new_game(7)
        self.assertEqual(game['history'], {
            'recentEvents': [{
                'kind': 'narration',
                'content': 'You stand in the town square outside the tavern. The tavern door is nearby, and the square is open around you.',
            }],
        })
        self.assertEqual(game['world']['locations'][0]['id'], game['currentLocationId'])
        self.assertEqual(game['world']['items'], [])

    def test_turn_reconciles_new_entities_into_their_collections(self):
        game = new_game(7)
        calls = fake_calls({
            'items': [{'name': 'Iron key', 'description': 'A small iron key.'}],
            'locations': [{'name': 'Tavern', 'description': 'The town tavern.'}],
            'npcs': [{'name': 'Mira', 'description': 'The tavern keeper.'}],
        }, narration='Mira hands you a key as you enter the tavern.')
        original = copy.deepcopy(game)

        updated, result = process_turn(game, 'Enter the tavern and take the key.', calls)

        self.assertEqual(game, original)
        self.assertEqual(len(calls['comparator'].inputs), 3)
        for encoded in calls['comparator'].inputs:
            comparator_context = json.loads(encoded)
            self.assertEqual(
                set(comparator_context),
                {'entity_type', 'existing_entities', 'proposed_entity'},
            )
            self.assertFalse(contains_key(comparator_context, 'id'))
            self.assertFalse(contains_key(comparator_context, 'status'))
        item = updated['world']['items'][0]
        self.assertEqual(item['name'], 'Iron key')
        self.assertEqual(item['holder'], {'type': 'player', 'id': game['playerId']})
        self.assertEqual(item['status'], 'available')
        location = updated['world']['locations'][1]
        self.assertEqual(location['description'], 'The town tavern.')
        self.assertFalse(location['visited'])
        self.assertEqual(location['exits'], [])
        npc = updated['world']['npcs'][0]
        self.assertEqual(npc['currentLocationId'], game['currentLocationId'])
        self.assertEqual(npc['status'], 'active')
        self.assertEqual(updated['currentLocationId'], game['currentLocationId'])
        self.assertEqual(result['narration'], 'Mira hands you a key as you enter the tavern.')
        self.assertEqual(updated['history']['recentEvents'][-2:], [
            {'kind': 'player_action', 'content': 'Enter the tavern and take the key.'},
            {'kind': 'narration', 'content': result['narration']},
        ])

    def test_comparator_match_preserves_existing_entities_without_changes(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'key-1', 'name': 'Iron key', 'description': 'A small iron key.',
            'aliases': ['old key'], 'knownByPlayer': False,
            'holder': {'type': 'location', 'id': game['currentLocationId']},
            'status': 'available',
        })
        game['world']['locations'].append({
            'id': 'tavern-1', 'name': 'Tavern', 'description': 'The town tavern.',
            'aliases': [], 'visited': False, 'exits': [],
        })
        game['world']['npcs'].append({
            'id': 'mira-1', 'name': 'Mira', 'description': 'The tavern keeper.',
            'aliases': [], 'currentLocationId': None, 'status': 'offstage', 'state': {},
        })
        calls = fake_calls({
            'items': [{'name': 'Iron key', 'description': 'A small iron key.'}],
            'locations': [{'name': 'Tavern', 'description': 'The town tavern.'}],
            'npcs': [{'name': 'Mira', 'description': 'The tavern keeper.'}],
        })
        original = copy.deepcopy(game)

        updated, _ = process_turn(game, 'Mention the key, tavern, and Mira.', calls)

        self.assertEqual(updated['world'], original['world'])
        self.assertEqual(updated['currentLocationId'], original['currentLocationId'])

    def test_llm_contexts_contain_natural_language_not_internal_state(self):
        game = new_game(7)
        game['world']['locations'][0]['exits'].append({
            'id': 'secret-exit-id', 'destinationId': None,
            'description': 'A narrow passage', 'traversable': False,
        })
        game['world']['npcs'].append({
            'id': 'npc-id', 'name': 'Mira', 'description': 'A keeper.',
            'aliases': [], 'currentLocationId': game['currentLocationId'],
            'status': 'active', 'state': {},
        })
        calls = fake_calls({
            'items': [{'name': 'New key', 'description': 'A brass key.'}],
            'locations': [], 'npcs': [],
        })

        process_turn(game, 'Look around.', calls)

        for call in (calls['inventory'], calls['map'], calls['npcs'], calls['narrator']):
            payload = json.loads(call.inputs[0])
            self.assertFalse(contains_key(payload, 'id'))
            self.assertFalse(contains_key(payload, 'status'))
            self.assertFalse(contains_key(payload, 'traversable'))
        comparator_payload = json.loads(calls['comparator'].inputs[0])
        self.assertFalse(contains_key(comparator_payload, 'id'))
        self.assertFalse(contains_key(comparator_payload, 'status'))

    def test_manager_inputs_are_domain_scoped_and_history_is_bounded(self):
        game = new_game(7)
        location_id = game['currentLocationId']
        game['world']['items'].extend([
            {
                'id': 'local-item', 'name': 'Iron key', 'description': 'A small key.',
                'status': 'available', 'holder': {'type': 'location', 'id': location_id},
            },
            {
                'id': 'other-item', 'name': 'Distant crown', 'description': 'A gold crown.',
                'status': 'available', 'holder': {'type': 'location', 'id': 'elsewhere'},
            },
        ])
        game['world']['objects'].append({
            'id': 'object-id', 'name': 'Stone well', 'description': 'A deep well.',
            'status': 'present', 'knownByPlayer': True, 'locationId': location_id,
        })
        game['world']['npcs'].append({
            'id': 'npc-id', 'name': 'Mira', 'description': 'A keeper.',
            'aliases': [], 'currentLocationId': location_id, 'status': 'active', 'state': {},
        })
        game['history']['recentEvents'] = [
            {'kind': 'player_action', 'content': 'Old action {}.'.format(index)}
            for index in range(10)
        ]
        game['history']['recentEvents'][-1]['content'] = 'x' * 600
        calls = fake_calls()
        expected_manager_results = {
            'inventory': {'items': [{
                'name': 'Iron key', 'description': 'A small key.', 'interaction': 'use',
            }]},
            'map': {'locations': [{
                'name': 'Town Square', 'description': 'The town square.',
                'interaction': 'remain',
            }]},
            'npcs': {'npcs': [{
                'name': 'Mira', 'description': 'The tavern keeper.', 'interaction': 'speak',
            }]},
        }
        for name, result in expected_manager_results.items():
            calls[name].data = result

        _, turn_result = process_turn(game, 'Use the key.', calls)

        manager_inputs = {
            name: json.loads(calls[name].inputs[0])
            for name in ('inventory', 'map', 'npcs')
        }
        inventory = manager_inputs['inventory']
        self.assertEqual(set(inventory), {
            'action', 'current_location', 'carried_items', 'items_here',
            'objects_here', 'recent_history',
        })
        self.assertEqual([item['name'] for item in inventory['items_here']], ['Iron key'])
        self.assertEqual([item['name'] for item in inventory['objects_here']], ['Stone well'])
        self.assertNotIn('Mira', json.dumps(inventory))
        self.assertNotIn('Distant crown', json.dumps(inventory))

        self.assertEqual(set(manager_inputs['map']), {
            'action', 'current_location', 'exits', 'known_locations', 'recent_history',
        })
        self.assertNotIn('Iron key', json.dumps(manager_inputs['map']))
        self.assertNotIn('Mira', json.dumps(manager_inputs['map']))
        self.assertEqual(set(manager_inputs['npcs']), {
            'action', 'current_location', 'npcs_here', 'recent_history',
        })
        self.assertEqual(
            [npc['name'] for npc in manager_inputs['npcs']['npcs_here']], ['Mira'])
        self.assertNotIn('Iron key', json.dumps(manager_inputs['npcs']))
        for manager_input in manager_inputs.values():
            self.assertEqual(len(manager_input['recent_history']), 2)
            self.assertEqual(len(manager_input['recent_history'][-1]['content']), 500)

        narrator_input = json.loads(calls['narrator'].inputs[0])
        self.assertEqual(narrator_input['manager_results'], expected_manager_results)
        self.assertEqual(turn_result['manager_results'], expected_manager_results)

    def test_invalid_narrator_entity_contract_is_rejected(self):
        narrator = NarratorCall(transport=lambda *args: {})
        valid = narrator_response()
        invalid_responses = [
            dict(valid, state_changes=[]),
            dict(valid, notable_elements={'items': [], 'locations': []}),
            dict(valid, notable_elements={
                'items': [{'name': 'Key', 'description': 'A key', 'id': 'forbidden'}],
                'locations': [], 'npcs': [],
            }),
            dict(valid, game_over='no'),
        ]
        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(LLMError):
                narrator.validate_response(response)

    def test_comparator_requires_a_boolean_identity_result(self):
        comparator = ComparatorCall(transport=lambda *args: {})
        for response in ({}, {'already_present': 'yes'}, {'already_present': False, 'id': 'forbidden'}):
            with self.subTest(response=response), self.assertRaises(LLMError):
                comparator.validate_response(response)

    def test_invalid_comparator_result_does_not_mutate_game(self):
        game = new_game(7)
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'An iron key.'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'already_present': 'yes'})
        original = copy.deepcopy(game)

        with self.assertRaisesRegex(InvalidTurnResponse, 'must be a boolean'):
            process_turn(game, 'Find a key.', calls)

        self.assertEqual(game, original)

    def test_manager_calls_run_in_parallel_and_report_progress(self):
        calls = fake_calls()
        release = threading.Event()
        inventory_waiting = threading.Event()

        class SlowInventory(FakeCall):
            def run(self, user_input):
                inventory_waiting.set()
                if not release.wait(5):
                    raise AssertionError('Progress did not release the slower manager.')
                return super().run(user_input)

        calls['inventory'] = SlowInventory({'items': []})
        events = []

        def on_progress(event):
            events.append(event)
            if event['component'] == 'map' and event['status'] == 'completed':
                self.assertTrue(inventory_waiting.wait(2))
                release.set()

        process_turn(new_game(7), 'Look around.', calls, on_progress=on_progress)
        completed = [event['component'] for event in events if event['status'] == 'completed']
        self.assertLess(completed.index('map'), completed.index('inventory'))
        self.assertEqual(set(completed), {'inventory', 'map', 'npcs', 'narrator'})

    def test_legacy_summary_is_removed_and_history_is_not_compacted(self):
        game = new_game(7)
        game['history']['summary'] = 'Old summary.'
        game['history']['recentEvents'] = [
            {'kind': 'narration', 'content': f'event {index}'}
            for index in range(30)
        ]

        updated, _ = process_turn(game, 'Look around.', fake_calls())

        self.assertNotIn('summary', updated['history'])
        self.assertEqual(len(updated['history']['recentEvents']), 32)
        self.assertEqual(game['history']['summary'], 'Old summary.')

    def test_game_over_updates_game_status(self):
        updated, result = process_turn(
            new_game(7), 'I die.', fake_calls(game_over=True))
        self.assertEqual(updated['status'], 'ended')
        self.assertEqual(updated['endReason'], 'character_died')
        self.assertTrue(result['game_over'])

    def test_failed_llm_step_does_not_mutate_game(self):
        game = new_game(7)
        original = copy.deepcopy(game)
        calls = fake_calls()

        class BrokenCall:
            def run(self, user_input):
                raise LLMError('provider unavailable')

        calls['npcs'] = BrokenCall()
        with self.assertRaisesRegex(LLMError, 'provider unavailable'):
            process_turn(game, 'Talk to Mira.', calls)
        self.assertEqual(game, original)


if __name__ == '__main__':
    unittest.main()
