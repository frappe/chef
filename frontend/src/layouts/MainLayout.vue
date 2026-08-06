<template>
  <div class="flex h-screen overflow-hidden bg-surface-base text-ink-gray-8">
    <!-- Sidebar (overlay drawer on mobile) -->
    <div
      v-if="mobileOpen"
      class="fixed inset-0 z-30 bg-black/30 md:hidden"
      @click="mobileOpen = false"
    />
    <aside
      class="fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r border-outline-gray-2 bg-surface-gray-1 transition-transform md:static md:translate-x-0"
      :class="mobileOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="flex h-14 items-center gap-2.5 px-4">
        <span class="text-2xl leading-none">🧑‍🍳</span>
        <div class="flex flex-col leading-tight">
          <span class="font-semibold text-ink-gray-9 text-lg">Chef</span>
          <span class="text-ink-gray-5 text-xs">Image kitchen</span>
        </div>
      </div>

      <nav class="flex-1 space-y-1 p-3">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm no-underline transition-colors"
          :class="
            isActive(item)
              ? 'bg-surface-selected font-medium text-ink-gray-9'
              : 'text-ink-gray-7 hover:bg-surface-gray-2'
          "
          @click="mobileOpen = false"
        >
          <span :class="['size-4 shrink-0', item.icon]" />
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="border-t border-outline-gray-2 p-4 text-xs text-ink-gray-5">
        Bake cold &amp; warm VM images
      </div>
    </aside>

    <!-- Main column -->
    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-outline-gray-2 bg-surface-base px-4 sm:px-6"
      >
        <button
          class="grid size-8 place-items-center rounded-md text-ink-gray-6 hover:bg-surface-gray-2 md:hidden"
          @click="mobileOpen = true"
        >
          <span class="size-5 lucide-menu" />
        </button>

        <div class="flex min-w-0 items-center gap-2">
          <RouterLink
            v-if="backTo"
            :to="backTo"
            class="grid size-7 place-items-center rounded-md text-ink-gray-5 no-underline hover:bg-surface-gray-2"
          >
            <span class="size-4 lucide-arrow-left" />
          </RouterLink>
          <h1 class="truncate text-base font-medium text-ink-gray-9">{{ heading }}</h1>
        </div>

        <div id="header-actions" class="ml-auto flex items-center gap-2" />
      </header>

      <main class="flex-1 overflow-auto p-4 sm:p-6">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const mobileOpen = ref(false)

const navItems = [
  { label: 'Recipes', to: '/', icon: 'lucide-book-open', match: ['Recipes', 'RecipeDetail'] },
  { label: 'Images', to: '/images', icon: 'lucide-hard-drive', match: ['Images'] },
]

function isActive(item) {
  return item.match.includes(route.name)
}

const heading = computed(() => {
  switch (route.name) {
    case 'RecipeDetail':
      return route.params.name
    case 'Images':
      return 'Images'
    case 'BakeDetail':
      return `Bake ${String(route.params.id).slice(0, 12)}`
    default:
      return 'Recipes'
  }
})

const backTo = computed(() => {
  if (route.name === 'RecipeDetail') return { name: 'Recipes' }
  if (route.name === 'BakeDetail') return { name: 'Recipes' }
  return null
})

watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false
  },
)
</script>
