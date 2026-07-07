export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })

  if (!response.ok) {
    const message = await response.text()
    const error = new Error(message || `API request failed: ${response.status}`)
    error.status = response.status
    throw error
  }

  return response.json()
}
