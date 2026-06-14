<script setup lang="ts">
import { onMounted, onUnmounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDashboardStore } from '@/stores/dashboard'
import { summarizeDataHealth } from '@/lib/dataHealth'
import { useRouter } from 'vue-router'

const { t } = useI18n()
const store = useDashboardStore()
const router = useRouter()
let timer: ReturnType<typeof setInterval>

onMounted(() => {
  store.fetchAll()
  timer = setInterval(() => store.fetchAll(), 5000)
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
const dataHealthSummary = computed(() => store.dataHealth ? summarizeDataHealth(store.dataHealth) : null)
const staleMarketCount = computed(() => store.dataHealth?.exchanges?.reduce((total: number, ex: any) => {
  return total + (ex.markets ?? []).filter((m: any) => m.status === 'stale').length
}, 0) ?? 0)

const enabledStrategies = computed(() => strategies.value.filter((s: any) => s.enabled))
const runningStrategies = computed(() => strategies.value.filter((s: any) => s.status === 'running'))

function fmtAgo(ms?: number | null) {
  if (!ms) return '—'
  const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000))
  if (sec < 60) return t('dashboard.agoShort', { sec })
  if (sec < 3600) return t('dashboard.agoMin', { m: Math.floor(sec / 60) })
  return t('dashboard.agoHour', { h: Math.floor(sec / 3600), m: Math.floor((sec % 3600) / 60) })
}

function healthClass(status?: string) {
  if (status === 'ok') return 'text-success'
  if (status === 'stale' || status === 'runner_off') return 'text-warning'
  return 'text-danger'
}

function jobTitle(job: any) {
  if (job.type === 'backfill') {
    const input = job.input ?? job.progress?.detail ?? job.result ?? {}
    return `Candles ${input.symbol ?? ''} ${input.timeframe ?? ''}`.trim()
  }
  return job.type
}

function jobSubtitle(job: any) {
  if (job.type === 'backfill') {
    const progress = job.progress
    if (progress?.message) return progress.message
    const result = job.result
    if (result?.bars_loaded != null) return t('dashboard.loadedCandles', { count: Number(result.bars_loaded).toLocaleString() })
  }
  return job.strategy_id ?? '—'
}

function jobPct(job: any) {
  const pct = Number(job.progress?.pct ?? (job.status === 'done' ? 1 : 0))
  return Math.max(0, Math.min(100, Math.round(pct * 100)))
}

function agoShort(sec: number) {
  if (sec < 60) return t('dashboard.agoSec', { s: sec })
  if (sec < 3600) return t('dashboard.agoMin', { m: Math.floor(sec / 60) })
  return t('dashboard.agoHour', { h: Math.floor(sec / 3600), m: Math.floor((sec % 3600) / 60) })
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
          <span class="text-xs text-gray-500 uppercase tracking-wider">{{ t('dashboard.statPineFiles') }}</span>
          <span class="text-xl">📄</span>
        </div>
        <div class="mt-2 text-3xl font-bold text-gray-100">{{ store.pineCount }}</div>
        <div class="mt-1 text-xs text-gray-500">{{ t('dashboard.statPineFilesDesc') }}</div>
      </div>

      <!-- Strategies -->
      <div
        class="bg-dark-800 rounded-xl border border-dark-500 p-4 cursor-pointer transition-all hover:ring-1 hover:ring-blue-500/50"
        @click="router.push('/strategies')"
      >
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">{{ t('dashboard.statStrategies') }}</span>
          <span class="text-xl">⚡</span>
        </div>
        <div class="mt-2 text-3xl font-bold text-gray-100">{{ store.strategiesCount }}</div>
        <div class="mt-1 text-xs text-gray-500">
          {{ t('dashboard.statStrategiesDesc', { enabled: store.enabledCount, running: runningStrategies.length }) }}
        </div>
      </div>

      <!-- Errors -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">{{ t('dashboard.statErrors') }}</span>
          <span class="text-xl">⚠️</span>
        </div>
        <div class="mt-2 text-3xl font-bold" :class="store.errorCount > 0 ? 'text-danger' : 'text-gray-100'">
          {{ store.errorCount }}
        </div>
        <div class="mt-1 text-xs text-gray-500">{{ t('dashboard.statErrorsDesc') }}</div>
      </div>

      <!-- System -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500 uppercase tracking-wider">{{ t('dashboard.statSystem') }}</span>
          <span class="text-xl">🌐</span>
        </div>
        <div class="mt-3 space-y-2">
          <div class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-accent" />
            <span class="text-gray-400">{{ t('dashboard.systemMarket') }}</span>
            <span class="ml-auto text-right text-gray-300 text-xs">{{ dataHealthSummary?.exchangeLabel ?? t('dashboard.loading') }}</span>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span :class="[staleMarketCount ? 'bg-warning' : 'bg-success', 'w-2 h-2 rounded-full']" />
            <span class="text-gray-400">{{ t('dashboard.systemCache') }}</span>
            <span class="ml-auto text-right text-gray-300 text-xs">{{ dataHealthSummary?.cacheLabel ?? '—' }}</span>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-success" />
            <span class="text-gray-400">{{ t('dashboard.systemStable') }}</span>
            <span class="ml-auto text-right text-gray-300 text-xs">{{ dataHealthSummary?.stableQuotesLabel ?? '—' }}</span>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span :class="[store.backendOk ? 'bg-success' : 'bg-danger', 'w-2 h-2 rounded-full']" />
            <span class="text-gray-400">{{ t('dashboard.systemBackend') }}</span>
            <span class="ml-auto text-gray-300 text-xs">{{ store.backendOk ? t('common.up') + ' ' + uptime : t('common.down') }}</span>
          </div>
          <div v-if="store.stats?.last_bar_update" class="flex items-center gap-2 text-sm">
            <span class="w-2 h-2 rounded-full bg-accent" />
            <span class="text-gray-400">{{ t('dashboard.systemLastBars') }}</span>
            <span class="ml-auto text-gray-300 text-xs">{{ agoShort(Math.floor((Date.now() - store.stats.last_bar_update) / 1000)) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Enabled Strategies Quick View -->
    <div class="bg-dark-800 rounded-xl border border-dark-500">
      <div class="px-4 py-3 border-b border-dark-500 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-300">{{ t('dashboard.runningStrategies') }}</h2>
        <span class="text-xs text-gray-500">{{ t('dashboard.runningCount', { count: runningStrategies.length }) }}</span>
      </div>
      <div class="md:hidden divide-y divide-dark-600/60">
        <div v-if="runningStrategies.length === 0" class="px-4 py-6 text-center text-gray-500 text-sm">{{ t('dashboard.noRunning') }}</div>
        <button
          v-for="s in runningStrategies"
          :key="s.strategy_id"
          class="w-full p-4 text-left hover:bg-dark-700/40 transition-colors"
          @click="router.push({ path: '/strategies', query: { open: s.strategy_id } })"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="break-words text-sm font-medium leading-snug text-gray-200">{{ s.name }}</div>
              <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
                <span class="font-mono text-gray-300">{{ s.symbol }}</span>
                <span>{{ s.timeframe }}</span>
                <span class="text-accent-light">{{ s.mode }}</span>
              </div>
            </div>
            <span
              :class="[
                s.status === 'running' ? 'bg-success/20 text-success' :
                s.status === 'error' ? 'bg-danger/20 text-danger' :
                'bg-gray-500/20 text-gray-400',
                'shrink-0 px-2 py-0.5 rounded-full text-xs font-medium'
              ]"
            >
              {{ s.status }}
            </span>
          </div>
          <div class="mt-3 grid grid-cols-2 gap-3 text-xs">
            <div>
              <span class="text-gray-500">{{ t('dashboard.thHealth') }}</span>
              <div :class="[healthClass(s.health?.status), 'mt-1']">{{ s.health?.status ?? '—' }}</div>
              <div class="mt-0.5 text-[10px] text-gray-500">{{ t('dashboard.healthBar') }} {{ fmtAgo(s.health?.last_bar_time) }}</div>
            </div>
            <div>
              <span class="text-gray-500">{{ t('dashboard.thLastOrder') }}</span>
              <div class="mt-1 text-gray-300">{{ s.health?.last_order?.side ?? '—' }} {{ s.health?.last_order?.status ?? '' }}</div>
              <div class="mt-0.5 text-[10px] text-gray-500">{{ fmtAgo(s.health?.last_order?.created_at) }}</div>
            </div>
          </div>
        </button>
      </div>
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thName') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thSymbol') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thTf') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thMode') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thHealth') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thLastOrder') }}</th>
              <th class="px-4 py-2.5 text-left">{{ t('dashboard.thStatus') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="runningStrategies.length === 0">
              <td colspan="7" class="px-4 py-6 text-center text-gray-500">{{ t('dashboard.noRunning') }}</td>
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
                  {{ t('dashboard.healthBar') }} {{ fmtAgo(s.health?.last_bar_time) }} · runner {{ s.health?.runner_alive ? t('dashboard.runnerOn') : t('dashboard.runnerOff') }}
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
        <h2 class="text-sm font-semibold text-gray-300 mb-3">{{ t('dashboard.marketData') }}</h2>
        <div class="space-y-2 text-sm">
          <div class="flex justify-between">
            <span class="text-gray-400">{{ t('dashboard.totalSize') }}</span>
            <span class="text-gray-200 font-mono">{{ dataSizeMB }} MB</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">{{ t('dashboard.database') }}</span>
            <span class="text-gray-200 font-mono">{{ databaseSizeMB }} MB</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">{{ t('dashboard.bars') }}</span>
            <span class="text-gray-200 font-mono">{{ totalBars }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">{{ t('dashboard.series') }}</span>
            <span class="text-gray-200">{{ store.dataInfo?.series_count ?? 0 }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-gray-400">{{ t('dashboard.killSwitch') }}</span>
            <span :class="store.stats?.kill_switch ? 'text-danger' : 'text-success'">
              {{ store.stats?.kill_switch ? t('common.active') : t('common.off_upper') }}
            </span>
          </div>
        </div>
      </div>

      <!-- Recent Jobs -->
      <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
        <h2 class="text-sm font-semibold text-gray-300 mb-3">{{ t('dashboard.recentJobs') }}</h2>
        <div v-if="!jobs.recent?.length" class="text-gray-500 text-sm text-center py-4">{{ t('dashboard.noJobs') }}</div>
        <div v-else class="space-y-2">
          <div
            v-for="job in (jobs.recent ?? []).slice(0, 5)"
            :key="job.id"
            class="py-2 border-b border-dark-600/30 last:border-0"
          >
            <div class="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)_auto] items-center gap-2 text-xs">
              <span class="text-gray-300">{{ jobTitle(job) }}</span>
              <span class="min-w-0 truncate text-gray-500 font-mono">{{ jobSubtitle(job) }}</span>
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
            <div v-if="job.type === 'backfill' && (job.status === 'running' || job.status === 'pending')" class="mt-2">
              <div class="h-1.5 overflow-hidden rounded-full bg-dark-600">
                <div class="h-full rounded-full bg-accent transition-all" :style="{ width: `${jobPct(job)}%` }" />
              </div>
              <div class="mt-1 text-right font-mono text-[10px] text-gray-500">{{ jobPct(job) }}%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
