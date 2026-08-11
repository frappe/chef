import { request, unwrap } from './client'

export const imagesApi = {
  // GET /images/ -> ImageOut[]
  list: () => unwrap(request.get('images/')),
  // GET /images/{id} -> ImageOut
  get: (id) => unwrap(request.get(`images/${encodeURIComponent(id)}`)),
  // POST /images/{id}/propagate -> PropagateResult (fan the image out to the fleet)
  propagate: (id, servers = null) =>
    unwrap(request.post(`images/${encodeURIComponent(id)}/propagate`, { json: { servers } })),
}
