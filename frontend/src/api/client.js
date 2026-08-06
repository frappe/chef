import ky from 'ky'

// Every backend route sits under `/api` from the browser's point of view: the
// dev server proxies `/api` -> the FastAPI backend (stripping the prefix), and a
// production build can point this elsewhere via VITE_CHEF_API_BASE.
export const API_PREFIX = import.meta.env.VITE_CHEF_API_BASE ?? '/api'

// EventSource can't send an Authorization header, so the SSE log route is
// allowed unauthenticated on the backend; other calls carry the bearer token.
const TOKEN = import.meta.env.VITE_CHEF_API_TOKEN || 'chef-dev-token'

// Absolute URL for a path (used for EventSource, which bypasses ky).
export function apiUrl(path = '') {
  const suffix = path ? `/${String(path).replace(/^\/+/, '')}` : ''
  return `${API_PREFIX}${suffix}`
}

export function apiErrorMessage(payload, fallback = 'Request failed.') {
  if (typeof payload?.detail === 'string' && payload.detail) return payload.detail
  if (typeof payload?.error === 'string' && payload.error) return payload.error
  if (typeof payload?.message === 'string' && payload.message) return payload.message
  return fallback
}

export const request = ky.create({
  // ky v2 renamed `prefixUrl` -> `prefix` (and allows slashes in the input path).
  prefix: API_PREFIX,
  throwHttpErrors: false,
  // ky's default is 10s; a bake enqueue / recipe validate can legitimately run
  // longer, still well under any reverse-proxy ceiling.
  timeout: 60_000,
  headers: {
    Authorization: `Bearer ${TOKEN}`,
  },
})

// Await a ky response, turning a non-2xx into a thrown Error carrying the
// backend's ErrorOut message. On success, parse and return JSON.
export async function unwrap(responsePromise) {
  const response = await responsePromise
  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      /* non-JSON error body */
    }
    throw new Error(apiErrorMessage(body, `Request failed (${response.status}).`))
  }
  if (response.status === 204) return null
  return response.json()
}
