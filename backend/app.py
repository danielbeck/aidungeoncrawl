from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
import sqlite3
import os
from datetime import datetime, timedelta
import logging
import traceback
from collections import deque
try:
    from backend.session_secret import load_session_secret, load_settings
    from backend.account_schema import ensure_account_schema
except ImportError:
    from session_secret import load_session_secret, load_settings
    from account_schema import ensure_account_schema

app = Flask(__name__)
app.secret_key = load_session_secret()
settings = load_settings()
for key in ('SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURE', 'SMTP_FROM_EMAIL',
            'SMTP_FROM_NAME', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'PASSWORD_RESET_URL'):
    app.config[key] = settings.get(key)
sub_dir = (settings.get('DEPLOY_SUB_DIR') or '').strip('/')
app.config['PASSWORD_RESET_URL'] = settings.get('PASSWORD_RESET_URL') or (
    'http://localhost:3000/' + (sub_dir + '/' if sub_dir else '') + 'reset-password'
)

# CORS Configuration - read from environment or use dev defaults
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '').split(',') if os.environ.get('ALLOWED_ORIGINS') else [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:3003",
    "http://localhost:3004",
    "http://localhost:3005"
]
# Filter out empty strings
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]

CORS(app, supports_credentials=True, origins=ALLOWED_ORIGINS)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Setup file logging for unexpected exceptions (helps when running under CGI/Apache)
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'flask_error.log')
try:
    logs_dir = os.path.dirname(log_path)
    os.makedirs(logs_dir, exist_ok=True)
except Exception:
    pass
handler = logging.FileHandler(log_path)
handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)


def _trim_log(path, max_lines=10000):
    try:
        # read last max_lines and rewrite file to keep size bounded
        with open(path, 'r') as f:
            dq = deque(f, maxlen=max_lines)
        with open(path, 'w') as f:
            f.writelines(dq)
    except Exception:
        pass

# Database path
# Prefer APP_BASE_DIR set by `api.cgi` when running under CGI/chroot.
BASE_DIR = os.environ.get('APP_BASE_DIR') or os.path.dirname(os.path.abspath(__file__))
DB_PATH = settings.get('DB_PATH') or os.path.join(BASE_DIR, 'database', 'app.db')

# Debug: log APP_BASE_DIR and DB_PATH to flask_error.log
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'flask_error.log'), 'a') as f:
        f.write(f"[DEBUG] APP_BASE_DIR: {BASE_DIR}\n")
        f.write(f"[DEBUG] DB_PATH: {DB_PATH}\n")
except Exception:
    pass

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

with get_db() as schema_db:
    ensure_account_schema(schema_db)
schema_db.close()

def dict_from_row(row):
    """Convert sqlite3.Row to dictionary"""
    return dict(zip(row.keys(), row))

# ============ ROOT ENDPOINT ============

@app.route('/')
def index():
    return jsonify({
        'message': 'Boilerplate API',
        'endpoints': ['/items', '/llm']
    })



# Register Blueprints for endpoints


try:
    # If run as module (python3 -m backend.app)
    from backend.endpoints.items import bp as items_bp
    from backend.endpoints.auth import bp as auth_bp
    from backend.endpoints.llm import bp as llm_bp
except ImportError:
    # If run as script (python3 app.py)
    from endpoints.items import bp as items_bp
    from endpoints.auth import bp as auth_bp
    from endpoints.llm import bp as llm_bp

# Provide DB helpers to Blueprints via app config

app.config['get_db'] = get_db
app.config['dict_from_row'] = dict_from_row
app.config['llm_settings'] = settings

# Secure session configuration
app.config['SESSION_COOKIE_SECURE'] = settings.get('FLASK_ENV') == 'production'  # HTTPS only in production
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)  # Session timeout
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024  # 16KB max request size

# WTF CSRF configuration
app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF tokens don't expire (session-based)
app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # We'll manually protect endpoints

app.register_blueprint(items_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(llm_bp)


@app.after_request
def set_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # CSP for API - very restrictive
    response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
    return response


# ============ ERROR HANDLERS ============

@app.errorhandler(400)
def bad_request(e):
    """Handle bad request errors"""
    app.logger.warning(f'Bad request: {request.url} - {str(e)}')
    return jsonify({
        'error': 'Bad Request',
        'message': 'The request could not be understood or was missing required parameters.'
    }), 400

@app.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access"""
    app.logger.warning(f'Unauthorized access attempt: {request.url} - IP: {request.remote_addr}')
    return jsonify({
        'error': 'Unauthorized',
        'message': 'Authentication is required to access this resource.'
    }), 401

@app.errorhandler(403)
def forbidden(e):
    """Handle forbidden access"""
    app.logger.warning(f'Forbidden access attempt: {request.url} - IP: {request.remote_addr}')
    return jsonify({
        'error': 'Forbidden',
        'message': 'You do not have permission to access this resource.'
    }), 403

@app.errorhandler(404)
def not_found(e):
    """Handle not found errors"""
    return jsonify({
        'error': 'Not Found',
        'message': 'The requested resource was not found.'
    }), 404

@app.errorhandler(405)
def method_not_allowed(e):
    """Handle method not allowed errors"""
    return jsonify({
        'error': 'Method Not Allowed',
        'message': f'The {request.method} method is not allowed for this endpoint.'
    }), 405

@app.errorhandler(413)
def request_entity_too_large(e):
    """Handle request too large errors"""
    app.logger.warning(f'Request too large: {request.url} - IP: {request.remote_addr}')
    return jsonify({
        'error': 'Request Too Large',
        'message': f'Request exceeds maximum allowed size of {app.config["MAX_CONTENT_LENGTH"]} bytes.'
    }), 413

@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Handle rate limit errors"""
    app.logger.warning(f'Rate limit exceeded: {request.url} - IP: {request.remote_addr}')
    return jsonify({
        'error': 'Too Many Requests',
        'message': 'Rate limit exceeded. Please try again later.'
    }), 429

@app.errorhandler(500)
def internal_server_error(e):
    """Handle internal server errors"""
    try:
        tb = traceback.format_exc()
        app.logger.error(f'Internal server error: {request.url}\n{tb}')
        _trim_log(log_path, max_lines=10000)
    except Exception:
        pass
    
    # Don't expose internal error details in production
    if os.environ.get('FLASK_ENV') == 'production':
        return jsonify({
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred. Please try again later.'
        }), 500
    else:
        # In development, provide more details
        return jsonify({
            'error': 'Internal Server Error',
            'message': str(e),
            'type': type(e).__name__
        }), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Handle all uncaught exceptions"""
    try:
        tb = traceback.format_exc()
        app.logger.error(f'Unhandled exception: {request.url}\n{tb}')
        _trim_log(log_path, max_lines=10000)
    except Exception:
        pass
    
    # Don't expose internal error details in production
    if os.environ.get('FLASK_ENV') == 'production':
        return jsonify({
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred. Please try again later.'
        }), 500
    else:
        # In development, provide more details for debugging
        return jsonify({
            'error': 'Internal Server Error',
            'message': str(e),
            'type': type(e).__name__
        }), 500


if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=8000, debug=debug_mode)
