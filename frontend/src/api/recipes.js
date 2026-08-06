import { request, unwrap } from './client'

export const recipesApi = {
  // GET /recipes/ -> RecipeSummary[]
  list: () => unwrap(request.get('recipes/')),
  // GET /recipes/{name} -> RecipeDetail
  get: (name) => unwrap(request.get(`recipes/${encodeURIComponent(name)}`)),
  // POST /recipes/validate -> { ok, errors[] }
  validate: (name, inputs = {}) =>
    unwrap(request.post('recipes/validate', { json: { name, inputs } })),
  // POST /recipes/{name}/bake -> 202 { bake_id, status, links }
  bake: (name, payload) =>
    unwrap(request.post(`recipes/${encodeURIComponent(name)}/bake`, { json: payload })),
}
