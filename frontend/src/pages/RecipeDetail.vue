<template>
  <div class="mx-auto max-w-4xl">
    <div v-if="loading" class="py-16 text-center text-ink-gray-5">
      <span class="mx-auto size-6 animate-spin lucide-loader-circle" />
      <p class="mt-2 text-sm">Loading recipe…</p>
    </div>

    <div v-else-if="error" class="py-12">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="recipe">
      <!-- Bake button in the header -->
      <Teleport defer to="#header-actions">
        <Button variant="solid" theme="gray" @click="showBake = true">
          <template #prefix><span class="size-4 lucide-flame" /></template>
          Bake
        </Button>
      </Teleport>

      <!-- Summary -->
      <div class="rounded-xl border border-outline-gray-2 bg-surface-base p-5">
        <div class="flex items-start gap-4">
          <div
            class="grid size-11 shrink-0 place-items-center rounded-lg bg-surface-gray-2 text-ink-gray-7"
          >
            <span class="size-6 lucide-chef-hat" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-lg font-semibold text-ink-gray-9">{{ recipe.name }}</h2>
              <span class="text-sm text-ink-gray-4">v{{ recipe.version }}</span>
            </div>
            <p class="mt-1 text-p-sm text-ink-gray-6">
              {{ recipe.description || 'No description.' }}
            </p>
            <div class="mt-3 flex flex-wrap items-center gap-1.5">
              <Badge
                v-for="mode in recipe.modes"
                :key="mode"
                :label="mode"
                :theme="modeTheme(mode)"
                variant="subtle"
                size="sm"
              />
              <Badge
                v-for="tag in recipe.tags"
                :key="tag"
                :label="tag"
                theme="gray"
                variant="outline"
                size="sm"
              />
            </div>
          </div>
        </div>

        <!-- Facts -->
        <div class="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div v-for="fact in facts" :key="fact.label" class="min-w-0">
            <p class="text-xs text-ink-gray-5">{{ fact.label }}</p>
            <p class="mt-0.5 truncate text-sm text-ink-gray-8" :title="fact.value">
              {{ fact.value }}
            </p>
          </div>
        </div>
      </div>

      <!-- Composed from -->
      <section v-if="recipe.compose?.length" class="mt-5">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Composed from</h3>
        <div class="flex flex-wrap items-center gap-1.5">
          <template v-for="(dep, i) in recipe.lineage" :key="dep">
            <span v-if="i > 0" class="text-ink-gray-4">→</span>
            <RouterLink
              v-if="dep !== recipe.name"
              :to="{ name: 'RecipeDetail', params: { name: dep } }"
              class="rounded-md border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-1 text-xs text-ink-blue-600 hover:bg-surface-gray-2"
            >
              {{ dep }}
            </RouterLink>
            <span
              v-else
              class="rounded-md border border-outline-gray-3 bg-surface-selected px-2.5 py-1 text-xs font-medium text-ink-gray-9"
            >
              {{ dep }}
            </span>
          </template>
        </div>
        <p class="mt-1.5 text-xs text-ink-gray-5">
          Base recipes stacked in order — each recipe's steps run left to right, then this
          recipe's own.
        </p>
      </section>

      <!-- Phases -->
      <section class="mt-5">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Phases</h3>
        <div class="flex flex-col gap-1.5">
          <div
            v-for="(sources, phase) in recipe.phase_sources"
            :key="phase"
            class="flex flex-wrap items-center gap-1.5 text-xs"
          >
            <span
              class="rounded-md border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-1 font-medium text-ink-gray-8"
            >
              {{ phase }}
            </span>
            <span v-if="sources.length > 1 || sources[0] !== recipe.name" class="text-ink-gray-5">
              {{ sources.join(' → ') }}
            </span>
          </div>
          <span v-if="!phaseCount" class="text-xs text-ink-gray-5">No phases declared.</span>
        </div>
      </section>

      <!-- Publish targets -->
      <section v-if="recipe.publish?.length" class="mt-5">
        <h3 class="mb-2 text-sm font-medium text-ink-gray-7">Publish targets</h3>
        <div class="flex flex-wrap gap-2">
          <Badge
            v-for="(p, i) in recipe.publish"
            :key="i"
            :label="p.type || p.name || 'target'"
            theme="blue"
            variant="subtle"
            size="sm"
          />
        </div>
      </section>

      <!-- Source -->
      <section v-if="sourceFiles.length" class="mt-5">
        <div class="mb-2 flex items-center gap-2">
          <h3 class="text-sm font-medium text-ink-gray-7">Source</h3>
          <div class="flex gap-1">
            <button
              v-for="file in sourceFiles"
              :key="file.name"
              class="rounded px-2 py-0.5 text-xs transition-colors"
              :class="
                activeFile === file.name
                  ? 'bg-surface-selected text-ink-gray-9'
                  : 'text-ink-gray-6 hover:bg-surface-gray-2'
              "
              @click="activeFile = file.name"
            >
              {{ file.name }}
            </button>
          </div>
        </div>
        <pre
          class="overflow-auto rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-3 text-xs leading-relaxed text-ink-gray-8"
          style="max-height: 24rem"
          >{{ activeSource }}</pre
        >
      </section>

      <BakeDialog v-model="showBake" :recipe="recipe" @baked="goToBake" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage } from 'frappe-ui'
import BakeDialog from '@/components/BakeDialog.vue'
import { recipesApi } from '@/api/recipes'
import { modeTheme } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const name = route.params.name

const recipe = ref(null)
const loading = ref(true)
const error = ref('')
const showBake = ref(false)
const activeFile = ref('')

const facts = computed(() => {
  const size = recipe.value?.size || {}
  return [
    { label: 'Base image', value: recipe.value?.base_image || '—' },
    { label: 'vCPUs', value: size.vcpus ?? '—' },
    { label: 'Memory', value: size.memory_megabytes ? `${size.memory_megabytes} MB` : '—' },
    { label: 'Disk', value: size.disk_gigabytes ? `${size.disk_gigabytes} GB` : '—' },
  ]
})

const phaseCount = computed(() => Object.keys(recipe.value?.phase_sources || {}).length)

const sourceFiles = computed(() =>
  Object.entries(recipe.value?.source || {}).map(([fileName, content]) => ({
    name: fileName,
    content,
  })),
)

const activeSource = computed(
  () => sourceFiles.value.find((f) => f.name === activeFile.value)?.content || '',
)

function goToBake(bakeId) {
  router.push({ name: 'BakeDetail', params: { id: bakeId } })
}

onMounted(async () => {
  try {
    recipe.value = await recipesApi.get(name)
    activeFile.value = sourceFiles.value[0]?.name || ''
  } catch (caught) {
    error.value = caught.message || 'Failed to load recipe'
  } finally {
    loading.value = false
  }
})
</script>
