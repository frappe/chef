import { ref } from 'vue'
import { recipesApi } from '@/api/recipes'

// Module-scoped: the recipe list is shared across the app (no Pinia).
const recipes = ref([])
const loading = ref(false)
const error = ref('')
let loaded = false

export function useRecipes() {
  async function load(force = false) {
    if (loaded && !force) return
    loading.value = true
    error.value = ''
    try {
      recipes.value = await recipesApi.list()
      loaded = true
    } catch (caught) {
      error.value = caught.message || 'Failed to load recipes'
    } finally {
      loading.value = false
    }
  }

  return { recipes, loading, error, load }
}
