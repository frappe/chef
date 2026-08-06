<template>
  <FrappeUIProvider>
    <MainLayout>
      <RouterView v-slot="{ Component }">
        <component :is="Component" class="fade-in" />
      </RouterView>
    </MainLayout>
  </FrappeUIProvider>
</template>

<script setup>
import { FrappeUIProvider, useTheme } from 'frappe-ui'
import MainLayout from './layouts/MainLayout.vue'

const { initializeTheme } = useTheme()
initializeTheme()
</script>
