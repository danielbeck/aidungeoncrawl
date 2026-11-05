"""Opt-in movement checks against the locally installed llama3.2 model.

RUN_LIVE_MOVEMENT_TESTS=1 venv/bin/python -m unittest backend.test_movement_live
"""
import json
import os
import unittest

from backend.game_engine import new_game, process_turn
from backend.llm_calls.gameplay import ComparatorCall, MapManagerCall, MovementResolverCall
from backend.test_game_engine import fake_calls


@unittest.skipUnless(os.environ.get('RUN_LIVE_MOVEMENT_TESTS') == '1',
                     'Set RUN_LIVE_MOVEMENT_TESTS=1 to call the local model.')
class LiveMovementTests(unittest.TestCase):
    def setUp(self):
        settings = {
            'LLM_PROVIDER': 'ollama',
            'OLLAMA_URL': 'http://localhost:11434',
            'OLLAMA_MODEL': 'llama3.2:latest',
        }
        self.calls = fake_calls()
        self.calls.update({
            'map': MapManagerCall(settings=settings),
            'movement_resolver': MovementResolverCall(settings=settings),
            'comparator': ComparatorCall(settings=settings),
        })

    def test_round_trip_then_new_destination_and_intentional_revisit(self):
        game = new_game(7)
        starting_id = game['currentLocationId']
        game, result = process_turn(game, 'Head into the wilderness.', self.calls)
        self.assertTrue(result['movement_outcome']['moved'], result)
        wilderness_id = game['currentLocationId']
        game, result = process_turn(game, 'Retrace my steps.', self.calls)
        self.assertEqual(game['currentLocationId'], starting_id, result)
        game, result = process_turn(game, 'Make my way to the tavern.', self.calls)
        self.assertEqual(result['manager_results']['map']['target']['kind'], 'named')
        self.assertNotIn(game['currentLocationId'], (starting_id, wilderness_id))
        self.assertIn('tavern', result['movement_outcome']['to_location']['name'].lower())
        self.assertEqual(len(game['world']['locations']), 3)
        game, result = process_turn(game, 'Go outside.', self.calls)
        self.assertEqual(game['currentLocationId'], starting_id, result)
        game, _ = process_turn(game, 'Return to the wilderness.', self.calls)
        self.assertEqual(game['currentLocationId'], wilderness_id)
        self.assertEqual(len(game['world']['locations']), 3)

    def test_comparator_valid_and_invalid_identity_matches(self):
        cases = (
            ('locations', ('into the tavern', 'A place known as into the tavern.'),
             ('the tavern', 'A place known as the tavern.'), True),
            ('locations', ('Forest Trail', 'A trail north of the clearing.'),
             ('the tavern', 'A place known as the tavern.'), False),
            ('locations', ('Town Square Outside the Tavern', 'The town square outside the tavern.'),
             ('Tavern', 'A cozy town tavern.'), False),
            ('locations', ('Tavern Cellar', 'Storage beneath the tavern.'),
             ('Tavern Main Room', 'The public room with the bar.'), False),
            ('items', ('mug of beer', 'A mug filled with ale.'),
             ('mug', 'A glass of ale.'), True),
            ('items', ('Iron key', 'A small iron key.'),
             ('the iron key', 'A small key made of iron.'), True),
            ('items', ('Iron key', 'A small iron key.'),
             ('Brass key', 'A small brass key.'), False),
            ('npcs', ('Mira', 'The tavern keeper.'),
             ('the barkeep', 'Mira, who runs the tavern.'), True),
            ('npcs', ('Mira', 'The tavern keeper.'),
             ("Mira's sword", 'A steel sword belonging to Mira.'), False),
            ('locations', ('Woodland Path', 'A narrow footpath among the trees.'),
             ('forest trail', 'The narrow path through the woods.'), True),
            ('items', ('Red mug', 'A red mug containing beer.'),
             ('Blue mug', 'A blue mug containing beer.'), False),
        )
        for entity_type, candidate, proposed, expected in cases:
            with self.subTest(candidate=candidate[0], proposed=proposed[0]):
                result = self.calls['comparator'].run(json.dumps({
                    'entity_type': entity_type,
                    'candidates': [{'id': 'candidate', **dict(zip(('name', 'description'), candidate))}],
                    'proposed_entity': dict(zip(('name', 'description'), proposed)),
                }))
                self.assertEqual(result.data, {'outcome': 'existing', 'entity_id': 'candidate'}
                                 if expected else {'outcome': 'new'})

    def test_named_paraphrases_and_non_movement(self):
        for action, kind, text in (
                ('I stroll through the door into the tavern.', 'named', 'tavern'),
                ('Find my way to North Gate.', 'named', 'north gate'),
                ('Head north towards the Great Forest.', 'named', 'great forest'),
                ('Go outside.', 'relative', 'outside'),
                ('Head north.', 'directional', 'north'),
                ('Explore farther into the woods.', 'exploratory', 'woods'),
                ('Go there.', 'reference', 'there')):
            with self.subTest(action=action):
                result = self.calls['map'].run(json.dumps({
                    'current_action': action,
                })).data
                self.assertTrue(result['movement'])
                self.assertEqual(result['target']['kind'], kind, result)
                self.assertIn(text, result['target']['text'].lower())
        for action in ('Look at the tavern.', 'Do not enter the tavern.', 'Drop the key.',
                       'If I entered the tavern, what would I see?'):
            with self.subTest(action=action):
                result = self.calls['map'].run(json.dumps({
                    'current_action': action,
                })).data
                self.assertEqual(result, {'movement': False, 'target': None})

    def test_contextual_resolution_and_ambiguous_reference(self):
        for action, expected_kind in (
                ('Explore farther into the woods.', 'exploratory'),
                ('Head north.', 'directional')):
            game = new_game(7)
            game['world']['locations'][0].update({
                'name': 'Forest Clearing', 'description': 'A clearing with a trail north.',
            })
            with self.subTest(action=action):
                updated, result = process_turn(game, action, self.calls)
                self.assertEqual(result['manager_results']['map']['target']['kind'], expected_kind)
                self.assertTrue(result['movement_outcome']['moved'], result)
                self.assertEqual(len(updated['world']['locations']), 2)
        game = new_game(7)
        updated, result = process_turn(game, 'Follow him.', self.calls)
        self.assertFalse(result['movement_outcome']['moved'], result)
        self.assertEqual(updated['world'], game['world'])
        self.assertIsNotNone(result['movement_resolution']['clarification'], result)
