import assert from 'node:assert/strict'
import test from 'node:test'
import { finishProgress, isProgressPending, updateProgress } from './progress.js'

test('tool operations retain execution order around nested comparator calls', () => {
  let events = []
  for (const event of [
    { component: 'game_master', operation_id: 'gm1', status: 'started' },
    { component: 'game_master', operation_id: 'gm1', status: 'completed' },
    { component: 'tool', operation_id: 'tool1', status: 'started', tool_name: 'create_item' },
    { component: 'comparator', operation_id: 'compare1', status: 'started', parent_operation_id: 'tool1' },
    { component: 'comparator', operation_id: 'compare1', status: 'completed' },
    { component: 'tool', operation_id: 'tool1', status: 'completed' },
    { component: 'tool', operation_id: 'tool2', status: 'started', tool_name: 'pick_up_item' },
    { component: 'tool', operation_id: 'tool2', status: 'completed' },
    { component: 'game_master', operation_id: 'gm2', status: 'started' },
  ]) events = updateProgress(events, event)
  assert.deepEqual(events.map(event => event.operation_id), ['gm1', 'tool1', 'compare1', 'tool2', 'gm2'])
  assert.deepEqual(events.map(event => event.status), ['completed', 'completed', 'completed', 'completed', 'started'])
})

test('one operation updates in place and preserves request and provider details', () => {
  let events = []
  for (const event of [
    { component: 'comparator', status: 'started', request: { name: 'Tavern' } },
    { component: 'map', status: 'started' },
    { component: 'comparator', status: 'received', response: { outcome: 'existing', entity_id: 'tavern' }, provider: 'test' },
    { component: 'comparator', status: 'completed', matched_entity: { name: 'Tavern' } },
  ]) events = updateProgress(events, event)
  assert.equal(events.length, 2)
  assert.equal(events[0].status, 'completed')
  assert.deepEqual(events[0].request, { name: 'Tavern' })
  assert.equal(events[0].provider, 'test')
  assert.equal(isProgressPending(events[0]), false)
  assert.equal(isProgressPending(events[1]), true)
})

test('sequential calls remain distinct while waiting and saving update in place', () => {
  let events = updateProgress([], { component: 'summarizer', status: 'started' })
  events = updateProgress(events, { component: 'summarizer', status: 'completed' })
  events = updateProgress(events, { component: 'summarizer', status: 'started' })
  assert.equal(events.length, 2)
  events = updateProgress(events, { component: 'narrator', status: 'waiting', reason: 'Waiting' })
  events = updateProgress(events, { component: 'narrator', status: 'started' })
  assert.equal(events.length, 3)
  assert.equal(events[2].reason, undefined)
  events = updateProgress(events, { component: 'turn', status: 'started' })
  events = updateProgress(events, { component: 'turn', status: 'saving' })
  events = updateProgress(events, { component: 'turn', status: 'saved' })
  assert.equal(events.length, 4)
  assert.equal(events[3].status, 'saved')
})

test('corrections update completed entries and failed streams stop every spinner', () => {
  let events = updateProgress([], { component: 'inventory', status: 'completed' })
  events = updateProgress(events, { component: 'inventory', status: 'corrected', reason: 'Corrected' })
  events = updateProgress(events, { component: 'turn', status: 'saving' })
  assert.equal(events.length, 2)
  const failed = finishProgress(events, 'Connection lost')
  assert.equal(failed[0].status, 'corrected')
  assert.equal(failed[1].status, 'interrupted')
  assert.equal(failed[1].error, 'Connection lost')
  assert.ok(failed.every(event => !isProgressPending(event)))
  assert.equal(finishProgress(events)[1].status, 'completed')
  assert.equal(events[1].status, 'saving')
})
