import { request, unwrap } from './client'

export const releasesApi = {
  // GET /releases/ -> TrackedReleaseOut[]
  list: () => unwrap(request.get('releases/')),
  // GET /releases/refs?repo=<repo> -> { repo, refs }
  refs: (repo) => unwrap(request.get('releases/refs', { searchParams: { repo } })),
  // PUT /releases/ -> TrackedReleaseOut
  setPin: (repo, ref) => unwrap(request.put('releases/', { json: { repo, ref } })),
  // DELETE /releases/{repo} -> 204
  deletePin: (repo) => unwrap(request.delete(`releases/${encodeURIComponent(repo)}`)),
}
