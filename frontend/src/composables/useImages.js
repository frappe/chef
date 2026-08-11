import { ref } from 'vue'
import { imagesApi } from '@/api/images'

const images = ref([])
const loading = ref(false)
const error = ref('')

export function useImages() {
  async function load() {
    loading.value = true
    error.value = ''
    try {
      images.value = await imagesApi.list()
    } catch (caught) {
      error.value = caught.message || 'Failed to load images'
    } finally {
      loading.value = false
    }
  }

  // Fire the fleet fan-out for one image; Atlas backgrounds the actual sync, so this
  // resolves as soon as the fan-out is queued and returns its {image, source, servers}.
  async function propagate(id, servers = null) {
    return imagesApi.propagate(id, servers)
  }

  return { images, loading, error, load, propagate }
}
