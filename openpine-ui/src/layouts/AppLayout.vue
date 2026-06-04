<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import TradeNotifications from '@/components/TradeNotifications.vue'

const router = useRouter()
const route = useRoute()
const sidebarOpen = ref(false)

const navItems = [
  { path: '/dashboard', label: 'Dashboard', icon: '📊' },
  { path: '/pine-files', label: 'Pine Files', icon: '📄' },
  { path: '/strategies', label: 'Strategies', icon: '⚡' },
  { path: '/backtests', label: 'Backtests', icon: '🧪' },
]

function navigate(path: string) {
  router.push(path)
  sidebarOpen.value = false
}
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <!-- Mobile overlay -->
    <div v-if="sidebarOpen" class="fixed inset-0 z-30 bg-black/50 lg:hidden" @click="sidebarOpen = false" />

    <!-- Sidebar -->
    <aside
      :class="[sidebarOpen ? 'translate-x-0' : '-translate-x-full', 'lg:translate-x-0']"
      class="fixed z-40 lg:static inset-y-0 left-0 w-56 bg-dark-800 border-r border-dark-500 flex flex-col transition-transform duration-200 ease-in-out"
    >
      <!-- Logo -->
      <div class="h-14 flex items-center px-4 border-b border-dark-500">
        <span class="text-lg font-bold text-accent-light">🌿 OpenPine</span>
      </div>

      <!-- Nav -->
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

      <!-- Footer -->
      <div class="p-3 border-t border-dark-500">
        <div class="text-xs text-gray-500">OpenPine Gateway v1.0</div>
      </div>
    </aside>

    <!-- Main -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Topbar -->
      <header class="h-14 flex items-center justify-between px-4 bg-dark-800 border-b border-dark-500 shrink-0">
        <button class="lg:hidden p-1.5 rounded-lg hover:bg-dark-600" @click="sidebarOpen = !sidebarOpen">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div class="text-sm text-gray-400">{{ navItems.find(i => i.path === route.path)?.label ?? 'OpenPine' }}</div>
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-success animate-pulse" title="Gateway online" />
          <span class="text-xs text-gray-500">Connected</span>
        </div>
      </header>

      <!-- Content -->
      <main class="flex-1 overflow-y-auto p-4 lg:p-6">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>

    <!-- Global trade notifications -->
    <TradeNotifications />
  </div>
</template>
