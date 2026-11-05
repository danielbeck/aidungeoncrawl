import assert from 'node:assert/strict'
import test from 'node:test'
import { fetchAPI, runLLMCall, streamGameTurn } from './api.js'

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

test('runLLMCall posts input and optional history to the named call', async (t) => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return new Response(JSON.stringify({
      data: { outcome: 'existing', entity_id: 'crypt' }, provider: 'p', model: 'm',
    }), {
      headers: { 'Content-Type': 'application/json' },
    })

  })

  assert.deepEqual(await runLLMCall('comparator', 'a crypt'), {
    data: { outcome: 'existing', entity_id: 'crypt' }, provider: 'p', model: 'm',
  })
  const history = [{ role: 'user', content: 'hi' }]
  await runLLMCall('comparator', 'a crypt', history)

  assert.ok(requests[0].url.endsWith('/llm/calls/comparator'))
  assert.equal(requests[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(requests[0].options.body), { input: 'a crypt' })
  assert.deepEqual(JSON.parse(requests[1].options.body), { input: 'a crypt', history })
})

test('gameplay stream delivers intermediate responses before completion across chunk boundaries', async (t) => {
  const progress = { type: 'progress', component: 'map', status: 'completed',
    response: { locations: [{
      name: 'Cafe', description: 'A small cafe nearby.', interaction: 'approach',
    }] } }
  const data = { game: { id: 'game-1' }, turn: { narration: 'Done.' } }
  const encoder = new TextEncoder()
  let streamController
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.ok(url.endsWith('/game/turn'))
    assert.equal(options.credentials, 'include')
    assert.equal(options.headers.Accept, 'application/x-ndjson')
    assert.equal(JSON.parse(options.body).request_id, 'request-1')
    return new Response(new ReadableStream({
      start(controller) { streamController = controller },
    }), { headers: { 'Content-Type': 'application/x-ndjson' } })
  })
  const received = []
  const result = streamGameTurn({ request_id: 'request-1' }, event => {
    received.push(event)
    streamController.enqueue(encoder.encode(`${JSON.stringify({ type: 'complete', data })}\n`))
    streamController.close()
  })
  await Promise.resolve()
  const bytes = encoder.encode(`${JSON.stringify(progress)}\n`)
  for (const byte of bytes) streamController.enqueue(Uint8Array.of(byte))
  assert.deepEqual(await result, data)
  assert.deepEqual(received, [progress])
})

test('gameplay stream surfaces terminal failures and premature disconnects', async (t) => {
  t.mock.method(console, 'error', () => {})
  t.mock.method(globalThis, 'fetch', async () => new Response(
    `${JSON.stringify({ type: 'error', status: 502, data: { error: 'State not changed.' } })}\n`,
    { headers: { 'Content-Type': 'application/x-ndjson' } },
  ))
  await assert.rejects(streamGameTurn({}, () => {}), /State not changed/)
  globalThis.fetch = async () => new Response('{"type":"heartbeat"}\n', {
    headers: { 'Content-Type': 'application/x-ndjson' },
  })
  await assert.rejects(streamGameTurn({}, () => {}), /ended before the turn completed/)
  globalThis.fetch = async () => new Response('{"error":"Authentication required"}', {
    status: 401, headers: { 'Content-Type': 'application/json' },
  })
  await assert.rejects(streamGameTurn({}, () => {}), /Authentication required/)
})
