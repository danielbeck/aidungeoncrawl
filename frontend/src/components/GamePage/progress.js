const pendingStatuses = new Set(['started', 'waiting', 'received', 'saving'])

export function isProgressPending(event) {
  return pendingStatuses.has(event.status)
}

export function updateProgress(events, event) {
  const index = events.findLastIndex(entry => event.operation_id
    ? entry.operation_id === event.operation_id
    : !entry.operation_id && entry.component === event.component)
  const previous = events[index]
  const startsNewOperation = event.status === 'started'
    && previous && !isProgressPending(previous)

  if (!previous || startsNewOperation) return [...events, event]

  return events.map((entry, position) => position === index ? {
    ...entry,
    ...event,
    reason: event.reason ?? (entry.status === 'waiting' ? undefined : entry.reason),
  } : entry)
}

export function finishProgress(events, error) {
  return events.map(event => isProgressPending(event) ? {
    ...event,
    status: error ? 'interrupted' : 'completed',
    ...(error ? { error } : {}),
  } : event)
}
