import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchAPI, streamGameTurn } from '../../api'
import { createRequestId } from '../../requestId'
import styles from './GamePage.module.scss'
import { finishProgress, isProgressPending, updateProgress } from './progress'

const progressComponentLabels = {
  inventory: 'Inventory manager',
  map: 'Movement intent',
  npcs: 'NPC manager',
  movement_resolver: 'Movement resolver',
  narrator: 'Narrator',
  comparator: 'Entity matcher',
  turn: 'Turn',
  summarizer: 'History summarizer',
  direct_narrator: 'Direct narrator',
  freeform_narrator: 'Freeform narrator',
  game_master: 'Game master',
  tool: 'World tool',
}

const modeOptions = [
  { value: 'managed', label: 'Managed', title: 'Managers interpret the action, code applies changes, then the narrator describes them.' },
  { value: 'direct', label: 'Direct', title: 'One narrator call describes the outcome and returns the whole updated world.' },
  { value: 'freeform', label: 'Freeform', title: 'Continue the story without updating tracked world state. Existing world data will go stale.' },
  { value: 'tool_using', label: 'Tool using', title: 'The game master uses tools to update locations, items, and NPCs, then narrates the outcome.' },
]

function GamePage() {
  const [game, setGame] = useState(null)
  const [action, setAction] = useState('')
  const [mode, setMode] = useState('managed')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState([])
  const requestId = useRef(null)
  const progressRevision = useRef(0)
  const storyContent = useRef(null)
  const actionInput = useRef(null)
  const restoreActionFocus = useRef(false)

  useEffect(() => {
    if (!submitting && restoreActionFocus.current) {
      restoreActionFocus.current = false
      actionInput.current?.focus({ preventScroll: true })
    }
  }, [submitting])

  useEffect(() => {
    if (window.matchMedia('(min-width: 761px)').matches && storyContent.current) {
      storyContent.current.scrollTop = storyContent.current.scrollHeight
    }
  }, [loading, game?.history.recentEvents, progress, error])

  const prepareHistory = async (gameId, revision) => {
    const update = event => {
      if (progressRevision.current === revision) {
        setProgress(events => updateProgress(events, { component: 'summarizer', ...event }))
      }
    }
    update({ status: 'started', reason: 'Preparing context for the next turn.' })
    try {
      const response = await fetchAPI('/game/prepare', {
        method: 'POST', credentials: 'include', body: JSON.stringify({ game_id: gameId }),
      })
      update({
        status: response.prepared ? 'completed' : 'skipped',
        response, reason: response.reason || 'Next-turn context is ready.'
      })
    } catch (err) {
      update({ status: 'failed', error: err.message })
    }
  }

  const loadGame = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchAPI('/game/', { credentials: 'include' })
      setGame(data.game)
      if (data.game.mode) setMode(data.game.mode)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadGame() }, [loadGame])

  const location = useMemo(
    () => game?.world.locations.find(item => item.id === game.currentLocationId),
    [game],
  )
  const inventory = useMemo(
    () => game?.world.items.filter(item => item.status === 'available'
      && item.holder?.type === 'player' && item.holder.id === game.playerId) || [],
    [game],
  )
  const peopleHere = useMemo(
    () => game?.world.npcs.filter(npc => npc.status === 'active'
      && npc.currentLocationId === game.currentLocationId) || [],
    [game],
  )
  const objectsHere = useMemo(
    () => game?.world.objects.filter(object => object.status === 'present'
      && object.knownByPlayer
      && object.locationId === game.currentLocationId
      && !object.containedInObjectId) || [],
    [game],
  )

  const submitAction = async (event) => {
    event.preventDefault()
    if (!action.trim() || submitting || game?.status !== 'active') return
    restoreActionFocus.current = true
    setSubmitting(true)
    setError('')
    setProgress([])
    const revision = ++progressRevision.current
    requestId.current ||= createRequestId()
    try {
      const data = await streamGameTurn({
        action: action.trim(),
        game_id: game.id,
        request_id: requestId.current,
        mode,
      }, event => {
        if (event.component === 'comparator' && event.llm === false
          && event.status !== 'started' && event.status !== 'failed') return
        setProgress(events => updateProgress(events, event))
      })
      setGame(data.game)
      setAction('')
      requestId.current = null
      setProgress(events => finishProgress(updateProgress(events, {
        component: 'turn',
        status: 'saved',
        response: {
          currentLocationId: data.game.currentLocationId,
          current_location: data.game.world.locations.find(
            location => location.id === data.game.currentLocationId,
          ),
          movement_outcome: data.turn.movement_outcome,
        },
      })))
      // Every mode narrates from the continuity summary, so keep it prepared.
      void prepareHistory(data.game.id, revision)
    } catch (err) {
      setError(err.message)
      setProgress(events => finishProgress(events, err.message))
    } finally {
      setSubmitting(false)
    }
  }

  const startNewGame = async (event) => {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError('')
    try {
      const data = await fetchAPI('/game/new', {
        method: 'POST', credentials: 'include',
        body: JSON.stringify({ starting_point: 'town' }),
      })
      setGame(data.game)
      setMode(data.game.mode || 'managed')
      setAction('')
      setProgress([])
      progressRevision.current += 1
      requestId.current = null
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const changeMode = async (nextMode) => {
    if (submitting || nextMode === mode) return
    setSubmitting(true)
    setError('')
    requestId.current = null
    progressRevision.current += 1
    try {
      const data = await fetchAPI('/game/mode', {
        method: 'POST', credentials: 'include',
        body: JSON.stringify({ game_id: game.id, mode: nextMode }),
      })
      setGame(data.game)
      setMode(data.game.mode)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <p className={styles.status} role="status">Loading your adventure...</p>
  if (!game) return <section className={styles.game}><p className={styles.error} role="alert">{error}</p><button onClick={loadGame}>Try again</button></section>

  return (
    <section className={styles.game} aria-label="Adventure">
      <header className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Your&nbsp;
            <fieldset className={styles.modeSwitch} disabled={submitting}>
              <legend>Storytelling mode</legend>
              {modeOptions.map(option => <label key={option.value} title={option.title}>
                <input type="radio" name="mode" value={option.value}
                  checked={mode === option.value}
                  onChange={() => changeMode(option.value)} />
                {option.label}
              </label>)}
            </fieldset>
            &nbsp;adventure
          </p>
          <h2>{location?.name || 'Unknown location'}</h2>
        </div>
        <form className={styles.newGameForm} onSubmit={startNewGame}>
          <button className={styles.secondaryButton} type="submit" disabled={submitting}>
            New game
          </button>
        </form>
      </header>

      <div className={styles.layout}>
        <div className={styles.story}>
          <div ref={storyContent} className={styles.storyContent} tabIndex={0} role="region" aria-label="Game history and turn progress">
            <ol className={styles.events} aria-live="polite">
              {game.history.recentEvents.map((event, index) => (
                <li key={`${index}-${event.kind}`} className={event.kind === 'player_action' ? styles.playerEvent : styles.narrationEvent}>
                  <span>{event.kind === 'player_action' ? 'You' : 'Narrator'}</span>
                  <p>{event.content}</p>
                </li>
              ))}
            </ol>
            {progress.length > 0 && <details className={styles.progress} aria-label="Turn progress">
              <summary>
                Turn progress
                <span className={styles.progressSummaryStatuses}>
                  {progress.map((event, index) => {
                    const label = event.tool_name || progressComponentLabels[event.component] || event.component
                    const failed = event.status === 'failed' || event.status === 'interrupted'
                    return <span
                      key={`${event.component}-${index}`}
                      className={styles.progressSummaryStatus}
                      role="img"
                      aria-label={`${label}: ${event.status}`}
                      title={`${label}: ${event.status}`}
                    >
                      {isProgressPending(event)
                        ? <span className={styles.spinner} aria-hidden="true" />
                        : failed
                          ? <span className={styles.progressFailure} aria-hidden="true">!</span>
                          : <span className={styles.progressComplete} aria-hidden="true">✓</span>}
                    </span>
                  })}
                </span>
              </summary>
              <ol role="log" aria-live="polite" aria-relevant="additions text">
                {progress.map((event, index) => (
                  <li key={index} aria-busy={isProgressPending(event)}>
                    {isProgressPending(event) && <span className={styles.spinner} aria-hidden="true" />}
                    <strong>{event.tool_name || progressComponentLabels[event.component] || event.component}</strong>
                    {' — '}{event.status}{event.attempt ? ` (attempt ${event.attempt})` : ''}
                    {event.provider && <small>{' '}{event.provider}/{event.model}</small>}
                    {event.reason && <p>{event.reason}</p>}
                    {event.request && <details>
                      <summary>Request details</summary>
                      <pre>{JSON.stringify(event.request, null, 2)}</pre>
                    </details>}
                    {event.proposed_entity && <details>
                      <summary>Proposed entity</summary>
                      <pre>{JSON.stringify({
                        entity_type: event.entity_type,
                        proposed_entity: event.proposed_entity,
                      }, null, 2)}</pre></details>}
                    {event.matched_entity && <details>
                      <summary>Matched entity</summary>
                      <pre>{JSON.stringify({
                        matched_entity: event.matched_entity,
                      }, null, 2)}</pre></details>}
                    {event.response && <details>
                      <summary>Response</summary>
                      <pre>{JSON.stringify(event.response, null, 2)}</pre>
                    </details>}
                    {event.error && <p className={styles.error}>{event.error}</p>}
                  </li>
                ))}
              </ol>
            </details>}
            {error && <p className={styles.error} role="alert">{error}</p>}
          </div>
          {game.status === 'active' ? <>
            <form className={styles.actionForm} onSubmit={submitAction}>
              <textarea
                ref={actionInput}
                id="player-action"
                aria-label="What do you do?"
                aria-describedby="action-input-hint"
                value={action}
                onKeyDown={event => {
                  if (event.key === 'Enter' && !event.shiftKey
                    && !event.ctrlKey && !event.altKey && !event.metaKey
                    && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                    event.preventDefault()
                    if (action.trim() && !submitting) event.currentTarget.form.requestSubmit()
                  }
                }}
                onChange={event => {
                  if (event.target.value !== action) requestId.current = null
                  setAction(event.target.value)
                }}
                placeholder="Describe your action..."
                maxLength={2000}
                rows={3}
                disabled={submitting}
              />
              <div className={styles.actionControls}>
                <p id="action-input-hint" className={styles.actionHint}>
                  <kbd>Enter</kbd> to send · <kbd>Shift + Enter</kbd> for a new line
                </p>
                <span role="status">{submitting ? 'The world responds...' : ''}</span>
                <button type="submit" aria-label="Send action"
                  title="Send action (Enter)" disabled={submitting || !action.trim()}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                    strokeLinejoin="round" aria-hidden="true" focusable="false">
                    <path d="m5 12 7-7 7 7M12 5v14" />
                  </svg>
                </button>
              </div>
            </form>
          </> : <div className={styles.ended} role="status">
            <h3>{game.endReason === 'character_died' ? 'Your character has died.' : 'Adventure ended.'}</h3>
            <p>Start a new game when you are ready to return to the story.</p>
          </div>}
        </div>

        <aside className={styles.sidebar} aria-label="Current world state">
          <section>
            <h3>Exits</h3>
            {location?.exits.some(exit => exit.identified !== false)
              ? <ul>{location.exits.filter(exit => exit.identified !== false).map(exit => <li key={exit.id}>
                {exit.description}{!exit.traversable && <span> (blocked)</span>}
                {!exit.destinationId && <span> (unexplored)</span>}
              </li>)}</ul>
              : <p>None identified</p>}
          </section>
          <section>
            <h3>Inventory</h3>
            {inventory.length ? <ul>{inventory.map(item => <li key={item.id}>{item.name}</li>)}</ul> : <p>Empty</p>}
          </section>
          <section>
            <h3>Here</h3>
            {peopleHere.length || objectsHere.length
              ? <ul>
                {peopleHere.map(npc => <li key={npc.id}>{npc.name}</li>)}
                {objectsHere.map(object => <li key={object.id}>{object.name}</li>)}
              </ul>
              : <p>No one or nothing notable identified</p>}
          </section>
        </aside>
      </div>
    </section>
  )
}

export default GamePage
