import sqlite3
import hashlib
import secrets
import smtplib
import time
from contextlib import closing
from flask import Blueprint, request, jsonify, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import BadRequest, UnsupportedMediaType
import re
try:
    from backend.reset_email import reset_email_settings, send_reset_email
except ImportError:
    from reset_email import reset_email_settings, send_reset_email

bp = Blueprint('auth', __name__, url_prefix='/auth')

def get_db():
    return current_app.config['get_db']()

def validate_email(email):
    if not isinstance(email, str) or not email.strip():
        return False, 'Email is required'
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        return False, 'Enter a valid email address'
    return True, None

def validate_password(password):
    """Validate password strength"""
    if not password or not isinstance(password, str):
        return False, 'Password is required'
    if len(password) < 8:
        return False, 'Password must be at least 8 characters'
    if len(password) > 128:
        return False, 'Password must be less than 128 characters'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one number'
    return True, None

def read_data():
    try:
        data = request.get_json()
    except (BadRequest, UnsupportedMediaType):
        return None
    return data if isinstance(data, dict) and data else None


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else value


@bp.before_app_request
def validate_session():
    user_id = session.get('user_id')
    if user_id is None:
        return
    try:
        with closing(get_db()) as db:
            user = db.execute('SELECT auth_version FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user or session.get('auth_version') != user['auth_version']:
            session.clear()
    except sqlite3.Error:
        current_app.logger.exception('Failed to validate account session')
        return jsonify({'error': 'Unable to validate your session. Please try again.'}), 503


@bp.route('/register', methods=['POST'])
def register():
    data = read_data()
    if data is None:
        return jsonify({'error': 'A JSON object with account details is required'}), 400
    email = normalize_email(data.get('email'))
    password = data.get('password', '')

    valid, error = validate_email(email)
    if not valid:
        return jsonify({'error': error}), 400
    
    # Validate password
    valid, error = validate_password(password)
    if not valid:
        return jsonify({'error': error}), 400
    
    try:
        with closing(get_db()) as db:
            cursor = db.execute(
                'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                (email, email, generate_password_hash(password))
            )
            db.commit()
            session.clear()
            session['user_id'] = cursor.lastrowid
            session['auth_version'] = 0
            session.permanent = True
        return jsonify({'success': True, 'email': email})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Email already registered'}), 409
    except sqlite3.Error:
        current_app.logger.exception('Error during registration')
        return jsonify({'error': 'Registration failed. Please try again.'}), 500

@bp.route('/login', methods=['POST'])
def login():
    data = read_data()
    if data is None:
        return jsonify({'error': 'A JSON object with account details is required'}), 400
    email = normalize_email(data.get('email'))
    password = data.get('password', '')

    if not validate_email(email)[0] or not isinstance(password, str) or not password:
        return jsonify({'error': 'Invalid credentials'}), 401
    
    try:
        with closing(get_db()) as db:
            user = db.execute('SELECT * FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['auth_version'] = user['auth_version']
            session.permanent = True
            return jsonify({'success': True, 'email': user['email']})
        
        # Log failed login attempt
        current_app.logger.warning('Failed login attempt from IP: %s', request.remote_addr)
        return jsonify({'error': 'Invalid credentials'}), 401
    except sqlite3.Error:
        current_app.logger.exception('Error during login')
        return jsonify({'error': 'Login failed. Please try again.'}), 500

@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@bp.route('/me', methods=['GET'])
def me():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'user': None}), 200
    try:
        with closing(get_db()) as db:
            user = db.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
        if user:
            return jsonify({'user': user['email']})
        # User was deleted but session still exists
        session.pop('user_id', None)
        return jsonify({'user': None}), 200
    except sqlite3.Error:
        current_app.logger.exception('Error fetching user info')
        return jsonify({'error': 'Failed to fetch user information'}), 500


@bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = read_data()
    if data is None:
        return jsonify({'error': 'A JSON object with an email address is required'}), 400
    email = normalize_email(data.get('email'))
    valid, error = validate_email(email)
    if not valid:
        return jsonify({'error': error}), 400
    response = {'success': True, 'message': 'If an account exists for that email, a password reset link has been sent. Check your inbox and spam folder.'}
    try:
        reset_email_settings(current_app.config)
        with closing(get_db()) as db:
            # Serialize issuance so simultaneous requests cannot bypass the cooldown.
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT id, email FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone()
            if not user:
                return jsonify(response)
            now = int(time.time())
            previous = db.execute('SELECT created_at FROM password_resets WHERE user_id = ?', (user['id'],)).fetchone()
            if previous and now - previous['created_at'] < 60:
                return jsonify(response)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode('ascii')).hexdigest()
            db.execute('DELETE FROM password_resets WHERE user_id = ?', (user['id'],))
            db.execute(
                'INSERT INTO password_resets (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)',
                (token_hash, user['id'], now, now + 15 * 60)
            )
            send_reset_email(current_app.config, user['email'], token)
            db.commit()
        return jsonify(response)
    except (ValueError, OSError, smtplib.SMTPException, sqlite3.Error):
        current_app.logger.exception('Failed to send password reset email')
        return jsonify({'error': 'Unable to send a reset email right now. Please try again later.'}), 503


@bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = read_data()
    if data is None:
        return jsonify({'error': 'A JSON object with reset details is required'}), 400
    token = data.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return jsonify({'error': 'This reset link is invalid or expired. Please request a new one.'}), 400
    password = data.get('password')
    valid, error = validate_password(password)
    if not valid:
        return jsonify({'error': error}), 400
    if password != data.get('confirm_password'):
        return jsonify({'error': 'Passwords do not match'}), 400
    token_hash = hashlib.sha256(token.encode('ascii')).hexdigest()
    try:
        password_hash = generate_password_hash(password)
        with closing(get_db()) as db:
            db.execute('BEGIN IMMEDIATE')
            reset = db.execute(
                'SELECT user_id FROM password_resets WHERE token_hash = ? AND expires_at > ?',
                (token_hash, int(time.time()))
            ).fetchone()
            if not reset:
                return jsonify({'error': 'This reset link is invalid or expired. Please request a new one.'}), 400
            updated = db.execute(
                'UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE id = ?',
                (password_hash, reset['user_id'])
            )
            if updated.rowcount != 1:
                return jsonify({'error': 'This reset link is invalid or expired. Please request a new one.'}), 400
            db.execute('DELETE FROM password_resets WHERE user_id = ?', (reset['user_id'],))
            db.commit()
        session.clear()
        return jsonify({'success': True, 'message': 'Your password has been updated. Log in with your new password.'})
    except sqlite3.Error:
        current_app.logger.exception('Failed to reset password')
        return jsonify({'error': 'Unable to reset your password. Please try again.'}), 500
