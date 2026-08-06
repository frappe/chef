import { ref, onBeforeUnmount } from 'vue'
import { bakesApi } from '@/api/bakes'
import { isBakeActive } from '@/utils/format'

// Per-bake state: one-shot load plus a poll that stops itself once the bake
// reaches a terminal status. The live log terminal is driven separately by
// useTaskStream (SSE); this polling keeps status/steps/images fresh.
export function useBake(id) {
  const bake = ref(null)
  const loading = ref(true)
  const error = ref('')
  let timer = null

  async function load() {
    try {
      bake.value = await bakesApi.get(id)
      error.value = ''
    } catch (caught) {
      error.value = caught.message || 'Failed to load bake'
    } finally {
      loading.value = false
    }
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function poll(intervalMs = 2500) {
    stop()
    timer = setInterval(async () => {
      await load()
      if (bake.value && !isBakeActive(bake.value.status)) stop()
    }, intervalMs)
  }

  onBeforeUnmount(stop)

  return { bake, loading, error, load, poll, stop }
}
