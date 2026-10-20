import os
import json
import sqlite3
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.app import app
from backend.account_schema import ensure_account_schema
from backend.game_engine import InvalidTurnResponse, new_game
from backend.harness.llm_provider import LLMError


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'game.db')
        with sqlite3.connect(self.path) as db:
            ensure_account_schema(db)

        def get_db():
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            return connection

        self.config_patch = patch.dict(app.config, {
            'TESTING': True,
            'get_db': get_db,
        })
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.client = app.test_client()
        self.assertEqual(self.client.post('/auth/register', json={
            'email': 'player@example.com',
            'password': 'PlayerPassword123',
        }).status_code, 200)

    def test_start_new_game_and_persist_across_requests(self):
        first = self.client.get('/game/').get_json()
        second = self.client.get('/game/').get_json()
        self.assertEqual(first['game']['id'], second['game']['id'])
        self.assertEqual(second['game']['currentLocationId'], 'town-square-outside-tavern')

        replacement = self.client.post('/game/new').get_json()['game']
        self.assertNotEqual(replacement['id'], first['game']['id'])
        self.assertEqual(self.client.get('/game/').get_json()['game']['id'], replacement['id'])

    def test_turn_is_idempotent_and_checks_game_revision(self):
        game = self.client.get('/game/').get_json()['game']
        request_id = 'efb74c49-3e11-4bf1-b72f-447e4b632c08'
        result_game = new_game(1)
        result_game['id'] = game['id']
        result_game['history']['recentEvents'].append({'kind': 'narration', 'content': 'Done.'})
        calls = []

        def process_turn(state, action, settings=None):
            calls.append((state, action))
            return result_game, {
                'narration': 'Done.',
                'action_succeeded': True,
                'provider': 'test',
                'model': 'test',
            }

        with patch('backend.endpoints.game.process_turn', side_effect=process_turn):
            body = {
                'action': 'Look around.',
                'game_id': game['id'],
                'request_id': request_id,
            }
            first = self.client.post('/game/turn', json=body)
            second = self.client.post('/game/turn', json=body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json(), second.get_json())
        self.assertEqual(len(calls), 1)

        changed_game = self.client.post('/game/new').get_json()['game']
        stale = self.client.post('/game/turn', json={
            'action': 'Look around.',
            'game_id': game['id'],
            'request_id': '7b71fd0e-a2d9-46b0-859d-c67be5a3c74f',
        })
        self.assertNotEqual(changed_game['id'], game['id'])
        self.assertEqual(stale.status_code, 409)

    def test_turn_rejects_invalid_requests_without_llm_calls(self):
        game = self.client.get('/game/').get_json()['game']
        self.assertEqual(self.client.post('/game/turn', json={
            'action': 'Look around',
            'game_id': game['id'],
        }).status_code, 400)
        self.assertEqual(self.client.post('/game/turn', json={
            'action': '   ',
            'game_id': game['id'],
            'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
        }).status_code, 400)

    def test_stream_reports_progress_before_commit_and_replays_completed_request(self):
        game = self.client.get('/game/').get_json()['game']
        body = {'action': 'Look around.', 'game_id': game['id'],
                'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08'}
        release = threading.Event()
        self.addCleanup(release.set)

        def resolve(state, action, settings=None, on_progress=None):
            on_progress({'component': 'map', 'status': 'completed',
                         'response': {'movement': False, 'target': None}})
            if not release.wait(5):
                raise AssertionError('Stream did not deliver intermediate progress.')
            return state, {'narration': 'You look around.', 'action_succeeded': True,
                           'provider': 'test', 'model': 'test'}

        with patch('backend.endpoints.game.process_turn', side_effect=resolve) as resolve_mock:
            response = self.client.post('/game/turn', json=body,
                                        headers={'Accept': 'application/x-ndjson'}, buffered=False)
            self.assertEqual(response.mimetype, 'application/x-ndjson')
            self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
            events = iter(response.response)
            self.assertEqual(json.loads(next(events))['status'], 'started')
            intermediate = json.loads(next(events))
            self.assertEqual(intermediate['response'], {'movement': False, 'target': None})
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute('SELECT version FROM games').fetchone()[0], 0)
            release.set()
            remaining = [json.loads(chunk) for chunk in events]
            self.assertEqual(remaining[-1]['type'], 'complete')
            self.assertEqual(remaining[-1]['data']['game']['id'], game['id'])
            replay = self.client.post('/game/turn', json=body,
                                      headers={'Accept': 'application/x-ndjson'})
            self.assertEqual(json.loads(replay.data), remaining[-1])
            self.assertEqual(resolve_mock.call_count, 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT version FROM games').fetchone()[0], 1)

    def test_stream_failure_emits_error_without_saving(self):
        game = self.client.get('/game/').get_json()['game']
        with patch('backend.endpoints.game.process_turn', side_effect=InvalidTurnResponse('Bad item')):
            response = self.client.post('/game/turn', json={
                'action': 'Order beer.', 'game_id': game['id'],
                'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
            }, headers={'Accept': 'application/x-ndjson'})
            events = [json.loads(line) for line in response.data.splitlines()]
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['status'], 502)
        self.assertIn('state was not changed', events[-1]['data']['error'])
        self.assertIn('Bad item', events[-1]['data']['error'])
        self.assertEqual(self.client.get('/game/').get_json()['game'], game)

    def test_turn_failures_explain_reason_in_json_and_stream(self):
        game = self.client.get('/game/').get_json()['game']
        for failure in (
                LLMError('inventory_manager: invalid interaction.',
                         details='PRIVATE RAW PROVIDER RESPONSE'),
                InvalidTurnResponse('Ambiguous item identity.'),
                KeyError('recipient'),
                ValueError('Invalid destination.'),
                TypeError('Expected an item list.')):
            for streaming in (False, True):
                with self.subTest(failure=type(failure).__name__, streaming=streaming):
                    with patch('backend.endpoints.game.process_turn', side_effect=failure):
                        response = self.client.post('/game/turn', json={
                            'action': 'Look around.', 'game_id': game['id'],
                            'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
                        }, headers={'Accept': 'application/x-ndjson' if streaming
                                    else 'application/json'})
                    if streaming:
                        event = json.loads(response.data.splitlines()[-1])
                        self.assertEqual(event['type'], 'error')
                        self.assertEqual(event['status'], 502)
                        data = event['data']
                    else:
                        self.assertEqual(response.status_code, 502)
                        data = response.get_json()
                    self.assertIn(str(failure), data['error'])
                    self.assertIn('state was not changed', data['error'])
                    self.assertNotIn('PRIVATE RAW PROVIDER RESPONSE', json.dumps(data))
                    self.assertNotIn('Traceback', data['error'])
                    self.assertEqual(self.client.get('/game/').get_json()['game'], game)

    def test_stream_shows_rejected_manager_response(self):
        from backend.game_engine import process_turn
        from backend.harness.llm_calls import LLMResponseError
        from backend.test_game_engine import fake_calls

        game = self.client.get('/game/').get_json()['game']
        rejected = {'items': [{'name': 'Beer', 'interaction': 'consume'}]}
        for failure in (
                LLMResponseError('inventory_manager: missing description.', details=rejected),
                LLMResponseError('inventory_manager: response was not a JSON object.',
                                 details='This is not JSON.'),
                LLMError('Provider unavailable.', details='PRIVATE PROVIDER DETAILS')):
            with self.subTest(failure=str(failure)):
                calls = fake_calls()
                calls['inventory'].run = Mock(side_effect=failure)

                def resolve(state, action, settings=None, on_progress=None):
                    return process_turn(state, action, calls=calls, on_progress=on_progress)

                with patch('backend.endpoints.game.process_turn', side_effect=resolve):
                    response = self.client.post('/game/turn', json={
                        'action': 'Drink the beer.', 'game_id': game['id'],
                        'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
                    }, headers={'Accept': 'application/x-ndjson'})
                    events = [json.loads(line) for line in response.data.splitlines()]
                failed = next(event for event in events
                              if event.get('component') == 'inventory'
                              and event.get('status') == 'failed')
                self.assertEqual(failed['error'], str(failure))
                if isinstance(failure, LLMResponseError):
                    self.assertEqual(failed['response'], failure.details)
                else:
                    self.assertNotIn('response', failed)
                    self.assertNotIn('PRIVATE PROVIDER DETAILS', json.dumps(events))
                self.assertEqual(events[-1]['type'], 'error')
                self.assertEqual(self.client.get('/game/').get_json()['game'], game)


if __name__ == '__main__':
    unittest.main()
