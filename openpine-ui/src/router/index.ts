import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: () => import('@/pages/Dashboard.vue') },
  { path: '/pine-files', name: 'pine-files', component: () => import('@/pages/PineFiles.vue') },
  { path: '/strategies', name: 'strategies', component: () => import('@/pages/Strategies.vue') },
  { path: '/backtests', name: 'backtests', component: () => import('@/pages/Backtests.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
