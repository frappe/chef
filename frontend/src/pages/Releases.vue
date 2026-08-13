<template>
  <div class="mx-auto max-w-5xl">
    <div class="mb-5 flex items-center gap-3">
      <p class="text-sm text-ink-gray-5">Repos whose refs Chef tracks and bakes against.</p>
      <Button
        class="ml-auto"
        variant="subtle"
        icon-left="lucide-refresh-cw"
        :loading="loading"
        label="Refresh"
        @click="load"
      />
    </div>

    <div v-if="loading && !releases.length" class="space-y-2">
      <div
        v-for="i in 3"
        :key="i"
        class="h-14 animate-pulse rounded-lg border border-outline-gray-2 bg-surface-gray-1"
      />
    </div>

    <div v-else-if="error" class="py-12">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="releases.length" class="overflow-hidden rounded-xl border border-outline-gray-2">
      <table class="w-full text-sm">
        <thead class="bg-surface-gray-1 text-ink-gray-5">
          <tr class="text-left">
            <th class="px-4 py-2.5 font-medium">Repo</th>
            <th class="px-4 py-2.5 font-medium">Pinned ref</th>
            <th class="px-4 py-2.5 font-medium">SHA</th>
            <th class="px-4 py-2.5 font-medium">Resolved at</th>
            <th class="px-4 py-2.5 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-outline-gray-2">
          <tr v-for="release in releases" :key="release.repo" class="hover:bg-surface-gray-1">
            <td class="px-4 py-3 font-medium text-ink-gray-9">{{ release.repo }}</td>
            <td class="px-4 py-3 text-ink-gray-7">{{ release.ref || '—' }}</td>
            <td class="px-4 py-3 font-mono text-xs text-ink-gray-6">{{ shortSha(release.sha) }}</td>
            <td class="px-4 py-3 text-ink-gray-6">{{ fmtDateTime(release.resolved_at) }}</td>
            <td class="px-4 py-3 text-right">
              <Button variant="subtle" icon-left="lucide-pencil" label="Edit" @click="openEdit(release)" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <EmptyState
      v-else
      class="mt-6"
      icon="lucide-tag"
      title="No tracked repos yet"
      description="Pin a repo ref here and Chef will resolve it on every bake."
    />

    <Dialog v-model="editOpen" :options="{ title: `Pin ref for ${editingRepo}`, size: 'sm' }">
      <template #body-content>
        <div class="space-y-4">
          <div>
            <FormControl
              v-model="editRef"
              type="text"
              label="Ref"
              list="release-ref-suggestions"
              placeholder="tag, branch, or SHA"
            />
            <datalist id="release-ref-suggestions">
              <option v-for="ref in suggestions" :key="ref" :value="ref" />
            </datalist>
            <p class="mt-1 text-xs text-ink-gray-5">
              <template v-if="suggestions.length">
                {{ suggestions.length }} tag{{ suggestions.length === 1 ? '' : 's' }} suggested — free text is
                also allowed.
              </template>
              <template v-else>Free text is allowed — a tag, branch, or SHA works.</template>
            </p>
          </div>

          <ErrorMessage :message="editError" />

          <div class="flex items-center justify-end gap-2 pt-1">
            <Button label="Cancel" variant="subtle" @click="editOpen = false" />
            <Button label="Save" variant="solid" theme="gray" :loading="saving" @click="saveEdit" />
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button, Dialog, ErrorMessage, FormControl } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { releasesApi } from '@/api/releases'
import { fmtDateTime } from '@/utils/format'

const releases = ref([])
const loading = ref(false)
const error = ref('')

const editOpen = ref(false)
const editingRepo = ref('')
const editRef = ref('')
const suggestions = ref([])
const saving = ref(false)
const editError = ref('')

function shortSha(sha) {
  if (!sha) return '—'
  return sha.slice(0, 12)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    releases.value = await releasesApi.list()
  } catch (caught) {
    error.value = caught.message || 'Failed to load releases'
  } finally {
    loading.value = false
  }
}

async function openEdit(release) {
  editingRepo.value = release.repo
  editRef.value = release.ref || ''
  suggestions.value = []
  editError.value = ''
  editOpen.value = true
  try {
    const result = await releasesApi.refs(release.repo)
    suggestions.value = result.refs || []
  } catch {
    // Ref lookup is best-effort (can be slow or 502); free text is always allowed.
  }
}

async function saveEdit() {
  saving.value = true
  editError.value = ''
  try {
    await releasesApi.setPin(editingRepo.value, editRef.value)
    editOpen.value = false
    await load()
  } catch (caught) {
    editError.value = caught.message || 'Failed to pin ref'
  } finally {
    saving.value = false
  }
}

onMounted(() => load())
</script>
