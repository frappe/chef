<template>
  <div class="mx-auto max-w-5xl">
    <!-- Toolbar -->
    <div class="mb-5 flex items-center gap-3">
      <FormControl
        v-model="search"
        type="text"
        placeholder="Search recipes…"
        class="w-full max-w-sm"
      >
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-search" />
        </template>
      </FormControl>
      <span class="ml-auto text-sm text-ink-gray-5">
        {{ filtered.length }} {{ filtered.length === 1 ? 'recipe' : 'recipes' }}
      </span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div
        v-for="i in 4"
        :key="i"
        class="h-36 animate-pulse rounded-xl border border-outline-gray-2 bg-surface-gray-1"
      />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="py-12">
      <ErrorMessage :message="error" />
    </div>

    <!-- Grid -->
    <div v-else-if="filtered.length" class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <RouterLink
        v-for="recipe in filtered"
        :key="recipe.name"
        :to="{ name: 'RecipeDetail', params: { name: recipe.name } }"
        class="group flex flex-col rounded-xl border border-outline-gray-2 bg-surface-base p-4 no-underline transition-colors hover:border-outline-gray-3 hover:bg-surface-gray-1"
      >
        <div class="flex items-start gap-3">
          <div
            class="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-gray-2 text-ink-gray-7"
          >
            <span class="size-5 lucide-chef-hat" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="truncate font-medium text-ink-gray-9">{{ recipe.name }}</span>
              <span class="shrink-0 text-xs text-ink-gray-4">v{{ recipe.version }}</span>
            </div>
            <p class="mt-0.5 line-clamp-2 text-p-sm text-ink-gray-6">
              {{ recipe.description || 'No description.' }}
            </p>
          </div>
          <span
            class="size-4 shrink-0 text-ink-gray-4 opacity-0 transition-opacity group-hover:opacity-100 lucide-arrow-up-right"
          />
        </div>

        <div class="mt-3 flex items-center gap-1.5 text-xs text-ink-gray-5">
          <span class="size-3.5 lucide-box" />
          <span class="truncate">{{ recipe.base_image }}</span>
        </div>

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
      </RouterLink>
    </div>

    <!-- Empty -->
    <EmptyState
      v-else
      class="mt-6"
      icon="lucide-book-open"
      :title="search ? 'No matching recipes' : 'No recipes yet'"
      :description="
        search
          ? 'Try a different search term.'
          : 'Add a recipe under the backend\'s recipes/ directory to get started.'
      "
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Badge, ErrorMessage, FormControl } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { useRecipes } from '@/composables/useRecipes'
import { modeTheme } from '@/utils/format'

const { recipes, loading, error, load } = useRecipes()
const search = ref('')

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return recipes.value
  return recipes.value.filter((r) => {
    const haystack = [r.name, r.description, ...(r.tags || [])].join(' ').toLowerCase()
    return haystack.includes(q)
  })
})

onMounted(() => load())
</script>
