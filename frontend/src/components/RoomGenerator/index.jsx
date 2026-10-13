import { useState } from 'react'
import { runLLMCall } from '../../api'
import roomSchema from '@shared/llm/describe_room.json'
import styles from './RoomGenerator.module.scss'

const ALL_EXITS = roomSchema.properties.exits.items.enum

function RoomGenerator() {
  const [idea, setIdea] = useState('')
  const [room, setRoom] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!idea.trim() || loading) return

    setLoading(true)
    setError(null)

    try {
      const result = await runLLMCall('describe_room', idea)
      setRoom(result.data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={styles.generator} aria-labelledby="room-generator-heading">
      <h2 id="room-generator-heading">Room Generator</h2>
      <form className={styles.form} onSubmit={handleSubmit}>
        <label htmlFor="room-idea" className={styles.label}>Room idea</label>
        <input
          id="room-idea"
          className={styles.input}
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="e.g. a flooded crypt"
          maxLength={2000}
        />
        <button className={styles.button} type="submit" disabled={loading || !idea.trim()}>
          {loading ? 'Generating...' : 'Generate'}
        </button>
      </form>

      {error && <p className={styles.error} role="alert">{error}</p>}

      {room && (
        <article className={styles.room} aria-live="polite">
          <h3>{room.title}</h3>
          <p>{room.description}</p>
          <h4 className={styles.exitsHeading}>Exits</h4>
          <ul className={styles.exits}>
            {ALL_EXITS.map((direction) => {
              const open = room.exits.includes(direction)
              return (
                <li key={direction} className={open ? styles.open : styles.closed}>
                  {direction}: {open ? 'open' : 'blocked'}
                </li>
              )
            })}
          </ul>
        </article>
      )}
    </section>
  )
}

export default RoomGenerator
