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

  return { images, loading, error, load }
}
