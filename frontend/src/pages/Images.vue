<template>
  <div class="mx-auto max-w-5xl">
    <div class="mb-5 flex items-center gap-3">
      <p class="text-sm text-ink-gray-5">Baked snapshots ready to install.</p>
      <Button
        class="ml-auto"
        variant="subtle"
        icon-left="lucide-refresh-cw"
        :loading="loading"
        label="Refresh"
        @click="load"
      />
    </div>

    <div
      v-if="note || noteError"
      class="mb-4 rounded-md px-3 py-2 text-p-sm"
      :class="noteError ? 'bg-surface-red-2 text-ink-red-8' : 'bg-surface-blue-2 text-ink-blue-8'"
    >
      {{ noteError || note }}
    </div>

    <div v-if="loading && !images.length" class="space-y-2">
      <div
        v-for="i in 4"
        :key="i"
        class="h-16 animate-pulse rounded-lg border border-outline-gray-2 bg-surface-gray-1"
      />
    </div>

    <div v-else-if="error" class="py-12">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="images.length" class="overflow-hidden rounded-xl border border-outline-gray-2">
      <table class="w-full text-sm">
        <thead class="bg-surface-gray-1 text-ink-gray-5">
          <tr class="text-left">
            <th class="px-4 py-2.5 font-medium">Recipe</th>
            <th class="px-4 py-2.5 font-medium">Kind</th>
            <th class="px-4 py-2.5 font-medium">Size</th>
            <th class="px-4 py-2.5 font-medium">Location</th>
            <th class="px-4 py-2.5 font-medium">Provenance</th>
            <th class="px-4 py-2.5 font-medium">Created</th>
            <th class="px-4 py-2.5 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-outline-gray-2">
          <tr v-for="image in images" :key="image.id" class="hover:bg-surface-gray-1">
            <td class="px-4 py-3">
              <div class="font-medium text-ink-gray-9">{{ image.recipe }}</div>
              <div class="text-xs text-ink-gray-5">
                <span v-if="image.version">v{{ image.version }}</span>
                <span v-if="image.base_image"> · {{ image.base_image }}</span>
              </div>
            </td>
            <td class="px-4 py-3">
              <Badge :label="image.kind" :theme="kindTheme(image.kind)" variant="subtle" size="sm" />
            </td>
            <td class="px-4 py-3 text-ink-gray-7">{{ fmtBytes(image.size_bytes) }}</td>
            <td class="px-4 py-3">
              <div class="text-ink-gray-7">{{ image.location?.type }}</div>
              <div class="max-w-[16rem] truncate text-xs text-ink-gray-5" :title="image.location?.uri">
                {{ image.location?.uri }}
              </div>
            </td>
            <td class="px-4 py-3 text-xs text-ink-gray-6">
              <span v-if="provenanceText(image)">{{ provenanceText(image) }}</span>
              <span v-else class="text-ink-gray-4">—</span>
            </td>
            <td class="px-4 py-3 text-ink-gray-6">{{ fmtDateTime(image.created_at) }}</td>
            <td class="px-4 py-3 text-right">
              <Button
                v-if="canPropagate(image)"
                variant="subtle"
                icon-left="lucide-share-2"
                :loading="propagatingId === image.id"
                label="Propagate"
                @click="onPropagate(image)"
              />
              <span v-else class="text-ink-gray-4" title="Only a promoted atlas-base-image can be fanned out">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <EmptyState
      v-else
      class="mt-6"
      icon="lucide-hard-drive"
      title="No images yet"
      description="Bake a recipe and its snapshots will show up here."
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Badge, Button, ErrorMessage } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { useImages } from '@/composables/useImages'
import { fmtBytes, fmtDateTime, kindTheme } from '@/utils/format'

const { images, loading, error, load, propagate } = useImages()

const propagatingId = ref('')
const note = ref('')
const noteError = ref('')

// Only a promoted local base image (atlas-base-image) can be fanned out host-to-host; a
// from-URL/s3/local artifact is placed differently, so the button is hidden for those.
function canPropagate(image) {
  return image.location?.type === 'atlas-base-image'
}

async function onPropagate(image) {
  propagatingId.value = image.id
  note.value = ''
  noteError.value = ''
  try {
    const result = await propagate(image.id)
    const servers = result.servers || []
    note.value =
      `Propagating ${result.image} from ${result.source} → ${servers.length} host(s). ` +
      `Atlas is syncing them in the background.`
  } catch (caught) {
    noteError.value = caught.message || 'Failed to start propagation'
  } finally {
    propagatingId.value = ''
  }
}

function provenanceText(image) {
  const p = image.provenance || {}
  const parts = []
  if (p.builder) parts.push(p.builder)
  if (p.host || p.server) parts.push(p.host || p.server)
  if (p.bake_id || image.bake_id) parts.push((p.bake_id || image.bake_id).slice(0, 8))
  return parts.join(' · ')
}

onMounted(() => load())
</script>
