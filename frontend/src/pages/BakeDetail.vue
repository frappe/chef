<template>
  <div class="mx-auto max-w-4xl">
    <div v-if="loading && !bake" class="py-16 text-center text-ink-gray-5">
      <span class="mx-auto size-6 animate-spin lucide-loader-circle" />
      <p class="mt-2 text-sm">Loading bake…</p>
    </div>

    <div v-else-if="error && !bake" class="py-12">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="bake">
      <!-- Header status + actions -->
      <Teleport defer to="#header-actions">
        <Badge
          :label="bake.status"
          :theme="bakeTheme(bake.status)"
          variant="subtle"
          size="md"
          class="capitalize"
        />
        <Button
          variant="subtle"
          icon-left="lucide-refresh-cw"
          :loading="loading"
          label="Refresh"
          @click="load"
        />
        <Button
          v-if="isBakeActive(bake.status)"
          variant="subtle"
          theme="red"
          icon-left="lucide-square"
          label="Abort"
          @click="abort"
        />
      </Teleport>

      <!-- Facts -->
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div v-for="fact in facts" :key="fact.label" class="min-w-0">
          <p class="text-xs text-ink-gray-5">{{ fact.label }}</p>
          <component
            :is="fact.to ? 'RouterLink' : 'p'"
            :to="fact.to"
            class="mt-0.5 block truncate text-sm text-ink-gray-8 no-underline"
            :class="fact.to ? 'hover:text-ink-gray-9' : ''"
            :title="fact.value"
          >
            {{ fact.value }}
          </component>
        </div>
      </div>

      <div v-if="bake.error" class="mt-4">
        <ErrorMessage :message="bake.error" />
      </div>

      <!-- Steps -->
      <section v-if="bake.steps?.length" class="mt-6">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Steps</h3>
        <div class="overflow-hidden rounded-lg border border-outline-gray-2">
          <div
            v-for="step in bake.steps"
            :key="step.index"
            class="flex items-center gap-3 border-b border-outline-gray-2 px-3 py-2 last:border-b-0"
          >
            <span
              class="size-4 shrink-0"
              :class="[stepIcon(step.state), stepIconColor(step.state)]"
            />
            <span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">{{ step.name }}</span>
            <span v-if="step.phase" class="text-xs text-ink-gray-4">{{ step.phase }}</span>
            <span v-if="step.retries" class="text-xs text-ink-amber-6">
              ↻ {{ step.retries }}
            </span>
            <Badge
              :label="stepLabel(step.state)"
              :theme="stepTheme(step.state)"
              variant="subtle"
              size="sm"
            />
          </div>
        </div>
      </section>

      <!-- Live logs -->
      <section class="mt-6">
        <div class="mb-2 flex items-center gap-2">
          <h3 class="text-sm font-medium text-ink-gray-7">Logs</h3>
          <span v-if="streaming" class="flex items-center gap-1 text-xs text-ink-green-7">
            <span class="size-1.5 animate-pulse rounded-full bg-ink-green-7" />
            streaming
          </span>
        </div>
        <TaskStream
          :url="logsUrl"
          empty-text="Waiting for output…"
          @status="onStatus"
          @step="onStep"
          @done="onDone"
          @line="streaming = true"
        />
      </section>

      <!-- Produced images -->
      <section v-if="imageDetails.length" class="mt-6">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Produced images</h3>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <RouterLink
            v-for="img in imageDetails"
            :key="img.id"
            :to="{ name: 'Images' }"
            class="flex items-center gap-3 rounded-lg border border-outline-gray-2 bg-surface-base p-3 no-underline hover:border-outline-gray-3"
          >
            <span class="size-5 shrink-0 text-ink-gray-6 lucide-hard-drive" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium text-ink-gray-9">
                {{ img.recipe }}<span v-if="img.version"> · v{{ img.version }}</span>
              </div>
              <div class="truncate text-xs text-ink-gray-5">{{ img.location?.uri }}</div>
            </div>
            <Badge :label="img.kind" :theme="kindTheme(img.kind)" variant="subtle" size="sm" />
          </RouterLink>
        </div>
      </section>
      <section v-else-if="bake.images?.length" class="mt-6">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Produced images</h3>
        <ul class="space-y-1 text-sm text-ink-gray-7">
          <li v-for="id in bake.images" :key="id" class="font-mono text-xs">{{ id }}</li>
        </ul>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Badge, Button, ErrorMessage } from 'frappe-ui'
import TaskStream from '@/components/TaskStream.vue'
import { useBake } from '@/composables/useBake'
import { bakesApi } from '@/api/bakes'
import { imagesApi } from '@/api/images'
import {
  bakeTheme,
  fmtDateTime,
  isBakeActive,
  kindTheme,
  stepIcon,
  stepLabel,
  stepTheme,
} from '@/utils/format'

const route = useRoute()
const id = route.params.id

const { bake, loading, error, load, poll, stop } = useBake(id)
const logsUrl = bakesApi.logsUrl(id)
const streaming = ref(false)
const imageDetails = ref([])

const facts = computed(() => {
  if (!bake.value) return []
  return [
    {
      label: 'Recipe',
      value: bake.value.version ? `${bake.value.recipe} · v${bake.value.version}` : bake.value.recipe,
      to: { name: 'RecipeDetail', params: { name: bake.value.recipe } },
    },
    { label: 'Mode', value: bake.value.mode },
    { label: 'Builder', value: bake.value.builder },
    {
      label: 'Exit code',
      value: bake.value.exit_code != null ? String(bake.value.exit_code) : '—',
    },
    { label: 'Started', value: fmtDateTime(bake.value.created_at) },
    { label: 'Updated', value: fmtDateTime(bake.value.updated_at) },
  ]
})

const STEP_ICON_COLOR = {
  running: 'text-ink-blue-6 animate-spin',
  changed: 'text-ink-green-7',
  no_change: 'text-ink-gray-4',
  failed: 'text-ink-red-7',
}
function stepIconColor(state) {
  return STEP_ICON_COLOR[state] || 'text-ink-gray-4'
}

// Live status from the SSE stream keeps the badge/steps moving between polls.
function onStatus(event) {
  if (bake.value && event.status) bake.value.status = event.status
}

function onStep() {
  // A structured step arrived; refresh the persisted step list.
  load()
}

async function loadImages() {
  const ids = bake.value?.images || []
  if (!ids.length) return
  try {
    imageDetails.value = await Promise.all(ids.map((imageId) => imagesApi.get(imageId)))
  } catch {
    imageDetails.value = []
  }
}

async function onDone() {
  streaming.value = false
  stop()
  await load()
  await loadImages()
}

async function abort() {
  try {
    await bakesApi.abort(id)
  } catch {
    /* surfaced on next poll */
  }
  load()
}

onMounted(async () => {
  await load()
  if (bake.value && isBakeActive(bake.value.status)) {
    poll()
  } else {
    await loadImages()
  }
})
</script>
