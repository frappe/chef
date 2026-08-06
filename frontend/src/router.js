import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Recipes',
    component: () => import('./pages/Recipes.vue'),
    meta: { title: 'Recipes' },
  },
  {
    path: '/recipes/:name',
    name: 'RecipeDetail',
    component: () => import('./pages/RecipeDetail.vue'),
    meta: { title: 'Recipe' },
  },
  {
    path: '/images',
    name: 'Images',
    component: () => import('./pages/Images.vue'),
    meta: { title: 'Images' },
  },
  {
    path: '/bakes/:id',
    name: 'BakeDetail',
    component: () => import('./pages/BakeDetail.vue'),
    meta: { title: 'Bake' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · Chef` : 'Chef 🧑‍🍳'
})
