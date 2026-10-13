import { useState } from 'react'
import { fetchAPI } from '../../api'
import styles from './LLMDebugWidget.module.scss'

// A minimal response_format used only by this debug tool so it can accept
// any free-form prompt. Real features should define their own structured
// schema for the data they actually need back from the model.
const DEBUG_RESPONSE_SCHEMA = {
  type: 'object',
  properties: {
    response: {
      type: 'string',
      description: "The model's free-form reply to the prompt"
    }
  },
  required: ['response'],
  additionalProperties: false
}

function LLMDebugWidget() {
  const [prompt, setPrompt] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!prompt.trim() || loading) return

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const data = await fetchAPI('/llm/chat', {
        method: 'POST',
        body: JSON.stringify({
          prompt,
          response_format: DEBUG_RESPONSE_SCHEMA,
          schema_name: 'debug_response'
        })
      })

      // The schema asks for {"response": "..."}, but fall back to the raw
      // text if a provider/model ignored the schema (e.g. some Ollama models).
      let displayText = data.response
      try {
        const parsed = JSON.parse(data.response)
        if (parsed && typeof parsed.response === 'string') {
          displayText = parsed.response
        }
      } catch {
        // Not JSON; show the raw response text as-is.
      }

      setResult({ provider: data.provider, model: data.model, displayText })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.widget}>
      <h2>LLM Debug</h2>
      <form onSubmit={handleSubmit}>
        <textarea
          className={styles.textarea}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Enter a message to send to the LLM..."
          rows={4}
        />
        <button className={styles.button} type="submit" disabled={loading || !prompt.trim()}>
          {loading ? 'Sending...' : 'Submit'}
        </button>
      </form>

      {error && <p className={styles.error} role="alert">{error}</p>}

      {result && (
        <div className={styles.result}>
          <p className={styles.meta}>
            Provider: <strong>{result.provider}</strong> &middot; Model: <strong>{result.model}</strong>
          </p>
          <pre className={styles.response}>{result.displayText}</pre>
        </div>
      )}
    </div>
  )
}

export default LLMDebugWidget
