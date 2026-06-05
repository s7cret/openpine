<script setup lang="ts">
import { onMounted, onUnmounted, computed } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import { useRouter } from 'vue-router'

const store = useDashboardStore()
const router = useRouter()
let timer: ReturnType<typeof setInterval>

onMounted(() => {
  store.fetchAll()
  timer = setInterval(() => store.fetchAll(), 30000)
})
onUnmounted(() => clearInterval(timer))

const strategies = computed(() => store.stats?.strategies ?? [])
const jobs = computed(() => store.stats?.jobs ?? {})
const uptime = computed(() => {
  const s = store.stats?.uptime_seconds ?? 0
  if (s < 60) return `${Math.floor(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
})

const dataSizeMB = computed(() => {
  const bytes = store.dataInfo?.total_size_bytes ?? store.cacheInfo?.total_size_bytes ?? 0
  return (bytes / 1024 / 1024).toFixed(1)
})
const databaseSizeMB = computed(() => ((store.dataInfo?.database_size_bytes ?? 0) / 1024 / 1024).toFixed(1))
const totalBars = computed(() => (store.dataInfo?.total_bars ?? 0).toLocaleString())

const enabledStrategies = computed(() => strategies.value.filter((s: any) => s.enabled))
const runningStrategies = computed(() => strategies.value.filter((s: any) => s.status === 'running'))

function fmtAgo(ms?: number | null) {
  if (!ms) return '—'
  const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000))
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m ago`
}

function healthClass(status?: string) {
  if (status === 'ok') return 'text-success'
  if (status === 'stale' || status === 'runner_off') return 'text-warning'
  return 'text-danger'
}
</script>

<template>
  <div class="space-y-6">
    <!-- Stat Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- Pine Files -->
      <div
        class="bg-dark-800 rounded-xl border border-dark-500 p-4 cursor-pointer transition-all hover:ring-1 hover:ring-blue-500/50"
        @click="router.push('/pine-files')"
      >
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">Pine Files</span>
          <span class="text-xl">📄</span>
        </div>
        <div class="mt-2 text-3xl font-bold text-gray-100">{{ store.pineCount }}</div>
        <div class="mt-1 text-xs text-gray-500">loaded sources</div>
      </div>

      <!-- Strategies -->
      <div
        class="bg-dark-800 rounded-xl border border-dark-500 p-4 cursor-pointer transition-all hover:ring-1 hover:ring-blue-500/50"
        @click="router.push('/strategies')"
      >
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">Strategies</span>
          <span class="text-xl">⚡</span>
        </div>
        <div class="mt-2 text-3xl font-bold text-gray-100">{{ store.strategiesCount }}</div>
        <div class="mt-1 text-xs text-gray-500">
          {{ store.enabledCount }} enabled / {{ runningStrategies.length }} running
        </div>
      </div>

      <!-- Errors -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">Errors</span>
          <span class="text-xl">⚠️</span>
        </div>
        <div class="mt-2 text-3xl font-bold" :class="store.errorCount > 0 ? 'text-danger' : 'text-gray-100'">
          {{ store.errorCount }}
        </div>
        <div class="mt-1 text-xs text-gray-500">failed jobs</div>
      </div>

      <!-- System -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">System</span>
          <span class="text-xl">🌐</span>
        </div>
        <div class="mt-3 space-y-2">
          <div class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-success" />
            <span class="text-gray-400">Network</span>
            <span class="ml-auto text-gray-300 text-xs">Online</span>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-success" />
            <span class="text-gray-400">Binance API</span>
            <span class="ml-auto text-gray-300 text-xs">OK</span>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span :class="[store.backendOk ? 'bg-success' : 'bg-danger', 'w-2 h-2 rounded-full']" />
            <span class="text-gray-400">Backend</span>
            <span class="ml-auto text-gray-300 text-xs">{{ store.backendOk ? 'Up ' + uptime : 'Down' }}</span>
          </div>
          <div v-if="store.stats?.last_bar_update" class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-accent" />
            <span class="text-gray-400">Last bars</span>
            <span class="ml-auto text-gray-300 text-xs">{{ (() => { const sec = Math.floor((Date.now() - store.stats.last_bar_update) / 1000); if (sec < 60) return sec + 's ago'; if (sec < 3600) return Math.floor(sec/60) + 'm ago'; return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm ago'; })() }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Enabled Strategies Quick View -->
    <div class="bg-dark-800 rounded-xl border border-dark-500">
      <div class="px-4 py-3 border-b border-dark-500 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-300">⚡ Running Strategies</h2>
        <span class="text-xs text-gray-500">{{ runningStrategies.length }} running</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
              <th class="px-4 py-2.5 text-left">Name</th>
              <th class="px-4 py-2.5 text-left">Symbol</th>
              <th class="px-4 py-2.5 text-left">TF</th>
              <th class="px-4 py-2.5 text-left">Mode</th>
              <th class="px-4 py-2.5 text-left">Health</th>
              <th class="px-4 py-2.5 text-left">Last Order</th>
              <th class="px-4 py-2.5 text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="runningStrategies.length === 0">
              <td colspan="7" class="px-4 py-6 text-center text-gray-500">No running strategies</td>
            </tr>
            <tr
              v-for="s in runningStrategies"
              :key="s.strategy_id"
              class="border-b border-dark-600/50 hover:bg-dark-700/50 transition-colors cursor-pointer"
              @click="router.push({ path: '/strategies', query: { open: s.strategy_id } })"
            >
              <td class="px-4 py-2 font-medium text-gray-200 text-xs">{{ s.name }}</td>
              <td class="px-4 py-2 text-gray-400 font-mono text-xs">{{ s.symbol }}</td>
              <td class="px-4 py-2 text-gray-400 text-xs">{{ s.timeframe }}</td>
              <td class="px-4 py-2 text-accent-light text-xs">{{ s.mode }}</td>
              <td class="px-4 py-2 text-xs">
                <div :class="healthClass(s.health?.status)">{{ s.health?.status ?? '—' }}</div>
                <div class="text-[10px] text-gray-500">
                  bar {{ fmtAgo(s.health?.last_bar_time) }} · runner {{ s.health?.runner_alive ? 'on' : 'off' }}
                </div>
              </td>
              <td class="px-4 py-2 text-xs">
                <div class="text-gray-300">{{ s.health?.last_order?.side ?? '—' }} {{ s.health?.last_order?.status ?? '' }}</div>
                <div class="text-[10px] text-gray-500">{{ fmtAgo(s.health?.last_order?.created_at) }}</div>
              </td>
              <td class="px-4 py-2">
                <span
                  :class="[
                    s.status === 'running' ? 'bg-success/20 text-success' :
                    s.status === 'error' ? 'bg-danger/20 text-danger' :
                    'bg-gray-500/20 text-gray-400',
                    'px-2 py-0.5 rounded-full text-xs font-medium'
                  ]"
                >
                  {{ s.status }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Cache Info + Recent Jobs -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- Market Data -->
      <div
        class="bg-dark-800 rounded-xl border border-dark-500 p-4 cursor-pointer transition-all hover:ring-1 hover:ring-blue-500/50"
        @click="router.push('/data')"
      >
        <h2 class="text-sm font-semibold text-gray-300 mb-3">💾 Market Data</h2>
        <div class="space-y-2 text-sm">
          <div class="flex justify-between">
            <span class="text-gray-400">Total size</span>
            <span class="text-gray-200 font-mono">{{ dataSizeMB }} MB</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">Database</span>
            <span class="text-gray-200 font-mono">{{ databaseSizeMB }} MB</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">Bars</span>
            <span class="text-gray-200 font-mono">{{ totalBars }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">Series</span>
            <span class="text-gray-200">{{ store.dataInfo?.series_count ?? 0 }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">Kill switch</span>
            <span :class="store.stats?.kill_switch ? 'text-danger' : 'text-success'">
              {{ store.stats?.kill_switch ? 'ACTIVE' : 'Off' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Recent Jobs -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <h2 class="text-sm font-semibold text-gray-300 mb-3">🔄 Recent Jobs</h2>
        <div v-if="!jobs.recent?.length" class="text-gray-500 text-sm text-center py-4">No jobs yet</div>
        <div v-else class="space-y-2">
          <div
            v-for="job in (jobs.recent ?? []).slice(0, 5)"
            :key="job.id"
            class="flex items-center justify-between text-xs py-1.5 border-b border-dark-600/30 last:border-0"
          >
            <span class="text-gray-300">{{ job.type }}</span>
            <span class="text-gray-500 font-mono">{{ job.strategy_id ?? '—' }}</span>
            <span
              :class="[
                job.status === 'done' ? 'text-success' :
                job.status === 'running' ? 'text-accent-light' :
                job.status === 'failed' ? 'text-danger' :
                'text-gray-400',
                'font-medium'
              ]"
            >
              {{ job.status }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
