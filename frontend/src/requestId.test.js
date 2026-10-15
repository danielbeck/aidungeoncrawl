import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequestId } from './requestId.js'

test('request ID uses randomUUID when available', () => {
  assert.equal(createRequestId({
    randomUUID: () => 'native-uuid',
  }), 'native-uuid')
})

test('request ID falls back to a cryptographically random UUID v4', () => {
  const requestId = createRequestId({
    getRandomValues: bytes => bytes.fill(0xab),
  })

  assert.match(requestId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.equal(requestId, 'abababab-abab-4bab-abab-abababababab')
})

test('request ID reports when secure randomness is unavailable', () => {
  assert.throws(
    () => createRequestId({}),
    /Secure random number generation is not available/,
  )
})
