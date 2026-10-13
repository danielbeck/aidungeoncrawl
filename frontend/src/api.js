// API configuration for different environments
import { config } from './config.js'


export const API_BASE_URL = import.meta.env?.DEV ? `${config.BASE_URL}api` : config.API_BASE_PATH;

export async function fetchAPI(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })

    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error(`API returned an unexpected response (HTTP ${response.status}). Check that the backend is running and the API URL is correct.`)
    }

    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.error || data.message || `API error: ${response.status} ${response.statusText}`)
    }
    return data
  } catch (error) {
    console.error('API request failed:', error)
    throw error
  }
}

export async function streamGameTurn(body, onProgress) {
  try {
    const response = await fetch(`${API_BASE_URL}/game/turn`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      if (response.headers.get('content-type')?.includes('application/json')) {
        const data = await response.json()
        throw new Error(data.error || `API error: ${response.status}`)
      }
      throw new Error(`API returned an unexpected response (HTTP ${response.status}).`)
    }
    if (!response.headers.get('content-type')?.includes('application/x-ndjson') || !response.body) {
      throw new Error('The backend did not return a gameplay progress stream.')
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        let newline
        while ((newline = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, newline).trim()
          buffer = buffer.slice(newline + 1)
          if (!line) continue
          const event = JSON.parse(line)
          if (event.type === 'progress') onProgress(event)
          else if (event.type === 'complete') {
            if (!event.data?.game) throw new Error('The turn returned an invalid game response.')
            return event.data
          } else if (event.type === 'error') {
            throw new Error(event.data?.error || 'The turn failed.')
          } else if (event.type !== 'heartbeat') {
            throw new Error('The turn returned an unknown progress event.')
          }
        }
        if (done) throw new Error('The progress connection ended before the turn completed. Reload or retry the same action.')
      }
    } finally {
      await reader.cancel()
      reader.releaseLock()
    }
  } catch (error) {
    console.error('Gameplay stream failed:', error)
    throw error
  }
}

// Runs a backend LLMCall registered under `name` (see backend/llm_calls).
// Resolves to { data, provider, model }.
export function runLLMCall(name, input, history) {
  return fetchAPI(`/llm/calls/${encodeURIComponent(name)}`, {
    method: 'POST',
    body: JSON.stringify(history ? { input, history } : { input }),
  })
}
