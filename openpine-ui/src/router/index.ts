import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: () => import('@/pages/Dashboard.vue') },
  { path: '/pine-files', name: 'pine-files', component: () => import('@/pages/PineFiles.vue') },
  { path: '/strategies', name: 'strategies', component: () => import('@/pages/Strategies.vue') },
  { path: '/backtests', name: 'backtests', component: () => import('@/pages/Backtests.vue') },
  { path: '/backtests/:runId', name: 'backtest-run', component: () => import('@/pages/Backtests.vue') },
  { path: '/optimize', name: 'optimize', component: () => import('@/pages/Optimizer.vue') },
  { path: '/optimize/:jobId', name: 'optimize-job', component: () => import('@/pages/Optimizer.vue') },
  { path: '/live', name: 'live', component: () => import('@/pages/Live.vue') },
  { path: '/live/:strategyId', name: 'live-strategy', component: () => import('@/pages/Live.vue') },
  { path: '/tv-parity', name: 'tv-parity', component: () => import('@/pages/TvParity.vue') },
  { path: '/data', name: 'data', component: () => import('@/pages/Data.vue') },
  { path: '/jobs', name: 'jobs', component: () => import('@/pages/JobInbox.vue') },
  { path: '/jobs/:jobId', name: 'job-detail', component: () => import('@/pages/JobInbox.vue') },
  { path: '/achievements', name: 'achievements', component: () => import('@/pages/Achievements.vue') },
  { path: '/settings', name: 'settings', component: () => import('@/pages/Settings.vue') },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/pages/NotFound.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
