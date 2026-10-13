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
