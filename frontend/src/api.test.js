import assert from 'node:assert/strict'
import test from 'node:test'
import { fetchAPI } from './api.js'

test('API client preserves authenticated requests and returns JSON', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.ok(url.endsWith('/auth/login'))
    assert.equal(options.credentials, 'include')
    assert.equal(options.method, 'POST')
    assert.equal(options.headers['Content-Type'], 'application/json')
    return new Response(JSON.stringify({ success: true, email: 'tester@example.com' }), {
      headers: { 'Content-Type': 'application/json' },
    })
  })
  assert.deepEqual(await fetchAPI('/auth/login', { method: 'POST', credentials: 'include' }), {
    success: true, email: 'tester@example.com',
  })
})

test('API client surfaces backend validation errors', async (t) => {
  t.mock.method(console, 'error', () => {})
  t.mock.method(globalThis, 'fetch', async () => new Response(
    JSON.stringify({ error: 'Password must be at least 8 characters' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    }))
  await assert.rejects(fetchAPI('/auth/register'), /Password must be at least 8 characters/)
})

test('API client explains non-JSON backend responses', async (t) => {
  t.mock.method(console, 'error', () => {})
  t.mock.method(globalThis, 'fetch', async () => new Response('<!DOCTYPE html>', {
    status: 500, headers: { 'Content-Type': 'text/html' },
  }))
  await assert.rejects(fetchAPI('/auth/me'), /unexpected response.*HTTP 500/)
})

test('API client preserves password recovery payloads', async (t) => {
  const payloads = {
    '/auth/forgot-password': { email: 'tester@example.com' },
    '/auth/reset-password': {
      token: 'a'.repeat(43), password: 'NewPassword123', confirm_password: 'NewPassword123',
    },
  }
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const endpoint = Object.keys(payloads).find(path => url.endsWith(path))
    assert.ok(endpoint)
    assert.equal(options.method, 'POST')
    assert.equal(options.credentials, 'include')
    assert.deepEqual(JSON.parse(options.body), payloads[endpoint])
    return new Response(JSON.stringify({ success: true, message: 'Request completed' }), {
      headers: { 'Content-Type': 'application/json' },
    })
  })
  for (const [endpoint, body] of Object.entries(payloads)) {
    assert.equal((await fetchAPI(endpoint, {
      method: 'POST', credentials: 'include', body: JSON.stringify(body),
    })).success, true)
  }
})

test('API client surfaces expired links and email delivery failures', async (t) => {
  t.mock.method(console, 'error', () => {})
  t.mock.method(globalThis, 'fetch', async url => new Response(JSON.stringify({
    error: url.endsWith('/reset-password') ? 'This reset link is invalid or expired.' : 'Unable to send a reset email right now.',
  }), {
    status: url.endsWith('/reset-password') ? 400 : 503,
    headers: { 'Content-Type': 'application/json' },
  }))
  await assert.rejects(fetchAPI('/auth/reset-password'), /invalid or expired/)
  await assert.rejects(fetchAPI('/auth/forgot-password'), /Unable to send/)
})
