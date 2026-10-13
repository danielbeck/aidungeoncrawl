import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.app import app
from backend.account_schema import ensure_account_schema
from backend.session_secret import load_session_secret


class AccountBackportTests(unittest.TestCase):
    def test_secret_sources_and_no_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch('backend.session_secret.dotenv_values', return_value={'SECRET_KEY': 'test-local-key'}):
                self.assertEqual(load_session_secret(), 'test-local-key')
                os.environ['SECRET_KEY'] = 'test-environment-key'
                self.assertEqual(load_session_secret(), 'test-environment-key')
            os.environ.pop('SECRET_KEY')
            with patch('backend.session_secret.dotenv_values', return_value={}):
                with self.assertRaises(ValueError):
                    load_session_secret()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'session.json')
            with open(path, 'w') as target:
                json.dump({'SECRET_KEY': 'test-private-key'}, target)
            with patch.dict(os.environ, {'APP_SECRET_FILE': path}, clear=True):
                self.assertEqual(load_session_secret(), 'test-private-key')
                os.remove(path)
                with self.assertRaises(FileNotFoundError):
                    load_session_secret()

    def test_authentication_and_todo_crud_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'test.db')
            with sqlite3.connect(path) as db:
                db.executescript('''
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL
                    );
                    CREATE TABLE items (
                        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                        name TEXT NOT NULL, description TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
                ensure_account_schema(db)

            def get_db():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db

            with patch.dict(app.config, {'TESTING': True, 'get_db': get_db}):
                with app.test_client() as client:
                    for endpoint in ['/auth/login', '/auth/register']:
                        self.assertEqual(client.post(endpoint, json={}).status_code, 400)
                    self.assertEqual(client.get('/items/').status_code, 401)
                    credentials = {'email': 'test-user@example.com', 'password': 'TestPassword123'}
                    self.assertEqual(client.post('/auth/register', json=credentials).status_code, 200)
                    self.assertEqual(client.get('/auth/me').get_json()['user'], 'test-user@example.com')
                    response = client.post('/items/', json={'name': 'Test todo', 'description': 'Keep items'})
                    self.assertEqual(response.status_code, 201)
                    item_id = response.get_json()['id']
                    self.assertEqual(len(client.get('/items/').get_json()['items']), 1)
                    self.assertEqual(client.put('/items/{}'.format(item_id), json={'name': 'Updated todo'}).status_code, 200)
                    self.assertEqual(client.get('/items/{}'.format(item_id)).get_json()['item']['name'], 'Updated todo')
                    self.assertEqual(client.post('/auth/logout').status_code, 200)
                    self.assertEqual(client.get('/items/').status_code, 401)
                    self.assertEqual(client.post('/auth/login', json=credentials).status_code, 200)
                    self.assertEqual(len(client.get('/items/').get_json()['items']), 1)
                    self.assertEqual(client.delete('/items/{}'.format(item_id)).status_code, 200)
                    self.assertEqual(client.get('/items/').get_json()['items'], [])


if __name__ == '__main__':
    unittest.main()
