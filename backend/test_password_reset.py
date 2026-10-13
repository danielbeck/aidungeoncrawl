import hashlib
import os
import smtplib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.account_schema import ensure_account_schema
from backend.app import app, limiter
from backend.reset_email import reset_email_settings, send_reset_email
from backend.session_secret import load_settings


EMAIL = 'person@example.com'
PASSWORD = 'OriginalPassword123'
NEW_PASSWORD = 'UpdatedPassword456'
NOW = 1800000000


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'accounts.db')
        with self.get_db() as db:
            ensure_account_schema(db)
            db.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT)')
        self.settings = {
            'TESTING': True, 'get_db': self.get_db,
            'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '587',
            'SMTP_SECURE': 'false', 'SMTP_FROM_EMAIL': 'accounts@example.com',
            'SMTP_FROM_NAME': 'Example Accounts', 'SMTP_USERNAME': 'smtp-user',
            'SMTP_PASSWORD': 'test-only-password',
            'PASSWORD_RESET_URL': 'https://example.com/testing/reset-password',
        }
        self.config_patch = patch.dict(app.config, self.settings)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        limiter.reset()
        self.clock = patch('backend.endpoints.auth.time.time', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.client = app.test_client()
        self.assertEqual(self.client.post('/auth/register', json={
            'email': EMAIL, 'password': PASSWORD,
        }).status_code, 200)
        self.mail = patch('backend.endpoints.auth.send_reset_email')
        self.send = self.mail.start()
        self.addCleanup(self.mail.stop)

    def get_db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def request_link(self, email=EMAIL):
        return self.client.post('/auth/forgot-password', json={'email': email})

    def issue_token(self):
        response = self.request_link()
        self.assertEqual(response.status_code, 200)
        return self.send.call_args.args[2]

    def reset(self, token, **overrides):
        details = {'token': token, 'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
        details.update(overrides)
        return self.client.post('/auth/reset-password', json=details)

    def test_email_normalization_uniqueness_and_legacy_accounts(self):
        self.assertEqual(self.client.get('/auth/me').get_json()['user'], EMAIL)
        self.assertEqual(self.client.post('/auth/register', json={
            'email': ' PERSON@EXAMPLE.COM ', 'password': PASSWORD,
        }).status_code, 409)
        self.client.post('/auth/logout')
        response = self.client.post('/auth/login', json={'email': ' PERSON@EXAMPLE.COM ', 'password': PASSWORD})
        self.assertEqual(response.get_json()['email'], EMAIL)
        with self.get_db() as db:
            db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', ('old-user', 'unused'))
        self.assertEqual(self.client.post('/auth/login', json={
            'username': 'old-user', 'password': PASSWORD,
        }).status_code, 401)

    def test_token_is_hashed_and_expires_in_exactly_fifteen_minutes(self):
        token = self.issue_token()
        with self.get_db() as db:
            row = db.execute('SELECT * FROM password_resets').fetchone()
        self.assertNotEqual(row['token_hash'], token)
        self.assertEqual(row['token_hash'], hashlib.sha256(token.encode('ascii')).hexdigest())
        self.assertEqual(row['expires_at'] - row['created_at'], 900)
        self.assertNotIn(token, str(self.request_link().get_json()))
        with patch('backend.endpoints.auth.time.time', return_value=NOW + 899):
            self.assertEqual(self.reset(token).status_code, 200)

    def test_token_is_expired_at_exact_boundary(self):
        token = self.issue_token()
        with patch('backend.endpoints.auth.time.time', return_value=NOW + 900):
            self.assertEqual(self.reset(token).status_code, 400)
        self.assertEqual(self.client.post('/auth/login', json={
            'email': EMAIL, 'password': PASSWORD,
        }).status_code, 200)

    def test_reset_is_single_use_and_revokes_other_sessions(self):
        other = app.test_client()
        self.assertEqual(other.post('/auth/login', json={'email': EMAIL, 'password': PASSWORD}).status_code, 200)
        token = self.issue_token()
        self.assertEqual(self.reset(token).status_code, 200)
        self.assertEqual(self.reset(token).status_code, 400)
        self.assertIsNone(self.client.get('/auth/me').get_json()['user'])
        self.assertEqual(other.get('/game/').status_code, 401)
        self.assertIsNone(other.get('/auth/me').get_json()['user'])
        self.assertEqual(self.client.post('/auth/login', json={'email': EMAIL, 'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'email': EMAIL, 'password': NEW_PASSWORD}).status_code, 200)

    def test_unknown_email_and_persistent_cooldown_have_generic_response(self):
        known = self.request_link()
        unknown = self.request_link('unknown@example.com')
        cooldown = app.test_client().post('/auth/forgot-password', json={'email': EMAIL})
        self.assertEqual(known.get_json(), unknown.get_json())
        self.assertEqual(known.get_json(), cooldown.get_json())
        self.send.assert_called_once()

    def test_new_link_replaces_old_link_after_cooldown(self):
        old_token = self.issue_token()
        with patch('backend.endpoints.auth.time.time', return_value=NOW + 60):
            new_token = self.issue_token()
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(self.reset(old_token).status_code, 400)
        self.assertEqual(self.reset(new_token).status_code, 200)

    def test_smtp_failure_rolls_back_and_preserves_previous_link(self):
        token = self.issue_token()
        self.send.side_effect = smtplib.SMTPException('test delivery failure')
        with patch('backend.endpoints.auth.time.time', return_value=NOW + 60):
            with self.assertLogs(app.logger, level='ERROR'):
                self.assertEqual(self.request_link().status_code, 503)
        self.assertEqual(self.reset(token).status_code, 200)

    def test_configuration_failure_is_reported_even_for_unknown_accounts(self):
        with patch.dict(app.config, {'SMTP_PASSWORD': None}):
            with self.assertLogs(app.logger, level='ERROR'):
                self.assertEqual(self.request_link('unknown@example.com').status_code, 503)
        self.send.assert_not_called()

    def test_validation_does_not_consume_token(self):
        token = self.issue_token()
        self.assertEqual(self.reset(token, confirm_password='Different123').status_code, 400)
        for weak in ('short', 'nouppercase123', 'NOLOWERCASE123', 'NoNumbersHere', 'A1' + 'a' * 127):
            self.assertEqual(self.reset(token, password=weak, confirm_password=weak).status_code, 400)
        self.assertEqual(self.reset('invalid-token').status_code, 400)
        self.assertEqual(self.reset(token).status_code, 200)

    def test_malformed_inputs_return_validation_errors(self):
        for endpoint in ('register', 'login', 'forgot-password', 'reset-password'):
            for value in ([], 'text', None, {}, {'email': []}):
                response = self.client.post('/auth/' + endpoint, json=value)
                self.assertIn(response.status_code, (400, 401))
            self.assertEqual(self.client.post('/auth/' + endpoint, data='{',
                                             content_type='application/json').status_code, 400)

    def test_simultaneous_redemption_succeeds_only_once(self):
        token = self.issue_token()

        def redeem(_):
            with app.test_client() as client:
                return client.post('/auth/reset-password', json={
                    'token': token, 'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(redeem, range(2))), [200, 400])

    def test_simultaneous_requests_send_only_one_email(self):
        def request_reset(_):
            with app.test_client() as client:
                return client.post('/auth/forgot-password', json={'email': EMAIL}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(request_reset, range(2))), [200, 200])
        self.send.assert_called_once()

    def test_link_changes_only_its_own_account(self):
        other = app.test_client()
        self.assertEqual(other.post('/auth/register', json={
            'email': 'other@example.com', 'password': PASSWORD,
        }).status_code, 200)
        token = self.issue_token()
        self.assertEqual(self.reset(token).status_code, 200)
        self.assertEqual(other.get('/auth/me').get_json()['user'], 'other@example.com')


class AccountInfrastructureTests(unittest.TestCase):
    def test_migrated_schema_does_not_acquire_a_write_lock_at_startup(self):
        with sqlite3.connect(':memory:') as db:
            ensure_account_schema(db)
            writes = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_CREATE_TABLE,
                      sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_ALTER_TABLE}
            db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
            ensure_account_schema(db)

    def test_migration_is_idempotent_and_preserves_existing_rows(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
                INSERT INTO users VALUES (7, 'legacy', 'existing-hash');
                CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT);
                INSERT INTO items VALUES (9, 7, 'Legacy item data');
            ''')
            ensure_account_schema(db)
            ensure_account_schema(db)
            self.assertEqual(db.execute('SELECT id, username, password_hash, email, auth_version FROM users').fetchone(),
                             (7, 'legacy', 'existing-hash', None, 0))
            self.assertEqual(db.execute('SELECT * FROM items').fetchone(), (9, 7, 'Legacy item data'))

    def test_mail_transport_uses_tls_and_correct_link(self):
        config = {
            'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '587', 'SMTP_SECURE': 'false',
            'SMTP_FROM_EMAIL': 'accounts@example.com', 'SMTP_FROM_NAME': 'Example Accounts',
            'SMTP_USERNAME': 'test-user', 'SMTP_PASSWORD': 'test-password',
            'PASSWORD_RESET_URL': 'https://example.com/sub/reset-password',
        }
        with patch('backend.reset_email.smtplib.SMTP') as smtp:
            send_reset_email(config, EMAIL, 'test-token')
            smtp.assert_called_once_with('smtp.example.com', 587, timeout=15)
            connection = smtp.return_value.__enter__.return_value
            connection.starttls.assert_called_once()
            connection.login.assert_called_once_with('test-user', 'test-password')
            message = connection.send_message.call_args.args[0]
            self.assertEqual(message['To'], EMAIL)
            self.assertEqual(message['From'], 'Example Accounts <accounts@example.com>')
            self.assertIn('https://example.com/sub/reset-password#token=test-token', message.get_content())
            self.assertIn('15 minutes', message.get_content())
        config.update(SMTP_PORT='465', SMTP_SECURE='true')
        with patch('backend.reset_email.smtplib.SMTP_SSL') as smtp:
            send_reset_email(config, EMAIL, 'test-token')
            self.assertEqual(smtp.call_args.args, ('smtp.example.com', 465))
            smtp.return_value.__enter__.return_value.starttls.assert_not_called()

    def test_invalid_mail_settings_are_rejected(self):
        config = {
            'SMTP_HOST': 'smtp.example.com', 'SMTP_FROM_EMAIL': 'accounts@example.com',
            'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'password',
            'PASSWORD_RESET_URL': 'https://example.com/reset-password',
        }
        for change in ({'SMTP_PORT': 'not-a-port'}, {'SMTP_PORT': '70000'}, {'SMTP_SECURE': 'maybe'},
                       {'PASSWORD_RESET_URL': '/reset-password'},
                       {'PASSWORD_RESET_URL': 'https://example.com/reset-password#token=bad'}):
            with self.assertRaises(ValueError):
                reset_email_settings(dict(config, **change))

    def test_plain_http_reset_url_is_accepted_for_certificate_less_domains(self):
        # Some production domains cannot get a TLS certificate; HTTPS is
        # recommended but must not be required for a non-localhost host.
        config = {
            'SMTP_HOST': 'smtp.example.com', 'SMTP_FROM_EMAIL': 'accounts@example.com',
            'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'password',
            'PASSWORD_RESET_URL': 'http://example.com/reset-password',
        }
        port, secure = reset_email_settings(config)
        self.assertEqual((port, secure), (587, False))

    def test_smtp_settings_load_from_local_and_private_configuration(self):
        with patch.dict(os.environ, {'SMTP_HOST': 'environment-host'}, clear=True):
            with patch('backend.session_secret.dotenv_values', return_value={'SMTP_HOST': 'local-host', 'SMTP_PORT': '587'}):
                settings = load_settings()
                self.assertEqual(settings['SMTP_HOST'], 'environment-host')
                self.assertEqual(settings['SMTP_PORT'], '587')
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'private.json')
            with open(path, 'w') as target:
                target.write('{"SECRET_KEY": "test-key", "SMTP_PASSWORD": "private-password"}')
            with patch.dict(os.environ, {'APP_SECRET_FILE': path}, clear=True):
                self.assertEqual(load_settings()['SMTP_PASSWORD'], 'private-password')


if __name__ == '__main__':
    unittest.main()
