<template>
  <div
    class="flex flex-col items-center px-6 text-center"
    :class="[
      bordered ? 'border border-outline-gray-2 border-dashed rounded-xl' : '',
      compact ? 'gap-3 py-6' : 'gap-4 py-14',
    ]"
  >
    <span
      v-if="icon"
      class="place-items-center grid bg-surface-gray-2 text-ink-gray-5 shrink-0"
      :class="compact ? 'rounded-md size-8' : 'rounded-lg size-10'"
    >
      <span :class="[compact ? 'size-4' : 'size-5', icon]" />
    </span>
    <div>
      <p class="font-medium text-ink-gray-7 text-base">{{ title }}</p>
      <p v-if="description" class="mt-1 max-w-sm text-ink-gray-5 text-p-sm">{{ description }}</p>
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: '' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  bordered: { type: Boolean, default: true },
  compact: { type: Boolean, default: false },
})
</script>
