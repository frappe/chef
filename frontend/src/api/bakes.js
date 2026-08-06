import { apiUrl, request, unwrap } from './client'

export const bakesApi = {
  // GET /bakes/{id} -> BakeStatus
  get: (id) => unwrap(request.get(`bakes/${encodeURIComponent(id)}`)),
  // POST /bakes/{id}/abort
  abort: (id) => request.post(`bakes/${encodeURIComponent(id)}/abort`),
  // GET /bakes/{id}/logs -> SSE. EventSource points here directly (no ky).
  logsUrl: (id) => apiUrl(`bakes/${encodeURIComponent(id)}/logs`),
}
