import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from backend.endpoints.game import bp
from backend.game_engine import new_game
from backend.harness.llm_provider import LLMError
from backend.harness import llm_provider


class HistoryPreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, 'game.db')
        self.state = new_game(1)
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE games (user_id INTEGER PRIMARY KEY, state_json TEXT, version INTEGER)')
            conn.execute('INSERT INTO games VALUES (1, ?, 4)', (json.dumps(self.state),))

        def get_db():
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

        app = Flask(__name__)
        app.config.update(SECRET_KEY='test', TESTING=True, get_db=get_db, llm_settings={})
        app.register_blueprint(bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1

    def prepare(self):
        return self.client.post('/game/prepare', json={'game_id': self.state['id']})

    def read(self):
        with sqlite3.connect(self.path) as conn:
            state, version = conn.execute('SELECT state_json, version FROM games').fetchone()
        return json.loads(state), version

    def test_preparation_saves_summary_without_changing_game_revision_and_reuses_it(self):
        result = SimpleNamespace(data={'summary': 'The player is in the square.'},
                                 provider='test', model='test')
        with patch('backend.endpoints.game.HistorySummarizerCall.run', return_value=result) as run:
            generated = self.prepare().get_json()
            cached = self.prepare().get_json()
        for response in (generated, cached):
            self.assertTrue(response['prepared'])
            self.assertEqual(response['summary'], result.data['summary'])
            self.assertEqual(response['summaryEventCount'], 1)
        self.assertEqual(generated['provider'], 'test')
        self.assertEqual(generated['model'], 'test')
        self.assertEqual(run.call_count, 1)
        saved, version = self.read()
        self.assertEqual(version, 4)
        self.assertEqual(saved['history']['summaryEventCount'], 1)
        self.assertEqual(saved['world'], self.state['world'])

    def test_late_preparation_cannot_overwrite_new_turn(self):
        def summarize(_):
            newer = json.loads(json.dumps(self.state))
            newer['history']['recentEvents'].append({'kind': 'narration', 'content': 'New turn.'})
            with sqlite3.connect(self.path) as conn:
                conn.execute('UPDATE games SET state_json = ?, version = 5', (json.dumps(newer),))
            return SimpleNamespace(data={'summary': 'Outdated summary.'}, provider='test', model='test')

        with patch('backend.endpoints.game.HistorySummarizerCall.run', side_effect=summarize):
            self.assertFalse(self.prepare().get_json()['prepared'])
        saved, version = self.read()
        self.assertEqual(version, 5)
        self.assertEqual(saved['history']['recentEvents'][-1]['content'], 'New turn.')
        self.assertNotIn('summary', saved['history'])

    def test_preparation_failure_preserves_saved_turn_and_reports_error(self):
        with patch('backend.endpoints.game.HistorySummarizerCall.run',
                   side_effect=LLMError('unavailable')), self.assertLogs(level='ERROR'):
            response = self.prepare()
        self.assertEqual(response.status_code, 502)
        self.assertIn('unavailable', response.get_json()['error'])
        self.assertEqual(self.read(), (self.state, 4))


class SummarizerProviderTests(unittest.TestCase):
    def test_schema_and_generation_budget_reach_both_providers(self):
        schema = {'type': 'object', 'properties': {'summary': {'type': 'string'}},
                  'required': ['summary'], 'additionalProperties': False}
        for provider in ('ollama', 'openrouter'):
            message = {'content': '{"summary":"Completed events."}'}
            response = ({'message': message, 'done_reason': 'stop'} if provider == 'ollama'
                        else {'choices': [{'message': message, 'finish_reason': 'stop'}]})
            with self.subTest(provider=provider), patch.object(
                    llm_provider, '_post_json', return_value=response) as post:
                result = llm_provider.chat([], schema, 'history_summarizer', settings={
                    'LLM_PROVIDER': provider, 'OPENROUTER_API_KEY': 'test-key'})
            self.assertEqual(json.loads(result['response']), {'summary': 'Completed events.'})
            payload = post.call_args[0][1]
            if provider == 'ollama':
                self.assertEqual(payload['format'], schema)
                self.assertEqual(payload['options'],
                                 {'num_ctx': 32768, 'num_predict': 8192, 'temperature': 0})
                self.assertFalse(payload['think'])
            else:
                self.assertTrue(payload['response_format']['json_schema']['strict'])
                self.assertEqual(payload['max_tokens'], 8192)

    def test_truncation_is_explicit_for_both_providers(self):
        for provider in ('ollama', 'openrouter'):
            message = {'content': '{"summary":"Incomplete'}
            response = ({'message': message, 'done_reason': 'length'} if provider == 'ollama'
                        else {'choices': [{'message': message, 'finish_reason': 'length'}]})
            with self.subTest(provider=provider), patch.object(
                    llm_provider, '_post_json', return_value=response):
                with self.assertRaisesRegex(LLMError, 'truncated'):
                    llm_provider.chat([], {'type': 'object'}, 'history_summarizer', settings={
                        'LLM_PROVIDER': provider, 'OPENROUTER_API_KEY': 'test-key'})
