import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchAPI, streamGameTurn } from '../../api'
import styles from './GamePage.module.scss'

function GamePage() {
  const [game, setGame] = useState(null)
  const [action, setAction] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState([])
  const requestId = useRef(null)

  const loadGame = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchAPI('/game/', { credentials: 'include' })
      setGame(data.game)
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
    setSubmitting(true)
    setError('')
    setProgress([])
    requestId.current ||= crypto.randomUUID()
    try {
      const data = await streamGameTurn({
        action: action.trim(),
        game_id: game.id,
        request_id: requestId.current,
      }, event => setProgress(events => [...events, event]))
      setGame(data.game)
      setAction('')
      requestId.current = null
      setProgress(events => [...events, { component: 'turn', status: 'saved' }])
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const startNewGame = async () => {
    setSubmitting(true)
    setError('')
    try {
      const data = await fetchAPI('/game/new', { method: 'POST', credentials: 'include' })
      setGame(data.game)
      setAction('')
      setProgress([])
      requestId.current = null
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const stopGame = async () => {
    setSubmitting(true)
    setError('')
    try {
      const data = await fetchAPI('/game/stop', { method: 'POST', credentials: 'include' })
      setGame(data.game)
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
          <p className={styles.eyebrow}>Your adventure</p>
          <h2>{location?.name || 'Unknown location'}</h2>
        </div>
        <button className={styles.secondaryButton} type="button" onClick={startNewGame} disabled={submitting}>
          New game
        </button>
      </header>

      <div className={styles.layout}>
        <div className={styles.story}>
          <ol className={styles.events} aria-live="polite">
            {game.history.recentEvents.map((event, index) => (
              <li key={`${index}-${event.kind}`} className={event.kind === 'player_action' ? styles.playerEvent : styles.narrationEvent}>
                <span>{event.kind === 'player_action' ? 'You' : 'Dungeon Master'}</span>
                <p>{event.content}</p>
              </li>
            ))}
          </ol>
          {progress.length > 0 && <section className={styles.progress} aria-label="Turn progress">
            <h3>Turn progress</h3>
            <p>Intermediate responses are debug information, not committed game state.</p>
            <ol role="log" aria-live="polite" aria-relevant="additions">
              {progress.map((event, index) => (
                <li key={index}>
                  <strong>{({
                    inventory: 'Inventory manager', map: 'Map manager', npcs: 'NPC manager',
                    narrator: 'Dungeon Master', turn: 'Turn',
                  })[event.component] || event.component}</strong>
                  {' — '}{event.status}{event.attempt ? ` (attempt ${event.attempt})` : ''}
                  {event.provider && <small>{' '}{event.provider}/{event.model}</small>}
                  {event.response && <pre>{event.response.narration
                    || JSON.stringify(event.response, null, 2)}</pre>}
                  {event.component === 'narrator' && event.response && <details>
                    <summary>Notable-entity debug details</summary>
                    <pre>{JSON.stringify({
                      action_succeeded: event.response.action_succeeded,
                      game_over: event.response.game_over,
                      notable_elements: event.response.notable_elements,
                    }, null, 2)}</pre>
                  </details>}
                  {event.error && <p className={styles.error}>{event.error}</p>}
                </li>
              ))}
            </ol>
          </section>}
          {error && <p className={styles.error} role="alert">{error}</p>}
          {game.status === 'active' ? <>
            <form className={styles.actionForm} onSubmit={submitAction}>
              <label htmlFor="player-action">What do you do?</label>
              <textarea
                id="player-action"
                value={action}
                onChange={event => {
                  if (event.target.value !== action) requestId.current = null
                  setAction(event.target.value)
                }}
                placeholder="Describe your action..."
                maxLength={2000}
                rows={3}
                disabled={submitting}
              />
              <button type="submit" disabled={submitting || !action.trim()}>
                {submitting ? 'The world responds...' : 'Take action'}
              </button>
            </form>
            <button className={styles.stopButton} type="button" onClick={stopGame} disabled={submitting}>
              End this adventure
            </button>
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
