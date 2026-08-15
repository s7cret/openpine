<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getGatewayHealth } from '@/api/client'
import { subscribeUnauthorized } from '@/api/auth'
import AuthUnlockDialog from '@/components/AuthUnlockDialog.vue'
import TradeNotifications from '@/components/TradeNotifications.vue'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const sidebarOpen = ref(false)
const authDialogOpen = ref(false)
const gatewayStatus = ref<'checking' | 'ok' | 'degraded' | 'offline'>('checking')
const gatewayVersion = ref('')
let healthTimer: ReturnType<typeof setTimeout> | null = null
let unsubscribeUnauthorized: (() => void) | null = null

const navItems = computed(() => [
  { path: '/dashboard',    label: t('nav.dashboard'),    icon: '📊' },
  { path: '/pine-files',   label: t('nav.pineFiles'),    icon: '📄' },
  { path: '/strategies',   label: t('nav.strategies'),   icon: '⚡' },
  { path: '/backtests',    label: t('nav.backtests'),    icon: '🧪' },
  { path: '/jobs',         label: t('nav.jobs'),         icon: '📋' },
  { path: '/tv-parity',    label: t('nav.tvParity'),     icon: '📺' },
  { path: '/data',         label: t('nav.data'),         icon: '💾' },
  { path: '/achievements', label: t('nav.achievements'), icon: '🏆' },
  { path: '/settings',     label: t('nav.settings'),     icon: '⚙️' },
])

function navigate(path: string) {
  router.push(path)
  sidebarOpen.value = false
}

const currentTitle = computed(
  () => navItems.value.find(i => i.path === route.path)?.label ?? t('app.name')
)

const gatewayLabel = computed(() => {
  if (gatewayStatus.value === 'ok') return t('app.gatewayOnline')
  if (gatewayStatus.value === 'degraded') return t('app.gatewayDegraded')
  if (gatewayStatus.value === 'offline') return t('app.gatewayOffline')
  return t('app.gatewayChecking')
})

const gatewayDotClass = computed(() => ({
  checking: 'bg-gray-500',
  ok: 'bg-success animate-pulse',
  degraded: 'bg-warning animate-pulse',
  offline: 'bg-danger',
}[gatewayStatus.value]))

async function refreshGatewayHealth() {
  if (healthTimer) clearTimeout(healthTimer)
  try {
    const { data } = await getGatewayHealth()
    gatewayVersion.value = data.version ?? ''
    gatewayStatus.value = data.status === 'ok' ? 'ok' : 'degraded'
  } catch {
    gatewayStatus.value = 'offline'
  } finally {
    healthTimer = setTimeout(() => { void refreshGatewayHealth() }, 15_000)
  }
}

function onUnlocked() {
  authDialogOpen.value = false
  router.go(0)
}

onMounted(() => {
  unsubscribeUnauthorized = subscribeUnauthorized(() => { authDialogOpen.value = true })
  void refreshGatewayHealth()
})

onBeforeUnmount(() => {
  unsubscribeUnauthorized?.()
  if (healthTimer) clearTimeout(healthTimer)
})
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <div v-if="sidebarOpen" class="fixed inset-0 z-30 bg-black/50 lg:hidden" @click="sidebarOpen = false" />

    <aside
      :class="[sidebarOpen ? 'translate-x-0' : '-translate-x-full', 'lg:translate-x-0']"
      class="fixed z-40 lg:static inset-y-0 left-0 w-56 bg-dark-800 border-r border-dark-500 flex flex-col transition-transform duration-200 ease-in-out"
    >
      <div class="h-14 flex items-center px-4 border-b border-dark-500">
        <span class="text-lg font-bold text-accent-light">🌿 OpenPine</span>
      </div>

      <nav class="flex-1 py-3 space-y-0.5 px-2">
        <button
          v-for="item in navItems"
          :key="item.path"
          @click="navigate(item.path)"
          :class="[
            route.path === item.path
              ? 'bg-dark-600 text-accent-light'
              : 'text-gray-400 hover:bg-dark-700 hover:text-gray-200',
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors'
          ]"
        >
          <span class="text-base">{{ item.icon }}</span>
          {{ item.label }}
        </button>
      </nav>

      <div class="p-3 border-t border-dark-500">
        <div class="text-xs text-gray-400">{{ gatewayVersion ? `OpenPine Gateway v${gatewayVersion}` : t('app.name') }} · {{ gatewayLabel }}</div>
      </div>
    </aside>

    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-14 flex items-center justify-between px-4 bg-dark-800 border-b border-dark-500 shrink-0">
        <button class="lg:hidden p-1.5 rounded-lg hover:bg-dark-600" :aria-label="currentTitle" @click="sidebarOpen = !sidebarOpen">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div class="text-sm text-gray-400">{{ currentTitle }}</div>
        <div class="flex items-center gap-3">
          <LanguageSwitcher />
          <div class="flex items-center gap-2">
            <span :class="[gatewayDotClass, 'w-2 h-2 rounded-full']" :title="gatewayLabel" />
            <span class="text-xs text-gray-400" aria-live="polite">{{ gatewayLabel }}</span>
          </div>
        </div>
      </header>

      <main class="flex-1 overflow-y-auto p-4 lg:p-6" tabindex="0">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>

    <TradeNotifications />
    <AuthUnlockDialog :open="authDialogOpen" @close="authDialogOpen = false" @unlocked="onUnlocked" />
  </div>
</template>
