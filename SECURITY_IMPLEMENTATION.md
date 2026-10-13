# Security Implementation Guide

## Overview
This document details all security fixes that have been implemented in this codebase.

## ✅ Implemented Security Features

### 1. Rate Limiting
**Status:** ✅ Implemented

**Changes:**
- Added `Flask-Limiter` package to requirements.txt
- Configured global rate limits: 200 requests/day, 50 requests/hour
- Authentication endpoints have rate limit checks built-in
- Returns HTTP 429 (Too Many Requests) when limits exceeded

**Configuration:**
```python
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)
```

**Note:** In production, consider using Redis for distributed rate limiting:
```python
storage_uri="redis://localhost:6379"
```

---

### 2. CSRF Protection
**Status:** ✅ Implemented

**Changes:**
- Added `Flask-WTF` package to requirements.txt
- Initialized CSRFProtect middleware
- Configured CSRF settings for session-based tokens

**Configuration:**
```python
csrf = CSRFProtect(app)
app.config['WTF_CSRF_TIME_LIMIT'] = None  # Session-based
app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # Manual protection
```

**Usage:** CSRF protection is enabled by default. To exempt specific endpoints (like public APIs), use:
```python
@csrf.exempt
@bp.route('/public-endpoint')
def public_endpoint():
    pass
```

---

### 3. Secure Session Cookies
**Status:** ✅ Implemented

**Changes:**
- Configured secure cookie settings
- Added session timeout (24 hours)
- Made sessions permanent with proper lifetime

**Configuration:**
```python
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only (production)
app.config['SESSION_COOKIE_HTTPONLY'] = True  # No JavaScript access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
```

**Note:** `SESSION_COOKIE_SECURE` is automatically set based on `FLASK_ENV` environment variable.

---

### 4. Input Validation & Length Limits
**Status:** ✅ Implemented

**Changes:**

#### Email Validation
- Required email-format address, maximum 254 characters
- Whitespace trimmed and addresses normalized to lowercase
- Case-insensitive uniqueness and lookup

#### Password Validation
- Minimum length: 8 characters
- Maximum length: 128 characters
- Must contain:
  - At least one uppercase letter
  - At least one lowercase letter
  - At least one number

#### Item Name/Description Validation
- Name: max 255 characters (required, non-empty)
- Description: max 1000 characters

**Code Example:**
```python
from backend.endpoints.auth import validate_email

valid, error = validate_email('person@example.com')
```

Password recovery uses 256-bit random, single-use tokens stored only as hashes,
with expiry enforced at 15 minutes. Issuance is serialized and throttled per
account in SQLite, including CGI deployments. Resetting a password increments
the account's session version, revoking previously issued sessions. SMTP uses
verified STARTTLS or implicit TLS. See [the account API guide](backend/README.md#api-examples).

---

### 5. Production CORS Configuration
**Status:** ✅ Implemented

**Changes:**
- CORS origins now read from `ALLOWED_ORIGINS` environment variable
- Falls back to localhost origins for development
- Supports multiple production domains

**Environment Variable:**
```bash
# .env file
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

**Code:**
```python
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '').split(',') if os.environ.get('ALLOWED_ORIGINS') else [
    "http://localhost:3000",
    "http://localhost:3001",
    # ... dev origins
]
```

---

### 6. Security Headers
**Status:** ✅ Implemented

**Changes:**
- Added security headers middleware to all responses

**Headers Added:**
```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
```

**Implementation:**
```python
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    # ... other headers
    return response
```

---

### 7. Request Size Limiting
**Status:** ✅ Implemented

**Changes:**
- Maximum request size: 16KB
- Prevents DoS attacks via large payloads

**Configuration:**
```python
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024  # 16KB
```

---

### 8. Session Timeout
**Status:** ✅ Implemented

**Changes:**
- Configured 24-hour session lifetime
- Sessions made permanent with proper timeout
- Automatic cleanup of expired sessions

**Configuration:**
```python
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
session.permanent = True  # Applied on login/register
```

---

### 9. Comprehensive Error Handling
**Status:** ✅ Implemented

**Changes:**
- Added specific error handlers for common HTTP errors (400, 401, 403, 404, 405, 413, 429, 500)
- Environment-aware error messages (detailed in dev, generic in production)
- Proper logging of all errors with IP addresses and stack traces
- Database connection cleanup in error scenarios
- JSON parsing error handling
- Validation error responses with specific messages

**Error Handlers:**
```python
@app.errorhandler(400)  # Bad Request
@app.errorhandler(401)  # Unauthorized
@app.errorhandler(403)  # Forbidden
@app.errorhandler(404)  # Not Found
@app.errorhandler(405)  # Method Not Allowed
@app.errorhandler(413)  # Request Too Large
@app.errorhandler(429)  # Rate Limit Exceeded
@app.errorhandler(500)  # Internal Server Error
@app.errorhandler(Exception)  # Catch-all
```

**Error Response Format:**
```json
{
  "error": "Error Type",
  "message": "Descriptive error message"
}
```

**Security Features:**
- Failed login attempts logged with IP addresses
- Database errors caught and logged without exposing internals
- Rate limit violations logged
- Production mode hides sensitive error details

---

## 📋 Configuration Checklist

### Development Setup
1. ✅ Install new dependencies: `pip install -r requirements.txt`
2. ✅ Update `.env` file (use `.env.example` as template)
3. ✅ Test authentication endpoints
4. ✅ Verify rate limiting works

### Production Deployment
1. ⚠️ Set `FLASK_ENV=production` in environment
2. ⚠️ Set `SECRET_KEY` to a secure random value
3. ⚠️ Configure `ALLOWED_ORIGINS` with production domains
4. ⚠️ Use HTTPS (required for secure cookies)
5. ⚠️ Consider Redis for rate limiting storage
6. ⚠️ Monitor rate limit logs
7. ⚠️ Set up proper logging infrastructure

### Example Production `.env`:
```bash
SECRET_KEY=your-64-character-secret-key-here
FLASK_ENV=production
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

---

## 🔒 Additional Security Recommendations

### Implemented ✅
- [x] SQL injection protection (parameterized queries)
- [x] Password hashing (Werkzeug security)
- [x] Session-based authentication
- [x] Authorization checks (user_id validation)
- [x] Rate limiting
- [x] CSRF protection
- [x] Secure session cookies
- [x] Input validation
- [x] Security headers
- [x] Request size limiting
- [x] Session timeout (24 hours)
- [x] Comprehensive error handling with logging
- [x] Email password reset with single-use, 15-minute links and session revocation

### Not Yet Implemented ⚠️
- [ ] Two-Factor Authentication (2FA)
- [ ] Email verification
- [ ] Account lockout after failed attempts
- [ ] Audit logging for security events
- [ ] IP-based blocking/whitelisting
- [ ] Content Security Policy for frontend
- [ ] Subresource Integrity (SRI) for CDN resources
- [ ] Regular security dependency updates
- [ ] Automated security scanning

---

## 🧪 Testing Security Features

### Test Rate Limiting
```bash
# Send multiple requests quickly
for i in {1..15}; do
  curl -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"test"}'
done
```

### Test Input Validation
```bash
# Test weak password
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"testuser@example.com","password":"weak"}'

# Test invalid email
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"not-an-email","password":"ValidPass123"}'
```

### Test Security Headers
```bash
curl -I http://localhost:8000/
# Check for X-Content-Type-Options, X-Frame-Options, etc.
```

---

## 📚 References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Flask Security Best Practices](https://flask.palletsprojects.com/en/2.3.x/security/)
- [Flask-Limiter Documentation](https://flask-limiter.readthedocs.io/)
- [Flask-WTF CSRF Documentation](https://flask-wtf.readthedocs.io/en/stable/csrf.html)

---

## 📝 Changelog

### 2026-10-07
- Replaced username-based login and registration with email addresses.
- Added SMTP password recovery with single-use links expiring after 15 minutes.
- Added password confirmation, persistent resend cooldown, and session revocation.

### 2026-02-07
- ✅ Added Flask-Limiter for rate limiting
- ✅ Added Flask-WTF for CSRF protection
- ✅ Configured secure session cookies
- ✅ Implemented input validation with regex patterns
- ✅ Added password strength requirements
- ✅ Configured production CORS from environment
- ✅ Added security headers middleware
- ✅ Added request size limiting
- ✅ Updated requirements.txt
- ✅ Updated .env.example with ALLOWED_ORIGINS
