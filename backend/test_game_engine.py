import copy
import json
import threading
import unittest
from types import SimpleNamespace

from backend.game_engine import InvalidTurnResponse, new_game, process_turn
from backend.llm_calls.gameplay import ComparatorCall, NarratorCall
from backend.llm_provider import LLMError


def narrator_response(narration='You look around.', game_over=False):
    return {
        'narration': narration,
        'action_succeeded': True,
        'game_over': game_over,
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
        entity = context['candidate_entity']
        self.data = {'same_entity': (
            entity['name'].casefold() == proposed['name'].casefold()
            or entity['description'].casefold() == proposed['description'].casefold()
        )}
        return SimpleNamespace(data=self.data, provider='test', model='test-model')


def fake_calls(notable=None, narration='You look around.', game_over=False):
    notable = notable or {'items': [], 'locations': [], 'npcs': []}
    return {
        'inventory': FakeCall({'items': notable['items']}),
        'map': FakeCall({'locations': notable['locations']}),
        'npcs': FakeCall({'npcs': notable['npcs']}),
        'narrator': FakeCall(narrator_response(narration, game_over)),
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
            'items': [{'name': 'Iron key', 'description': 'A small iron key.',
                       'interaction': 'take'}],
            'locations': [
                {'name': game['world']['locations'][0]['name'],
                 'description': game['world']['locations'][0]['description'],
                 'interaction': 'leave'},
                {'name': 'Tavern', 'description': 'The town tavern.', 'interaction': 'enter'},
            ],
            'npcs': [{'name': 'Mira', 'description': 'The tavern keeper.',
                      'interaction': 'encounter'}],
        }, narration='Mira hands you a key as you enter the tavern.')
        original = copy.deepcopy(game)

        updated, result = process_turn(game, 'Enter the tavern and take the key.', calls)

        self.assertEqual(game, original)
        self.assertEqual(len(calls['comparator'].inputs), 0)
        for encoded in calls['comparator'].inputs:
            comparator_context = json.loads(encoded)
            self.assertEqual(
                set(comparator_context),
                {'entity_type', 'candidate_entity', 'proposed_entity'},
            )
            self.assertFalse(contains_key(comparator_context, 'id'))
            self.assertFalse(contains_key(comparator_context, 'status'))
        item = updated['world']['items'][0]
        self.assertEqual(item['name'], 'Iron key')
        self.assertEqual(item['holder'], {'type': 'player', 'id': game['playerId']})
        self.assertEqual(item['status'], 'available')
        location = updated['world']['locations'][1]
        self.assertEqual(location['description'], 'The town tavern.')
        self.assertTrue(location['visited'])
        self.assertEqual(location['exits'][0]['destinationId'], game['currentLocationId'])
        npc = updated['world']['npcs'][0]
        self.assertEqual(npc['currentLocationId'], location['id'])
        self.assertEqual(npc['status'], 'active')
        self.assertEqual(updated['currentLocationId'], location['id'])
        self.assertEqual(result['reconciled_entities'], {
            'items': [{'id': item['id'], 'name': item['name'],
                       'description': item['description']}],
            'locations': [
                {'id': game['currentLocationId'],
                 'name': game['world']['locations'][0]['name'],
                 'description': game['world']['locations'][0]['description']},
                {'id': location['id'], 'name': location['name'],
                 'description': location['description']},
            ],
            'npcs': [{'id': npc['id'], 'name': npc['name'],
                      'description': npc['description']}],
        })
        self.assertEqual(result['narration'], 'Mira hands you a key as you enter the tavern.')
        self.assertEqual(updated['history']['recentEvents'][-2:], [
            {'kind': 'player_action', 'content': 'Enter the tavern and take the key.'},
            {'kind': 'narration', 'content': result['narration']},
        ])

    def test_comparator_match_preserves_existing_entities_without_changes(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'key-1', 'name': 'Iron key', 'description': 'A small iron key.',
            'aliases': ['old key'], 'knownByPlayer': True,
            'holder': {'type': 'player', 'id': game['playerId']},
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
            'items': [{'name': 'Iron key', 'description': 'A small iron key.',
                       'interaction': 'take'}],
            'locations': [],
            'npcs': [{'name': 'Mira', 'description': 'The tavern keeper.',
                      'interaction': 'mentioned'}],
        })
        original = copy.deepcopy(game)

        updated, result = process_turn(game, 'Keep the key and mention Mira.', calls)

        self.assertEqual(updated['world'], original['world'])
        self.assertEqual(updated['currentLocationId'], original['currentLocationId'])
        self.assertEqual(result['reconciled_entities'], {
            'items': [{'id': 'key-1', 'name': 'Iron key', 'description': 'A small iron key.'}],
            'locations': [],
            'npcs': [{'id': 'mira-1', 'name': 'Mira', 'description': 'The tavern keeper.'}],
        })

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
            'items': [{'name': 'New key', 'description': 'A brass key.', 'interaction': 'take'}],
            'locations': [], 'npcs': [
                {'name': 'The keeper', 'description': 'A keeper.', 'interaction': 'mentioned'},
            ],
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
            'inventory': {'items': []},
            'map': {'locations': []},
            'npcs': {'npcs': []},
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
            'current_action', 'current_location', 'carried_items', 'items_here',
            'objects_here', 'completed_history',
        })
        self.assertEqual([item['name'] for item in inventory['items_here']], ['Iron key'])
        self.assertEqual([item['name'] for item in inventory['objects_here']], ['Stone well'])
        self.assertNotIn('Mira', json.dumps(inventory))
        self.assertNotIn('Distant crown', json.dumps(inventory))

        self.assertEqual(set(manager_inputs['map']), {
            'current_action', 'current_location', 'exits', 'known_locations', 'completed_history',
        })
        self.assertNotIn('Iron key', json.dumps(manager_inputs['map']))
        self.assertNotIn('Mira', json.dumps(manager_inputs['map']))
        self.assertEqual(set(manager_inputs['npcs']), {
            'current_action', 'current_location', 'npcs_here', 'completed_history',
        })
        self.assertEqual(
            [npc['name'] for npc in manager_inputs['npcs']['npcs_here']], ['Mira'])
        self.assertNotIn('Iron key', json.dumps(manager_inputs['npcs']))
        for manager_input in manager_inputs.values():
            self.assertEqual(manager_input['current_action'], 'Use the key.')
            self.assertEqual(len(manager_input['completed_history']), 2)
            self.assertEqual(len(manager_input['completed_history'][-1]['content']), 500)

        narrator_input = json.loads(calls['narrator'].inputs[0])
        self.assertEqual(narrator_input['current_action'], 'Use the key.')
        self.assertEqual(narrator_input['completed_history'], game['history']['recentEvents'])
        for field in ('game_context', 'resolved_context'):
            for forbidden in ('action', 'current_action', 'history', 'recent_history',
                              'completed_history'):
                self.assertNotIn(forbidden, narrator_input[field])
        self.assertNotIn('manager_results', narrator_input)
        self.assertEqual(narrator_input['resolved_interactions'], {
            'items': [], 'locations': [], 'npcs': [],
        })
        self.assertEqual(turn_result['manager_results'], expected_manager_results)

    def test_new_action_is_separate_from_completed_movement_history(self):
        game = new_game(7)
        game['history']['recentEvents'] = [
            {'kind': 'player_action', 'content': 'Go back outside'},
            {'kind': 'narration', 'content': 'You step out of the tavern into the square.'},
        ]
        current = game['world']['locations'][0]
        action = 'Head north, towards the Great Forest.'
        calls = fake_calls({
            'items': [],
            'locations': [
                {'name': current['name'], 'description': current['description'],
                 'interaction': 'leave'},
                {'name': 'Great Forest', 'description': 'The forest north of town.',
                 'interaction': 'enter'},
            ],
            'npcs': [],
        }, narration='You head north into the Great Forest.')
        previous = copy.deepcopy(game['history']['recentEvents'])

        updated, _ = process_turn(game, action, calls)

        for manager in ('inventory', 'map', 'npcs'):
            payload = json.loads(calls[manager].inputs[0])
            self.assertEqual(payload['current_action'], action)
            self.assertEqual(payload['completed_history'], previous)
        payload = json.loads(calls['narrator'].inputs[0])
        self.assertEqual(list(payload)[0], 'current_action')
        self.assertEqual(payload['current_action'], action)
        self.assertEqual(payload['completed_history'], previous)
        self.assertEqual(payload['game_context']['current_location']['name'], current['name'])
        self.assertEqual(payload['resolved_context']['current_location']['name'], 'Great Forest')
        for context_field in ('game_context', 'resolved_context'):
            self.assertFalse(contains_key(payload[context_field], 'history'))
            self.assertFalse(contains_key(payload[context_field], 'completed_history'))
            self.assertFalse(contains_key(payload[context_field], 'action'))
        self.assertEqual(updated['history']['recentEvents'][:-2], previous)
        self.assertEqual(updated['history']['recentEvents'][-2]['content'], action)

    def test_map_enter_leave_creates_bidirectional_exits_and_moves_player(self):
        game = new_game(7)
        calls = fake_calls()
        calls['map'].data = {'locations': [
            {
                'name': 'Town Square Outside the Tavern',
                'description': 'The town square outside the tavern.',
                'interaction': 'leave',
            },
            {
                'name': 'Tavern',
                'description': 'A cozy town tavern.',
                'interaction': 'enter',
            },
        ]}

        updated, result = process_turn(game, 'Enter the tavern.', calls)

        self.assertEqual(len(updated['world']['locations']), 2)
        square, tavern = updated['world']['locations']
        self.assertEqual(updated['currentLocationId'], tavern['id'])
        self.assertTrue(tavern['visited'])
        self.assertEqual(square['exits'][0]['destinationId'], tavern['id'])
        self.assertEqual(tavern['exits'][0]['destinationId'], square['id'])
        self.assertTrue(square['exits'][0]['traversable'])
        self.assertTrue(tavern['exits'][0]['traversable'])
        self.assertIn('Tavern', square['exits'][0]['description'])
        self.assertIn(square['name'], tavern['exits'][0]['description'])
        self.assertEqual(
            [entity['id'] for entity in result['reconciled_entities']['locations']],
            [square['id'], tavern['id']],
        )

    def test_engine_preserves_interactions_without_narrator_echo(self):
        game = new_game(7)
        calls = fake_calls()
        calls['inventory'].data = {'items': [
            {'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'},
        ]}
        current = game['world']['locations'][0]
        calls['map'].data = {'locations': [
            {'name': current['name'], 'description': current['description'],
             'interaction': 'leave'},
            {'name': 'Tavern', 'description': 'A warm tavern.', 'interaction': 'enter'},
        ]}
        calls['npcs'].data = {'npcs': [
            {'name': 'Mira', 'description': 'The keeper.', 'interaction': 'encounter'},
        ]}
        originals = copy.deepcopy({
            key: calls[key].data for key in ('inventory', 'map', 'npcs')
        })

        updated, result = process_turn(game, 'Enter the tavern and meet Mira.', calls)

        self.assertEqual(result['manager_results'], originals)
        self.assertEqual(result['effective_manager_results'], originals)
        self.assertNotIn('manager_overrides', result)
        self.assertEqual(result['notable_elements'], {
            'items': originals['inventory']['items'],
            'locations': originals['map']['locations'],
            'npcs': originals['npcs']['npcs'],
        })
        self.assertEqual(len(calls['comparator'].inputs), 0)
        self.assertEqual(updated['world']['items'][0]['name'], 'Key')
        self.assertEqual(updated['currentLocationId'], updated['world']['locations'][1]['id'])
        self.assertEqual(updated['world']['npcs'][0]['name'], 'Mira')
        self.assertEqual(updated['world']['npcs'][0]['currentLocationId'],
                         updated['currentLocationId'])
        self.assertEqual(result['reconciled_entities']['npcs'][0]['name'], 'Mira')

    def test_engine_corrects_reversed_map_labels_before_narration(self):
        game = new_game(7)
        calls = fake_calls(narration='You enter the tavern.')
        current = game['world']['locations'][0]
        calls['map'].data = {'locations': [
            {'name': current['name'], 'description': current['description'],
             'interaction': 'enter'},
            {'name': 'Tavern', 'description': 'A tavern.', 'interaction': 'leave'},
        ]}
        original = copy.deepcopy(game)
        original_map = copy.deepcopy(calls['map'].data)

        with self.assertLogs('backend.game_engine', level='WARNING') as logs:
            updated, result = process_turn(game, 'Enter the tavern.', calls)

        self.assertIn('Correcting reversed map interactions', logs.output[0])
        self.assertEqual(game, original)
        self.assertEqual(result['manager_results']['map'], original_map)
        self.assertEqual(calls['map'].data, original_map)
        locations = result['notable_elements']['locations']
        self.assertEqual([entry['interaction'] for entry in locations], ['leave', 'enter'])
        tavern = updated['world']['locations'][1]
        self.assertEqual(updated['currentLocationId'], tavern['id'])
        self.assertEqual(current['exits'], [])
        self.assertEqual(tavern['exits'][0]['destinationId'], current['id'])
        narrator_input = json.loads(calls['narrator'].inputs[0])
        self.assertEqual(narrator_input['resolved_context']['current_location']['name'], 'Tavern')
        self.assertEqual(narrator_input['resolved_interactions']['locations'], locations)

    def test_narrator_receives_resulting_inventory_and_context(self):
        game = new_game(7)
        calls = fake_calls()
        calls['inventory'].data = {'items': [
            {'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'},
        ]}

        updated, result = process_turn(game, 'Take the key.', calls)

        context = json.loads(calls['narrator'].inputs[0])
        self.assertEqual(context['game_context']['carried_items'], [])
        self.assertEqual(context['resolved_context']['carried_items'], [
            {'name': 'Key', 'description': 'An iron key.'},
        ])
        self.assertEqual(context['resolved_interactions'], result['notable_elements'])
        self.assertEqual(updated['world']['items'][0]['holder'],
                         {'type': 'player', 'id': game['playerId']})

    def test_narrator_failure_discards_resolved_candidate(self):
        game = new_game(7)
        calls = fake_calls()
        calls['inventory'].data = {'items': [
            {'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'},
        ]}

        class BrokenNarrator:
            def run(self, user_input):
                raise LLMError('narrator unavailable')

        calls['narrator'] = BrokenNarrator()
        original = copy.deepcopy(game)

        with self.assertRaisesRegex(LLMError, 'narrator unavailable'):
            process_turn(game, 'Take the key.', calls)

        self.assertEqual(game, original)
        self.assertEqual(len(calls['comparator'].inputs), 0)

    def test_map_leave_enter_matches_existing_location_before_moving(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        tavern = {
            'id': 'tavern-existing',
            'name': 'The Rusty Tankard',
            'description': 'A busy tavern.',
            'aliases': [],
            'visited': False,
            'exits': [],
        }
        game['world']['locations'].append(tavern)
        calls = fake_calls()
        calls['map'].data = {'locations': [
            {
                'name': current['name'], 'description': current['description'],
                'interaction': 'leave',
            },
            {
                'name': 'The Rusty Tankard', 'description': 'A tavern full of patrons.',
                'interaction': 'enter',
            },
        ]}

        updated, _ = process_turn(game, 'Go into the tavern.', calls)

        self.assertEqual(len(updated['world']['locations']), 2)
        self.assertEqual(updated['currentLocationId'], tavern['id'])
        self.assertEqual(current['id'], updated['world']['locations'][0]['id'])
        self.assertEqual(tavern['description'], 'A busy tavern.')
        self.assertTrue(updated['world']['locations'][1]['visited'])
        self.assertEqual(updated['world']['locations'][0]['exits'][0]['destinationId'], tavern['id'])
        self.assertEqual(updated['world']['locations'][1]['exits'][0]['destinationId'], current['id'])

    def test_map_transition_does_not_duplicate_existing_exits(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        tavern = {
            'id': 'tavern-existing',
            'name': 'Tavern',
            'description': 'A busy tavern.',
            'aliases': [],
            'visited': True,
            'exits': [{
                'id': 'exit-to-square',
                'destinationId': current['id'],
                'description': 'Back to the square',
                'traversable': True,
                'identified': True,
            }],
        }
        current['exits'].append({
            'id': 'exit-to-tavern',
            'destinationId': tavern['id'],
            'description': 'Into the tavern',
            'traversable': True,
            'identified': True,
        })
        game['world']['locations'].append(tavern)
        calls = fake_calls()
        calls['map'].data = {'locations': [
            {
                'name': current['name'], 'description': current['description'],
                'interaction': 'leave',
            },
            {'name': 'Tavern', 'description': 'A tavern.', 'interaction': 'enter'},
        ]}

        updated, _ = process_turn(game, 'Enter the tavern.', calls)

        self.assertEqual(len(updated['world']['locations'][0]['exits']), 1)
        self.assertEqual(len(updated['world']['locations'][1]['exits']), 1)
        self.assertEqual(updated['currentLocationId'], tavern['id'])

    def test_map_movement_requires_current_leave_and_single_enter(self):
        for locations in (
            [{'name': 'Tavern', 'description': 'A tavern.', 'interaction': 'enter'}],
            [
                {'name': 'Square', 'description': 'The square.', 'interaction': 'leave'},
                {'name': 'Tavern', 'description': 'A tavern.', 'interaction': 'enter'},
                {'name': 'Cellar', 'description': 'A cellar.', 'interaction': 'enter'},
            ],
        ):
            game = new_game(7)
            calls = fake_calls()
            calls['map'].data = {'locations': locations}
            original = copy.deepcopy(game)

            with self.subTest(locations=locations), self.assertRaises(InvalidTurnResponse):
                process_turn(game, 'Move.', calls)

            self.assertEqual(game, original)

    def test_map_removes_redundant_current_room_enter(self):
        for normalize in (False, True):
            game = new_game(7)
            current = game['world']['locations'][0]
            departure = {
                'name': current['name'], 'description': current['description'],
                'interaction': 'leave',
            }
            redundant = dict(departure, interaction='enter')
            if normalize:
                redundant['name'] = ' ' + redundant['name'].upper() + ' '
                redundant['description'] = ' ' + redundant['description'].upper() + ' '
            destination = {
                'name': 'Tavern', 'description': 'A cozy town tavern.',
                'interaction': 'enter',
            }
            calls = fake_calls()
            calls['map'].data = {'locations': [departure, redundant, destination]}
            original_map = copy.deepcopy(calls['map'].data)
            original = copy.deepcopy(game)
            events = []

            with self.subTest(normalize=normalize), self.assertLogs(
                    'backend.game_engine', level='WARNING'):
                updated, result = process_turn(
                    game, 'Enter the tavern.', calls, on_progress=events.append)

            self.assertEqual(game, original)
            self.assertEqual(result['manager_results']['map'], original_map)
            self.assertEqual(calls['map'].data, original_map)
            self.assertEqual(result['notable_elements']['locations'], [departure, destination])
            self.assertEqual(updated['currentLocationId'], updated['world']['locations'][1]['id'])
            self.assertEqual(len(updated['world']['locations']), 2)
            self.assertEqual(len(updated['world']['locations'][0]['exits']), 1)
            self.assertEqual(len(updated['world']['locations'][1]['exits']), 1)
            corrected = [event for event in events
                         if event['component'] == 'map' and event['status'] == 'corrected']
            self.assertEqual(len(corrected), 1)
            narrator_input = json.loads(calls['narrator'].inputs[0])
            self.assertEqual(narrator_input['resolved_interactions']['locations'],
                             [departure, destination])

    def test_map_redundant_enter_does_not_hide_distinct_destinations(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        departure = {
            'name': current['name'], 'description': current['description'],
            'interaction': 'leave',
        }
        calls = fake_calls()
        calls['map'].data = {'locations': [
            departure, dict(departure, interaction='enter'),
            {'name': 'Tavern', 'description': 'A tavern.', 'interaction': 'enter'},
            {'name': 'Cellar', 'description': 'A cellar.', 'interaction': 'enter'},
        ]}
        original = copy.deepcopy(game)
        with self.assertLogs('backend.game_engine', level='WARNING'), self.assertRaises(
                InvalidTurnResponse):
            process_turn(game, 'Move.', calls)
        self.assertEqual(game, original)

    def test_inventory_take_and_drop_move_existing_and_new_items(self):
        game = new_game(7)
        location_id = game['currentLocationId']
        existing = {
            'id': 'key-existing',
            'name': 'Iron key',
            'description': 'A small iron key.',
            'aliases': [],
            'knownByPlayer': False,
            'holder': {'type': 'location', 'id': location_id},
            'status': 'available',
        }
        carried = {
            'id': 'coin-existing',
            'name': 'Gold coin',
            'description': 'A single gold coin.',
            'aliases': [],
            'knownByPlayer': True,
            'holder': {'type': 'player', 'id': game['playerId']},
            'status': 'available',
        }
        game['world']['items'].extend([existing, carried])
        calls = fake_calls()
        calls['inventory'].data = {'items': [
            {'name': 'Iron key', 'description': 'A small iron key.', 'interaction': 'take'},
            {'name': 'Gold coin', 'description': 'A single gold coin.', 'interaction': 'drop'},
            {'name': 'Silver ring', 'description': 'A plain silver ring.', 'interaction': 'take'},
        ]}

        updated, result = process_turn(game, 'Take the key and ring; drop the coin.', calls)

        self.assertEqual(len(updated['world']['items']), 3)
        key, coin, ring = updated['world']['items']
        self.assertEqual(key['holder'], {'type': 'player', 'id': game['playerId']})
        self.assertTrue(key['knownByPlayer'])
        self.assertEqual(coin['holder'], {'type': 'location', 'id': location_id})
        self.assertEqual(ring['holder'], {'type': 'player', 'id': game['playerId']})
        self.assertEqual(
            {entity['id'] for entity in result['reconciled_entities']['items']},
            {key['id'], coin['id'], ring['id']},
        )

    def test_inventory_ignores_drop_without_carried_match(self):
        game = new_game(7)
        calls = fake_calls()
        calls['inventory'].data = {'items': [
            {'name': 'Unknown item', 'description': 'An unknown item.', 'interaction': 'drop'},
        ]}
        original = copy.deepcopy(game)

        events = []
        with self.assertLogs('backend.game_engine', level='WARNING'):
            updated, result = process_turn(
                game, 'Drop the unknown item.', calls, on_progress=events.append)

        self.assertEqual(game, original)
        self.assertEqual(updated['world'], original['world'])
        self.assertEqual(result['notable_elements']['items'], [])
        self.assertEqual(len(result['ignored_interactions']), 1)
        self.assertTrue(any(event['status'] == 'ignored' for event in events))
        self.assertEqual(json.loads(calls['narrator'].inputs[0])['ignored_interactions'],
                         result['ignored_interactions'])
        self.assertEqual(calls['comparator'].inputs, [])

    def test_empty_collections_create_entities_without_comparator(self):
        game = new_game(7)
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}],
            'locations': [],
            'npcs': [{'name': 'Mira', 'description': 'A keeper.', 'interaction': 'encounter'}],
        })
        calls['comparator'] = FakeCall({'same_entity': True})

        updated, _ = process_turn(game, 'Take the key and meet Mira.', calls)

        self.assertEqual(calls['comparator'].inputs, [])
        self.assertEqual(updated['world']['items'][0]['name'], 'Key')
        self.assertEqual(updated['world']['npcs'][0]['name'], 'Mira')

    def test_drop_compares_only_carried_items_and_moves_matched_id(self):
        game = new_game(7)
        for item_id, name, holder in (
                ('local-key', 'the key', {'type': 'location', 'id': game['currentLocationId']}),
                ('coin', 'Coin', {'type': 'player', 'id': game['playerId']}),
                ('carried-key', 'Iron key', {'type': 'player', 'id': game['playerId']})):
            game['world']['items'].append({
                'id': item_id, 'name': name, 'description': 'An object.',
                'status': 'available', 'holder': holder,
            })
        calls = fake_calls({
            'items': [{'name': 'the key', 'description': 'The carried key.', 'interaction': 'drop'}],
            'locations': [], 'npcs': [],
        })

        class KeyComparator(FakeCall):
            def run(self, user_input):
                self.data = {
                    'same_entity': json.loads(user_input)['candidate_entity']['name'] == 'Iron key',
                }
                return super().run(user_input)

        calls['comparator'] = KeyComparator({})
        updated, result = process_turn(game, 'Drop the key.', calls)

        candidates = [json.loads(value)['candidate_entity']['name']
                      for value in calls['comparator'].inputs]
        self.assertEqual(candidates, ['Coin', 'Iron key'])
        self.assertEqual(result['reconciled_entities']['items'][0]['id'], 'carried-key')
        self.assertEqual(updated['world']['items'][2]['holder'],
                         {'type': 'location', 'id': game['currentLocationId']})
        self.assertEqual(updated['world']['items'][1]['holder']['type'], 'player')

    def test_take_candidates_exclude_distant_and_unavailable_items(self):
        game = new_game(7)
        game['world']['npcs'].append({
            'id': 'mira', 'name': 'Mira', 'description': 'Keeper.',
            'status': 'active', 'currentLocationId': game['currentLocationId'],
        })
        for name, status, holder in (
                ('Distant', 'available', {'type': 'location', 'id': 'elsewhere'}),
                ('Destroyed', 'destroyed', {'type': 'player', 'id': game['playerId']}),
                ('Local', 'available', {'type': 'location', 'id': game['currentLocationId']}),
                ('Carried', 'available', {'type': 'player', 'id': game['playerId']}),
                ('NPC item', 'available', {'type': 'npc', 'id': 'mira'})):
            game['world']['items'].append({
                'id': name, 'name': name, 'description': 'An object.',
                'status': status, 'holder': holder,
            })
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'same_entity': False})

        updated, _ = process_turn(game, 'Take a key.', calls)

        self.assertEqual([json.loads(value)['candidate_entity']['name']
                          for value in calls['comparator'].inputs],
                         ['Local', 'Carried', 'NPC item'])
        self.assertEqual(len(updated['world']['items']), 6)

    def test_no_semantic_drop_match_is_ignored_without_creation(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'coin', 'name': 'Coin', 'description': 'Gold coin.',
            'status': 'available', 'holder': {'type': 'player', 'id': game['playerId']},
        })
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'A key.', 'interaction': 'drop'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'same_entity': False})
        original = copy.deepcopy(game)
        with self.assertLogs('backend.game_engine', level='WARNING'):
            updated, result = process_turn(game, 'Drop a key.', calls)
        self.assertEqual(game, original)
        self.assertEqual(updated['world'], original['world'])
        self.assertEqual(result['effective_manager_results']['inventory'], {'items': []})
        self.assertEqual(len(calls['comparator'].inputs), 1)

    def test_invalid_drop_does_not_block_take_or_movement(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        items = [
            {'name': 'beer', 'description': 'A drink', 'interaction': 'take'},
            {'name': 'tavern door', 'description': 'The door to the tavern',
             'interaction': 'drop'},
        ]
        calls = fake_calls({
            'items': items,
            'locations': [
                {'name': current['name'], 'description': current['description'],
                 'interaction': 'leave'},
                {'name': 'Town Tavern', 'description': 'A cozy tavern.', 'interaction': 'enter'},
            ],
            'npcs': [],
        })
        original = copy.deepcopy(game)
        with self.assertLogs('backend.game_engine', level='WARNING'):
            updated, result = process_turn(game, 'Enter the tavern and take a beer.', calls)
        self.assertEqual(game, original)
        self.assertEqual(result['manager_results']['inventory']['items'], items)
        self.assertEqual(result['notable_elements']['items'], [items[0]])
        self.assertEqual([item['name'] for item in updated['world']['items']], ['beer'])
        self.assertEqual(updated['currentLocationId'], updated['world']['locations'][1]['id'])
        self.assertEqual(result['ignored_interactions'][0]['proposed_entity'], items[1])
        self.assertTrue(calls['narrator'].inputs)

    def test_multiple_semantic_matches_reject_turn_atomically(self):
        game = new_game(7)
        for name in ('Iron key', 'Brass key'):
            game['world']['items'].append({
                'id': name, 'name': name, 'description': 'A key.',
                'status': 'available', 'holder': {'type': 'player', 'id': game['playerId']},
            })
        calls = fake_calls({
            'items': [{'name': 'the key', 'description': 'A carried key.', 'interaction': 'drop'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'same_entity': True})
        original = copy.deepcopy(game)
        with self.assertRaisesRegex(InvalidTurnResponse, 'multiple candidates matched'):
            process_turn(game, 'Drop the key.', calls)
        self.assertEqual(game, original)
        self.assertEqual(len(calls['comparator'].inputs), 2)

    def test_second_identical_item_matches_without_comparator(self):
        game = new_game(7)
        item = {'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}
        calls = fake_calls({'items': [item, dict(item)], 'locations': [], 'npcs': []})

        updated, _ = process_turn(game, 'Take the key.', calls)

        self.assertEqual(len(updated['world']['items']), 1)
        self.assertEqual(len(calls['comparator'].inputs), 0)

    def test_unique_exact_match_bypasses_incorrect_comparator(self):
        for normalize in (False, True):
            game = new_game(7)
            game['world']['items'].append({
                'id': 'beer-existing', 'name': 'beer', 'description': 'A beer',
                'status': 'available',
                'holder': {'type': 'location', 'id': game['currentLocationId']},
            })
            calls = fake_calls({
                'items': [{
                    'name': ' BEER ' if normalize else 'beer',
                    'description': ' A BEER ' if normalize else 'A beer',
                    'interaction': 'take',
                }],
                'locations': [], 'npcs': [],
            })
            calls['comparator'] = FakeCall({'same_entity': False})
            events = []

            with self.subTest(normalize=normalize):
                updated, result = process_turn(
                    game, 'Take the beer.', calls, on_progress=events.append)

            self.assertEqual(calls['comparator'].inputs, [])
            self.assertEqual(len(updated['world']['items']), 1)
            self.assertEqual(updated['world']['items'][0]['holder'],
                             {'type': 'player', 'id': game['playerId']})
            self.assertEqual(result['reconciled_entities']['items'][0]['id'], 'beer-existing')
            match = next(event for event in events if event['component'] == 'comparator')
            self.assertEqual(match['response'], {'same_entity': True})
            self.assertIn('no LLM call needed', match['reason'])

    def test_multiple_exact_matches_are_ambiguous(self):
        game = new_game(7)
        for item_id in ('beer-first', 'beer-second'):
            game['world']['items'].append({
                'id': item_id, 'name': 'beer', 'description': 'A beer',
                'status': 'available',
                'holder': {'type': 'location', 'id': game['currentLocationId']},
            })
        calls = fake_calls({
            'items': [{'name': 'beer', 'description': 'A beer', 'interaction': 'take'}],
            'locations': [], 'npcs': [],
        })
        original = copy.deepcopy(game)
        with self.assertRaisesRegex(InvalidTurnResponse, 'multiple exact matches'):
            process_turn(game, 'Take the beer.', calls)
        self.assertEqual(game, original)
        self.assertEqual(calls['comparator'].inputs, [])

    def test_generous_text_matching_preserves_canonical_item(self):
        for name, description in (
                ('IRON-key!', ' A  small iron key '),
                ('iron key', 'A rusty key with a worn handle.'),
                ('  Iron   KEY ', 'A small iron key!!!')):
            game = new_game(7)
            game['world']['items'].append({
                'id': 'key-existing', 'name': 'Iron key',
                'description': 'A small iron key.', 'status': 'available',
                'holder': {'type': 'player', 'id': game['playerId']},
            })
            calls = fake_calls({
                'items': [{'name': name, 'description': description, 'interaction': 'drop'}],
                'locations': [], 'npcs': [],
            })
            calls['comparator'] = FakeCall({'same_entity': False})
            with self.subTest(name=name, description=description):
                updated, result = process_turn(game, 'Drop the key.', calls)
            self.assertEqual(calls['comparator'].inputs, [])
            self.assertEqual(result['reconciled_entities']['items'][0]['id'], 'key-existing')
            self.assertEqual(updated['world']['items'][0]['description'], 'A small iron key.')
            self.assertEqual(updated['world']['items'][0]['holder'],
                             {'type': 'location', 'id': game['currentLocationId']})

    def test_generous_location_match_does_not_merge_tavern_and_square(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        calls = fake_calls({
            'items': [], 'npcs': [],
            'locations': [
                {'name': current['name'], 'description': current['description'].rstrip('.'),
                 'interaction': 'leave'},
                {'name': 'Tavern', 'description': 'A cozy tavern.', 'interaction': 'enter'},
            ],
        })
        calls['comparator'] = FakeCall({'same_entity': False})
        updated, _ = process_turn(game, 'Enter the tavern.', calls)
        self.assertEqual(len(calls['comparator'].inputs), 0)
        self.assertEqual(updated['currentLocationId'], updated['world']['locations'][1]['id'])

    def test_destination_cannot_false_match_departure(self):
        game = new_game(7)
        current = game['world']['locations'][0]
        calls = fake_calls({
            'items': [], 'npcs': [],
            'locations': [
                {'name': current['name'], 'description': current['description'],
                 'interaction': 'leave'},
                {'name': 'Tavern', 'description': 'A cozy tavern.', 'interaction': 'enter'},
            ],
        })
        calls['comparator'] = FakeCall({'same_entity': True})

        updated, result = process_turn(game, 'Enter the tavern.', calls)

        self.assertEqual(calls['comparator'].inputs, [])
        self.assertEqual(len(updated['world']['locations']), 2)
        self.assertNotEqual(updated['currentLocationId'], game['currentLocationId'])
        outcome = {
            'moved': True,
            'from_location': {'name': current['name'], 'description': current['description']},
            'to_location': {'name': 'Tavern', 'description': 'A cozy tavern.'},
        }
        self.assertEqual(result['movement_outcome'], outcome)
        self.assertEqual(json.loads(calls['narrator'].inputs[0])['movement_outcome'], outcome)

    def test_no_movement_outcome_is_explicit(self):
        for same_room_pair in (False, True):
            game = new_game(7)
            current = game['world']['locations'][0]
            calls = fake_calls()
            if same_room_pair:
                calls['map'].data = {'locations': [
                    {'name': current['name'], 'description': current['description'],
                     'interaction': interaction}
                    for interaction in ('leave', 'enter')
                ]}
            with self.subTest(same_room_pair=same_room_pair):
                updated, result = process_turn(game, 'Look around.', calls)
            self.assertEqual(updated['world']['locations'], game['world']['locations'])
            self.assertFalse(result['movement_outcome']['moved'])
            self.assertEqual(result['movement_outcome']['from_location'],
                             result['movement_outcome']['to_location'])
            self.assertEqual(json.loads(calls['narrator'].inputs[0])['movement_outcome'],
                             result['movement_outcome'])

    def test_narrator_requires_only_narration_and_boolean_outcomes(self):
        narrator = NarratorCall(transport=lambda *args: {})
        valid = narrator_response()
        self.assertEqual(narrator.validate_response(valid), valid)
        invalid_responses = [
            None,
            {},
            dict(valid, narration=''),
            dict(valid, state_changes=[]),
            dict(valid, notable_elements={'items': [], 'locations': [], 'npcs': []}),
            dict(valid, manager_overrides=[]),
            dict(valid, game_over='no'),
            dict(valid, action_succeeded='yes'),
        ]
        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(LLMError):
                narrator.validate_response(response)

    def test_comparator_requires_boolean_only(self):
        comparator = ComparatorCall(transport=lambda *args: {})
        for value in (True, False):
            self.assertEqual(comparator.validate_response({'same_entity': value}),
                             {'same_entity': value})
        invalid_responses = (
            {}, None, {'same_entity': 'yes'}, {'same_entity': 1},
            {'same_entity': True, 'id': 'forbidden'},
            {'already_present': True, 'matched_index': 0},
        )
        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(LLMError):
                comparator.validate_response(response)

    def test_invalid_comparator_result_does_not_mutate_game(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'coin', 'name': 'Coin', 'description': 'A gold coin.',
            'status': 'available', 'holder': {'type': 'player', 'id': game['playerId']},
        })
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'same_entity': 'yes'})
        original = copy.deepcopy(game)

        with self.assertRaisesRegex(InvalidTurnResponse, 'must return boolean'):
            process_turn(game, 'Find a key.', calls)

        self.assertEqual(game, original)

    def test_legacy_comparator_index_response_rejected_with_progress(self):
        game = new_game(7)
        game['world']['items'].append({
            'id': 'coin', 'name': 'Coin', 'description': 'A gold coin.',
            'status': 'available', 'holder': {'type': 'player', 'id': game['playerId']},
        })
        calls = fake_calls({
            'items': [{'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}],
            'locations': [], 'npcs': [],
        })
        calls['comparator'] = FakeCall({'already_present': True, 'matched_index': 1})
        original = copy.deepcopy(game)
        events = []

        with self.assertRaisesRegex(InvalidTurnResponse, 'boolean same_entity only'):
            process_turn(game, 'Find a key.', calls, on_progress=events.append)

        self.assertEqual(game, original)
        comparator_events = [event for event in events if event['component'] == 'comparator']
        self.assertEqual([event['status'] for event in comparator_events],
                         ['started', 'received', 'failed'])
        failed = comparator_events[-1]
        self.assertEqual(failed['request']['candidate_entity'],
                         {'name': 'Coin', 'description': 'A gold coin.'})
        self.assertEqual(failed['response'], {'already_present': True, 'matched_index': 1})
        self.assertIn('boolean same_entity only', failed['error'])
        self.assertEqual(events[-1]['component'], 'narrator')
        self.assertEqual(events[-1]['status'], 'skipped')
        self.assertEqual(calls['narrator'].inputs, [])

    def test_progress_includes_comparator_match_and_dungeon_master(self):
        game = new_game(7)
        item = {'name': 'Key', 'description': 'An iron key.', 'interaction': 'take'}
        calls = fake_calls({
            'items': [item, dict(item, name='The key')],
            'locations': [], 'npcs': [],
        })
        events = []

        process_turn(game, 'Take the key.', calls, on_progress=events.append)

        comparator_events = [event for event in events if event['component'] == 'comparator']
        self.assertEqual([event['status'] for event in comparator_events],
                         ['skipped', 'started', 'received', 'completed'])
        self.assertEqual(comparator_events[-1]['matched_entity'],
                         {'name': 'Key', 'description': 'An iron key.'})
        narrator_events = [event for event in events if event['component'] == 'narrator']
        self.assertEqual([event['status'] for event in narrator_events],
                         ['waiting', 'started', 'completed'])
        self.assertIn('resolved_context', narrator_events[1]['request'])
        self.assertEqual(narrator_events[-1]['response']['narration'], 'You look around.')

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
