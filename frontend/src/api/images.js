import { request, unwrap } from './client'

export const imagesApi = {
  // GET /images/ -> ImageOut[]
  list: () => unwrap(request.get('images/')),
  // GET /images/{id} -> ImageOut
  get: (id) => unwrap(request.get(`images/${encodeURIComponent(id)}`)),
}
