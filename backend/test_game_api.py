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

    def test_selected_starting_point_persists(self):
        for starting_point, location_id in (
                ('town', 'town-square-outside-tavern'),
                ('wilderness', 'wilderness-trail'),
                ('dungeon', 'dungeon-entrance')):
            with self.subTest(starting_point=starting_point):
                response = self.client.post('/game/new', json={'starting_point': starting_point})
                self.assertEqual(response.status_code, 200)
                game = response.get_json()['game']
                self.assertEqual(game['currentLocationId'], location_id)
                self.assertEqual(self.client.get('/game/').get_json()['game'], game)

    def test_invalid_starting_point_does_not_replace_saved_game(self):
        game = self.client.get('/game/').get_json()['game']
        for body in ({'starting_point': 'unknown'}, {'starting_point': None},
                     {'starting_point': []}, {'starting_point': {}}, [], 'wilderness'):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/game/new', json=body).status_code, 400)
        self.assertEqual(self.client.post(
            '/game/new', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get('/game/').get_json()['game'], game)

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

    def test_freeform_turn_persists_and_can_switch_back_to_managed(self):
        game = self.client.get('/game/').get_json()['game']

        def resolve(state, action, **options):
            return state, {'narration': 'The story continues.'}

        with patch('backend.endpoints.game.process_freeform_turn', side_effect=resolve) as freeform:
            response = self.client.post('/game/turn', json={
                'action': 'Wait.', 'game_id': game['id'], 'mode': 'freeform',
                'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
            })
        self.assertEqual(response.status_code, 200)
        freeform.assert_called_once()
        saved = self.client.get('/game/').get_json()['game']
        self.assertEqual(saved['mode'], 'freeform')
        self.assertEqual(saved['world'], game['world'])
        with patch('backend.endpoints.game.process_turn', side_effect=resolve) as managed:
            response = self.client.post('/game/turn', json={
                'action': 'Look around.', 'game_id': game['id'], 'mode': 'managed',
                'request_id': '7b71fd0e-a2d9-46b0-859d-c67be5a3c74f',
            })
        self.assertEqual(response.status_code, 200)
        managed.assert_called_once()
        self.assertEqual(response.get_json()['game']['mode'], 'managed')

    def test_mode_selection_persists_without_a_turn(self):
        game = self.client.get('/game/').get_json()['game']
        for mode in ('freeform', 'tool_using', 'direct', 'managed'):
            response = self.client.post('/game/mode', json={
                'game_id': game['id'], 'mode': mode,
            })
            self.assertEqual(response.status_code, 200)
            saved = self.client.get('/game/').get_json()['game']
            self.assertEqual(saved['mode'], mode)
            self.assertEqual(saved['world'], game['world'])
            self.assertEqual(saved['history'], game['history'])
        self.assertEqual(self.client.post('/game/mode', json={
            'game_id': 'old-game', 'mode': 'freeform',
        }).status_code, 409)
        self.assertEqual(self.client.post('/game/mode', json={
            'game_id': game['id'], 'mode': 'unknown',
        }).status_code, 400)
        self.assertEqual(self.client.get('/game/').get_json()['game']['mode'], 'managed')

    def test_tool_using_turn_returns_before_history_preparation(self):
        from backend.tool_mode import GameMasterCall, process_tool_turn
        from types import SimpleNamespace

        game = self.client.get('/game/').get_json()['game']
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                original = self.client.get('/game/').get_json()['game']
                transport = Mock(return_value={
                    'provider': 'fake', 'model': 'fake',
                    'message': {'role': 'assistant', 'content': 'The story continues.', 'tool_calls': []},
                })
                def resolve(state, action, **options):
                    return process_tool_turn(
                        state, action, call=GameMasterCall(transport=transport),
                        **options)

                with patch('backend.endpoints.game.process_tool_turn', side_effect=resolve), \
                        patch('backend.endpoints.game.process_turn') as managed:
                    response = self.client.post('/game/turn', json={
                        'action': 'Wait.', 'game_id': game['id'], 'mode': 'tool_using',
                        'request_id': ('efb74c49-3e11-4bf1-b72f-447e4b632c08' if not streaming
                                       else '7b71fd0e-a2d9-46b0-859d-c67be5a3c74f'),
                    }, headers={'Accept': 'application/x-ndjson'} if streaming else {})
                    self.assertEqual(response.status_code, 200)
                    if streaming:
                        events = [json.loads(line) for line in response.data.splitlines()]
                        self.assertEqual(events[-1]['type'], 'complete')
                        self.assertTrue(any(event.get('component') == 'game_master'
                                            for event in events))
                    managed.assert_not_called()
                transport.assert_called_once()
                self.assertEqual({tool['function']['name'] for tool in transport.call_args[0][1]},
                                 {'move_to_new_location', 'create_item', 'create_npc'})
                saved = self.client.get('/game/').get_json()['game']
                self.assertEqual(saved['mode'], 'tool_using')
                self.assertEqual(saved['world'], original['world'])
                self.assertEqual(saved['currentLocationId'], original['currentLocationId'])
                self.assertEqual(saved['history']['recentEvents'][:-2],
                                 original['history']['recentEvents'])
                self.assertEqual(saved['history']['recentEvents'][-1]['content'],
                                 'The story continues.')
                self.assertNotEqual(saved['history'].get('summaryEventCount'),
                                    len(saved['history']['recentEvents']))
                with patch('backend.endpoints.game.HistorySummarizerCall.run',
                           return_value=SimpleNamespace(
                               data={'summary': 'The player waited.'}, provider='fake',
                               model='fake')) as summarize:
                    prepared = self.client.post('/game/prepare', json={
                        'game_id': game['id'],
                    }).get_json()
                summarize.assert_called_once()
                self.assertTrue(prepared['prepared'])
                self.assertEqual(prepared['summaryEventCount'], len(saved['history']['recentEvents']))

    def test_tool_using_world_changes_are_saved_once(self):
        from backend.tool_mode import GameMasterCall, process_tool_turn
        from backend.test_tool_mode import reply, tool

        game = self.client.get('/game/').get_json()['game']
        rounds = []

        def respond(messages, tools):
            copy_of_messages = json.loads(json.dumps(messages))
            rounds.append(copy_of_messages)
            if len(rounds) == 1:
                return reply(calls=[tool('create_item', name='Key', description='Iron.',
                                         location_id=game['currentLocationId'])])
            if len(rounds) == 2:
                outcome = json.loads(next(message for message in reversed(copy_of_messages)
                                          if message['role'] == 'tool')['content'])
                return reply(calls=[tool('pick_up_item', item_id=outcome['entity']['id'])])
            return reply('You pick up the key.')

        master = GameMasterCall(transport=respond)

        def resolve(state, action, **options):
            return process_tool_turn(state, action, call=master, **options)

        body = {
            'action': 'Take a key.', 'game_id': game['id'], 'mode': 'tool_using',
            'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
        }
        with patch('backend.endpoints.game.process_tool_turn', side_effect=resolve):
            response = self.client.post('/game/turn', json=body).get_json()
            repeated = self.client.post('/game/turn', json=body).get_json()
        self.assertEqual(response, repeated)
        self.assertEqual(len(rounds), 3)
        saved = self.client.get('/game/').get_json()['game']
        self.assertEqual(saved['world']['items'][0]['holder'],
                         {'type': 'player', 'id': saved['playerId']})
        self.assertNotIn('summaryEventCount', saved['history'])

    def test_tool_using_provider_failure_does_not_save_draft(self):
        from backend.tool_mode import GameMasterCall, process_tool_turn
        from backend.test_tool_mode import reply, tool

        game = self.client.get('/game/').get_json()['game']
        transport = Mock(side_effect=[
            reply(calls=[tool('create_item', name='Key', description='Iron.',
                              location_id=game['currentLocationId'])]),
            LLMError('Provider unavailable.', details='No endpoints found that support tools.',
                     code='PROVIDER_HTTP_ERROR'),
        ])
        master = GameMasterCall(transport=transport)

        def resolve(state, action, **options):
            return process_tool_turn(state, action, call=master, **options)

        with patch('backend.endpoints.game.process_tool_turn', side_effect=resolve):
            response = self.client.post('/game/turn', json={
                'action': 'Take a key.', 'game_id': game['id'], 'mode': 'tool_using',
                'request_id': 'efb74c49-3e11-4bf1-b72f-447e4b632c08',
            }, headers={'Accept': 'application/x-ndjson'})
            event = json.loads(response.data.splitlines()[-1])
        self.assertEqual(event['type'], 'error')
        self.assertEqual(event['status'], 502)
        self.assertEqual(event['data']['details'], 'No endpoints found that support tools.')
        self.assertIn('No endpoints found that support tools.', event['data']['error'])
        progress = [json.loads(line) for line in response.data.splitlines()]
        failed = next(entry for entry in progress
                      if entry.get('component') == 'game_master'
                      and entry.get('status') == 'failed')
        self.assertEqual(failed['response'], event['data']['details'])
        self.assertEqual(self.client.get('/game/').get_json()['game'], game)

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
