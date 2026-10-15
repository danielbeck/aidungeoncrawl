# Security Implementation and Limitations

This describes current controls and configuration boundaries, not a security
audit or a guarantee of protection. Setup belongs in [CONFIG.md](CONFIG.md);
account API behavior belongs in [backend/README.md](backend/README.md#account-api).

## Credentials and sessions

- A non-empty `SECRET_KEY` is required in every environment, without a hardcoded
  fallback. Keep `.env` private and out of version control.
- Deployment stores session, SMTP, and LLM credentials outside the web root.
  See [private server settings](CONFIG.md#private-server-settings).
- Passwords are hashed with Werkzeug. Registration requires an email address
  and an 8-128-character password with uppercase, lowercase, and a number.
- Login and registration create permanent sessions with a 24-hour lifetime.
  Cookies are `HttpOnly` and `SameSite=Lax`; `Secure` is enabled when the loaded
  settings specify `FLASK_ENV=production`.
- Password recovery uses hashed, single-use, 256-bit random tokens expiring after
  15 minutes. SQLite enforces a per-account resend cooldown across CGI processes.
  Successful resets revoke existing sessions. SMTP uses verified TLS.

Legacy username-only accounts remain stored but cannot authenticate; users need
new email-based accounts. Account schema migrations are additive.

## Request controls

| Control | Current behavior and boundary |
|---|---|
| Authentication and ownership | Gameplay requires a session and loads the authenticated user's saved game. |
| SQL parameters | Account and gameplay queries use parameterized values. |
| Input validation | Account fields and gameplay/LLM contracts are validated before use. |
| Request size | Flask's request-body limit is 16 KiB. |
| Rate limiting | Flask-Limiter applies IP-based defaults of 200/day and 50/hour with in-memory storage. |
| CORS | Credentialed requests are restricted to configured origins, with localhost development defaults; see [runtime settings](CONFIG.md#other-runtime-settings). |
| CSRF configuration | Flask-WTF is initialized, but `WTF_CSRF_CHECK_DEFAULT=False` disables automatic checks. Current endpoint modules do not explicitly invoke CSRF protection; do not describe CSRF enforcement as implemented. |

In-memory rate limits are process-local, not durable or shared between workers
or CGI requests. A shared backend such as Redis requires an explicit application
configuration change. CORS and `SameSite` cookies do not replace CSRF validation.

## Response headers

Flask adds these API response headers:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
```

The CSP is for API responses, not a frontend document policy. The legacy
`X-XSS-Protection` header is not a substitute for output safety or CSP.
HSTS only has an effect when received over HTTPS.

## Errors, state integrity, and logs

Flask has JSON handlers for common HTTP failures and uncaught exceptions.
Endpoint errors can also return a single `error` message; not all responses use
the handlers' `error`/`message` shape.

Unhandled-error detail is controlled by the process `FLASK_ENV`, while cookie
settings use loaded settings. Deployment writes `FLASK_ENV` into private JSON;
the CGI entry point does not export that value into the process environment.
Do not assume that the production cookie setting also enables generic unhandled
errors; configure the process environment separately where required.

Gameplay validates changes on a candidate state and commits atomically, with
version checks and request-ID deduplication. Invalid responses and processing
failures leave saved gameplay unchanged.

Application errors are logged in `backend/logs/flask_error.log`; LLM diagnostics
are also written to a rotating `backend/logs/llm.log`. These can contain player
actions, narrative context, and model output. Keep logs and databases private.
Logging exists, but this is not a dedicated, comprehensive security audit trail.

## Operational checklist

- Use HTTPS, a stable random session key, and private configuration permissions.
- Set any required CORS process environment explicitly.
- Do not assume automatic CSRF validation or cross-process rate limiting exists.
- Restrict access to backend data, logs, private settings, and provider keys.
- Keep dependencies updated and review deployment-specific behavior.
- For regression tests, see the [backend guide](backend/README.md#testing).

Two-factor authentication, email verification, dedicated failed-login account
lockout, and a frontend CSP are not provided by the controls described above.

## Change history

- **2026-10-07:** Email-based accounts, SMTP recovery, resend cooldown, password
  confirmation, and session revocation.
- **2026-02-07:** Rate-limiter and Flask-WTF setup, cookie settings, input checks,
  configurable CORS, API headers, and request-size limits. Flask-WTF setup alone
  did not enable endpoint CSRF enforcement.
