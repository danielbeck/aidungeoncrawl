import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from flask import Flask

from backend.endpoints.game import bp
from backend.endpoints.llm import bp as llm_bp


def processor(state, action, settings=None, on_progress=None):
    return state, {'narration': 'You look around.', 'action_succeeded': True,
                   'provider': 'test', 'model': 'test'}


class TurnQuotaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, 'game.db')
        self.app = self.make_app({'USER_DAILY_TURN_QUOTA': '2'})

    def make_app(self, settings):
        app = Flask(__name__)
        app.config.update(SECRET_KEY='test', TESTING=True, llm_settings=settings,
                          get_db=self.get_db)
        app.register_blueprint(bp)
        return app

    def get_db(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def client(self, app=None, user_id=1):
        client = (app or self.app).test_client()
        with client.session_transaction() as session:
            session['user_id'] = user_id
        return client

    def start_game(self, client):
        return client.get('/game/').get_json()['game']

    def turn(self, client, game, request_id=None):
        return client.post('/game/turn', json={
            'action': 'Look around.', 'game_id': game['id'],
            'request_id': request_id or str(uuid.uuid4()),
        })

    def usage(self):
        with sqlite3.connect(self.path) as conn:
            return conn.execute('SELECT user_id, day, turns FROM user_turn_usage').fetchall()

    def test_turns_beyond_the_daily_allowance_are_rejected_without_calling_the_engine(self):
        client = self.client()
        game = self.start_game(client)
        with patch('backend.endpoints.game.process_turn', side_effect=processor):
            self.assertEqual(self.turn(client, game).status_code, 200)
            self.assertEqual(self.turn(client, game).status_code, 200)
        with patch('backend.endpoints.game.process_turn', side_effect=processor) as run:
            response = self.turn(client, game)
        self.assertEqual(response.status_code, 429)
        self.assertIn('daily limit of 2 game turns', response.get_json()['error'])
        run.assert_not_called()
        self.assertEqual([row[2] for row in self.usage()], [2])

    def test_replaying_a_completed_request_does_not_consume_another_turn(self):
        app = self.make_app({'USER_DAILY_TURN_QUOTA': '1'})
        client = self.client(app)
        game = self.start_game(client)
        request_id = str(uuid.uuid4())
        with patch('backend.endpoints.game.process_turn', side_effect=processor) as run:
            self.assertEqual(self.turn(client, game, request_id).status_code, 200)
            self.assertEqual(self.turn(client, game, request_id).status_code, 200)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(self.turn(client, game).status_code, 429)

    def test_usage_is_durable_across_processes(self):
        client = self.client()
        game = self.start_game(client)
        with patch('backend.endpoints.game.process_turn', side_effect=processor):
            self.assertEqual(self.turn(client, game).status_code, 200)
            self.assertEqual(self.turn(client, game).status_code, 200)
        # A CGI request is a fresh process with fresh in-memory state.
        fresh = self.make_app({'USER_DAILY_TURN_QUOTA': '2'})
        with patch('backend.endpoints.game.process_turn', side_effect=processor) as run:
            self.assertEqual(self.turn(self.client(fresh), game).status_code, 429)
        run.assert_not_called()

    def test_each_account_has_its_own_allowance(self):
        first = self.client(user_id=1)
        game = self.start_game(first)
        with patch('backend.endpoints.game.process_turn', side_effect=processor):
            self.assertEqual(self.turn(first, game).status_code, 200)
            self.assertEqual(self.turn(first, game).status_code, 200)
            self.assertEqual(self.turn(first, game).status_code, 429)
        other = self.client(user_id=2)
        other_game = self.start_game(other)
        with patch('backend.endpoints.game.process_turn', side_effect=processor):
            self.assertEqual(self.turn(other, other_game).status_code, 200)
        self.assertEqual(sorted(row[0] for row in self.usage()), [1, 2])

    def test_ended_games_cost_nothing(self):
        client = self.client()
        game = self.start_game(client)
        client.post('/game/stop')
        with patch('backend.endpoints.game.process_turn', side_effect=processor):
            self.assertEqual(self.turn(client, game).status_code, 409)
        self.assertEqual(self.usage(), [])

    def test_missing_or_invalid_setting_falls_back_to_the_default(self):
        for settings in ({}, {'USER_DAILY_TURN_QUOTA': 'lots'}, {'USER_DAILY_TURN_QUOTA': '0'}):
            with self.subTest(settings=settings):
                app = self.make_app(settings)
                client = self.client(app)
                game = self.start_game(client)
                with patch('backend.endpoints.game.process_turn', side_effect=processor):
                    self.assertEqual(self.turn(client, game).status_code, 200)


class ProductionSurfaceTests(unittest.TestCase):
    def make_app(self, settings):
        app = Flask(__name__)
        app.config.update(SECRET_KEY='test', TESTING=True, llm_settings=settings)
        app.register_blueprint(llm_bp)
        return app

    def test_free_form_llm_chat_is_absent_in_production(self):
        with patch('backend.endpoints.llm.call_llm') as call:
            response = self.make_app({'FLASK_ENV': 'production'}).test_client().post(
                '/llm/chat', json={'prompt': 'hello', 'response_format': {'type': 'object'}})
        self.assertEqual(response.status_code, 404)
        call.assert_not_called()

    def test_free_form_llm_chat_still_works_in_development(self):
        with patch('backend.endpoints.llm.call_llm', return_value={
                'provider': 'test', 'model': 'test', 'response': json.dumps({'ok': True})}) as call:
            response = self.make_app({}).test_client().post(
                '/llm/chat', json={'prompt': 'hello', 'response_format': {'type': 'object'}})
        self.assertEqual(response.status_code, 200)
        call.assert_called_once()


if __name__ == '__main__':
    unittest.main()
