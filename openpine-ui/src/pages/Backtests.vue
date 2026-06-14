<script setup lang="ts">
import { computed, onMounted, ref, onUnmounted, watch } from 'vue'
import { useBacktestsStore } from '@/stores/backtests'
import { useStrategiesStore } from '@/stores/strategies'
import DateRangePicker from '@/components/DateRangePicker.vue'
import { formatDateTime } from '@/utils/time'
import { confirmBacktestDelete, shouldStopBacktestPolling } from '@/lib/backtestUi'

const btStore = useBacktestsStore()
const stStore = useStrategiesStore()
const showRun = ref(false)
const expandedId = ref<string | null>(null)
const progressTimer = ref<ReturnType<typeof setInterval> | null>(null)
const activePollId = ref<string | null>(null)
const runStatus = ref('')
const runLoading = ref(false)
const estimate = ref<any>(null)
const availability = ref<any>(null)
const estimateLoading = ref(false)
let estimateTimer: ReturnType<typeof setTimeout> | null = null

const form = ref({ strategy_id: '', from_time: '', to_time: '' })
const allAvailableFrom = computed(() => msToDate(availability.value?.earliest_available ?? availability.value?.effective_from))
const selectedStrategy = computed(() => stStore.items.find((item: any) => (item.strategy_id ?? item.id) === form.value.strategy_id) ?? null)

onMounted(() => {
  btStore.fetchAll()
  stStore.fetchAll()
})

onUnmounted(() => {
  if (progressTimer.value) clearInterval(progressTimer.value)
  if (estimateTimer) clearTimeout(estimateTimer)
})

watch(() => form.value.strategy_id, async (id) => {
  availability.value = null
  estimate.value = null
  if (!id) return
  const today = new Date().toISOString().slice(0, 10)
  availability.value = await btStore.estimate({ strategy_id: id, from_time: '2016-01-01', to_time: today })
})

watch(
  () => [form.value.strategy_id, form.value.from_time, form.value.to_time],
  () => {
    if (estimateTimer) clearTimeout(estimateTimer)
    estimateTimer = setTimeout(refreshEstimate, 350)
  },
)

async function runBacktest() {
  if (!form.value.strategy_id) { runStatus.value = '❌ Select a strategy'; return }
  if (!form.value.from_time || !form.value.to_time) { runStatus.value = '❌ Select date range'; return }
  runLoading.value = true
  runStatus.value = 'Starting backtest...'
  const result = await btStore.run(form.value)
  runLoading.value = false
  if (result?.run_id) {
    runStatus.value = '✅ Backtest started!'
    showRun.value = false
    btStore.fetchAll()
    pollProgress(result.run_id)
    setTimeout(() => runStatus.value = '', 2000)
  } else {
    runStatus.value = '❌ Failed to start backtest'
  }
}

async function refreshEstimate() {
  if (!form.value.strategy_id || !form.value.from_time || !form.value.to_time) return
  estimateLoading.value = true
  estimate.value = await btStore.estimate(form.value)
  estimateLoading.value = false
}

function stopProgressPolling(refresh = false) {
  if (progressTimer.value) clearInterval(progressTimer.value)
  progressTimer.value = null
  activePollId.value = null
  if (refresh) btStore.fetchAll()
}

function runById(id: string) {
  return btStore.items.find((run: any) => (run.run_id ?? run.id) === id) ?? btStore.current
}

function pollProgress(id: string) {
  if (progressTimer.value) clearInterval(progressTimer.value)
  if (shouldStopBacktestPolling(null, runById(id))) {
    stopProgressPolling(false)
    return
  }
  activePollId.value = id
  // Immediate first poll
  btStore.fetchProgress(id)
  progressTimer.value = setInterval(async () => {
    await btStore.fetchProgress(id)
    const p = btStore.progress
    if (shouldStopBacktestPolling(p, runById(id))) {
      stopProgressPolling(true)
    }
  }, 3000)
}

async function expandRun(id: string) {
  if (expandedId.value === id) { expandedId.value = null; return }
  expandedId.value = id
  await btStore.fetchOne(id)
  pollProgress(id)
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    completed: 'bg-success/20 text-success',
    running: 'bg-accent/20 text-accent-light',
    failed: 'bg-danger/20 text-danger',
    cancelled: 'bg-warning/20 text-warning',
    cancelling: 'bg-warning/20 text-warning',
    queued: 'bg-gray-500/20 text-gray-400',
  }
  return map[(status ?? '').toLowerCase()] ?? 'bg-gray-500/20 text-gray-400'
}

function isControllable(run: any) {
  const status = (btStore.getProgress(run.run_id ?? run.id)?.status ?? run.status ?? '').toLowerCase()
  return status === 'queued' || status === 'running' || status === 'cancelling'
}

async function cancelRun(run: any) {
  const id = run.run_id ?? run.id
  if (!id || !confirm(`Cancel backtest ${id}?`)) return
  await btStore.controlRun(id, 'cancel')
  pollProgress(id)
}

async function deleteRun(run: any) {
  const id = confirmBacktestDelete(run)
  if (!id) return
  await btStore.deleteRun(id)
}

function msToDate(ms?: number | null) {
  return ms ? new Date(ms).toISOString().slice(0, 10) : ''
}

function fmtDateTime(ms?: number | null) {
  return formatDateTime(ms).replace('-', '—')
}

function fmtPeriod(run: any) {
  return `${fmtDateTime(run?.from_time)} → ${fmtDateTime(run?.to_time)}`
}

function metric(run: any, key: string) {
  const metrics = run?.metrics?.metrics ?? run?.metrics ?? {}
  if (key === 'trades_total') return metrics.trades_total ?? metrics.total_trades ?? run?.trades_total ?? run?.total_trades ?? null
  if (key === 'total_trades') return metrics.total_trades ?? metrics.trades_total ?? run?.total_trades ?? run?.trades_total ?? null
  return metrics?.[key] ?? run?.[key] ?? null
}

function fmtNumber(value: any, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

function fmtPct(value: any) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const pct = Math.abs(Number(value)) <= 1 ? Number(value) * 100 : Number(value)
  return `${pct.toFixed(1)}%`
}

function fmtPnl(run: any) {
  const value = metric(run, 'net_profit')
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}`
}

function fmtEstimate(e: any) {
  if (!e) return ''
  const adjusted = e.adjusted ? `range ${msToDate(e.requested_from)} -> ${msToDate(e.effective_from)}` : 'range ok'
  return `${e.symbol} ${e.timeframe}: ${e.estimated_bars?.toLocaleString?.() ?? e.estimated_bars} bars, ${e.estimated_pages} pages, ${adjusted}`
}

function marketDataContext(e: any) {
  const source = e ?? selectedStrategy.value ?? {}
  const exchange = source.exchange ?? selectedStrategy.value?.exchange ?? '—'
  const market = source.market_type ?? selectedStrategy.value?.market_type ?? '—'
  const symbol = source.symbol ?? selectedStrategy.value?.symbol ?? '—'
  const timeframe = source.timeframe ?? selectedStrategy.value?.timeframe ?? '—'
  return `${exchange} / ${market} / ${symbol} / ${timeframe}`
}

function availabilityTone(e: any) {
  if (!form.value.strategy_id) return 'border-dark-500 bg-dark-700/40 text-gray-400'
  if (estimateLoading.value) return 'border-accent/30 bg-accent/10 text-accent-light'
  if (!e) return 'border-warning/40 bg-warning/10 text-warning'
  if (e.adjusted) return 'border-warning/40 bg-warning/10 text-warning'
  return 'border-success/40 bg-success/10 text-success'
}

function availabilityLabel(e: any) {
  if (!form.value.strategy_id) return 'Select strategy first'
  if (estimateLoading.value) return 'Checking market data…'
  if (!e) return 'Select date range to estimate data'
  if (e.adjusted) return 'Range auto-adjusted to listed data'
  return 'Selected range is available'
}

function effectiveRange(e: any) {
  if (!e) return '—'
  return `${msToDate(e.effective_from)} → ${msToDate(e.effective_to)}`
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-gray-200">🧪 Backtests</h1>
      <button @click="showRun = !showRun" class="px-3 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg transition-colors">
        + Run Backtest
      </button>
    </div>

    <!-- Run Form -->
    <transition name="fade">
      <div v-if="showRun" class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <select v-model="form.strategy_id" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option value="" disabled>Select strategy</option>
            <option v-for="s in stStore.items" :key="s.strategy_id ?? s.id" :value="s.strategy_id ?? s.id">
              {{ s.name ?? s.strategy_id }}
            </option>
          </select>
          <div class="sm:col-span-2">
            <DateRangePicker
              :from="form.from_time"
              :to="form.to_time"
              :all-from="allAvailableFrom"
              @update:from="form.from_time = $event"
              @update:to="form.to_time = $event"
            />
          </div>
        </div>
        <div class="rounded-lg border px-3 py-3 text-xs" :class="availabilityTone(estimate ?? availability)">
          <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div class="font-medium">{{ availabilityLabel(estimate ?? availability) }}</div>
              <div class="mt-1 font-mono text-[11px] opacity-90">{{ marketDataContext(estimate ?? availability) }}</div>
            </div>
            <div class="text-left sm:text-right">
              <div class="text-[11px] uppercase tracking-wide opacity-70">Effective range</div>
              <div class="font-mono text-[11px]">{{ effectiveRange(estimate ?? availability) }}</div>
            </div>
          </div>
          <div v-if="estimate || availability || estimateLoading" class="mt-2 text-[11px] opacity-85">
            {{ estimateLoading ? 'Estimating bars/pages…' : fmtEstimate(estimate ?? availability) }}
          </div>
        </div>
        <div class="flex gap-2 justify-end items-center">
          <span v-if="runStatus" class="text-xs" :class="runStatus.startsWith('❌') ? 'text-danger' : 'text-success'">{{ runStatus }}</span>
          <button @click="showRun = false; runStatus = ''" class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200">Cancel</button>
          <button @click="runBacktest" :disabled="runLoading" class="px-4 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg disabled:opacity-50">
            {{ runLoading ? 'Starting...' : '🧪 Run' }}
          </button>
        </div>
      </div>
    </transition>

    <!-- Table -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 overflow-hidden">
      <div class="md:hidden divide-y divide-dark-600/60">
        <div v-if="btStore.items.length === 0" class="px-4 py-8 text-center text-gray-500">
          {{ btStore.loading ? 'Loading...' : 'No backtests yet' }}
        </div>
        <div v-for="run in btStore.items" :key="run.run_id ?? run.id" class="p-4 space-y-3" @click="expandRun(run.run_id ?? run.id)">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="font-medium text-gray-200 truncate">{{ run.strategy_name ?? run.strategy_id ?? '—' }}</div>
              <div class="text-xs text-gray-500 font-mono">v{{ run.version ?? 1 }} · {{ run.symbol }} {{ run.timeframe }}</div>
            </div>
            <span :class="[statusBadge(run.status), 'shrink-0 px-2 py-0.5 rounded-full text-xs font-medium']">{{ run.status ?? '—' }}</span>
          </div>
          <div class="text-xs text-gray-400 break-words">{{ fmtPeriod(run) }}</div>
          <div class="grid grid-cols-3 gap-3 text-xs">
            <div><span class="text-gray-500">Trades</span><div class="font-mono text-gray-200">{{ fmtNumber(metric(run, 'trades_total')) }}</div></div>
            <div><span class="text-gray-500">Win</span><div class="font-mono text-gray-200">{{ fmtPct(metric(run, 'win_rate')) }}</div></div>
            <div><span class="text-gray-500">Net</span><div class="font-mono" :class="Number(metric(run, 'net_profit') ?? 0) >= 0 ? 'text-success' : 'text-danger'">{{ fmtPnl(run) }}</div></div>
          </div>
          <div v-if="btStore.getProgress(run.run_id ?? run.id)" class="space-y-1">
            <div class="h-1.5 bg-dark-600 rounded-full overflow-hidden">
              <div class="h-full rounded-full bg-accent transition-all duration-500" :style="{ width: ((btStore.getProgress(run.run_id ?? run.id)?.pct ?? 0) * 100) + '%' }" />
            </div>
            <div class="text-[10px] text-gray-500 truncate">{{ btStore.getProgress(run.run_id ?? run.id)?.message }}</div>
          </div>
          <div class="flex gap-2" @click.stop>
            <button
              v-if="isControllable(run)"
              class="flex-1 px-2 py-2 rounded bg-warning/20 hover:bg-warning/30 text-xs text-warning"
              @click="cancelRun(run)"
            >
              Cancel
            </button>
            <button
              class="flex-1 px-2 py-2 rounded bg-danger/20 hover:bg-danger/30 text-xs text-danger"
              @click="deleteRun(run)"
            >
              Delete
            </button>
          </div>
        </div>
      </div>
      <table class="hidden md:table w-full text-sm">
        <thead>
          <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
            <th class="px-4 py-2.5 text-left">Strategy</th>
            <th class="px-4 py-2.5 text-left">Version</th>
            <th class="px-4 py-2.5 text-left">Date</th>
            <th class="px-4 py-2.5 text-left">Period</th>
            <th class="px-4 py-2.5 text-left">Status</th>
            <th class="px-4 py-2.5 text-right">Trades</th>
            <th class="px-4 py-2.5 text-right">Win</th>
            <th class="px-4 py-2.5 text-right">PnL</th>
            <th class="px-4 py-2.5 text-left w-32">Progress</th>
            <th class="px-2 py-2.5 w-10"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="btStore.items.length === 0">
            <td colspan="10" class="px-4 py-8 text-center text-gray-500">
              {{ btStore.loading ? 'Loading...' : 'No backtests yet' }}
            </td>
          </tr>
          <template v-for="run in btStore.items" :key="run.run_id ?? run.id">
            <tr
              class="border-b border-dark-600/50 hover:bg-dark-700/50 cursor-pointer transition-colors"
              @click="expandRun(run.run_id ?? run.id)"
            >
              <td class="px-4 py-2.5 font-medium text-gray-200 max-w-[140px] truncate">{{ run.strategy_name ?? run.strategy_id ?? '—' }}</td>
              <td class="px-4 py-2.5 text-gray-400 font-mono text-xs">v{{ run.version ?? 1 }}</td>
              <td class="px-4 py-2.5 text-gray-400 text-xs">{{ fmtDateTime(run.started_at) }}</td>
              <td class="px-4 py-2.5 text-gray-400 text-xs max-w-[220px] truncate">{{ fmtPeriod(run) }}</td>
              <td class="px-4 py-2.5">
                <span :class="[statusBadge(run.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">
                  {{ run.status ?? '—' }}
                </span>
              </td>
              <td class="px-4 py-2.5 text-right text-gray-300 font-mono">{{ fmtNumber(metric(run, 'trades_total')) }}</td>
              <td class="px-4 py-2.5 text-right text-gray-300 font-mono">{{ fmtPct(metric(run, 'win_rate')) }}</td>
              <td class="px-4 py-2.5 text-right font-mono" :class="Number(metric(run, 'net_profit') ?? 0) >= 0 ? 'text-success' : 'text-danger'">{{ fmtPnl(run) }}</td>
              <td class="px-4 py-2.5">
                <div v-if="btStore.getProgress(run.run_id ?? run.id)" class="flex items-center gap-2">
                  <div class="flex-1 h-1.5 bg-dark-600 rounded-full overflow-hidden">
                    <div
                      class="h-full rounded-full transition-all duration-500"
                      :class="(btStore.getProgress(run.run_id ?? run.id)?.pct ?? 0) >= 1 ? 'bg-success' : 'bg-accent'"
                      :style="{ width: ((btStore.getProgress(run.run_id ?? run.id)?.pct ?? 0) * 100) + '%' }"
                    />
                  </div>
	                  <span class="text-xs text-gray-500">{{ ((btStore.getProgress(run.run_id ?? run.id)?.pct ?? 0) * 100).toFixed(0) }}%</span>
	                </div>
	                <div v-if="btStore.getProgress(run.run_id ?? run.id)?.message" class="mt-1 text-[10px] text-gray-500 truncate">
	                  {{ btStore.getProgress(run.run_id ?? run.id)?.message }}
	                </div>
                <span v-else class="text-xs text-gray-500">{{ run.status === 'completed' ? '100%' : (run.status === 'failed' ? '❌' : '—') }}</span>
              </td>
              <td class="px-2 py-2.5 text-center">
                <button
                  v-if="isControllable(run)"
                  @click.stop="cancelRun(run)"
                  class="mr-1 p-1 rounded hover:bg-warning/20 text-gray-500 hover:text-warning transition-colors"
                  title="Cancel run"
                >
                  ⏹
                </button>
                <button
                  @click.stop="deleteRun(run)"
                  class="p-1 rounded hover:bg-danger/20 text-gray-500 hover:text-danger transition-colors"
                  title="Delete run"
                >
                  🗑
                </button>
              </td>
            </tr>
            <!-- Expanded detail -->
            <tr v-if="expandedId === (run.run_id ?? run.id)">
              <td colspan="10" class="px-4 py-3 bg-dark-900/50">
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                  <div>
                    <span class="text-xs text-gray-500">Win Rate</span>
                    <div class="text-sm font-bold text-gray-200">{{ fmtPct(metric(btStore.current, 'win_rate')) }}</div>
                  </div>
                  <div>
                    <span class="text-xs text-gray-500">Trades</span>
                    <div class="text-sm font-bold text-gray-200">{{ fmtNumber(metric(btStore.current, 'trades_total')) }}</div>
                  </div>
                  <div>
                    <span class="text-xs text-gray-500">Max Drawdown</span>
                    <div class="text-sm font-bold text-danger">{{ fmtPct(metric(btStore.current, 'max_drawdown_pct') ?? metric(btStore.current, 'max_drawdown')) }}</div>
                  </div>
                  <div>
                    <span class="text-xs text-gray-500">Period</span>
                    <div class="text-sm font-bold text-gray-200 truncate">{{ fmtPeriod(btStore.current ?? run) }}</div>
                  </div>
                </div>
                <div v-if="(run.status === 'done' || run.status === 'completed')" class="flex gap-2">
                  <a :href="'/api/backtest/runs/' + (run.run_id ?? run.id) + '/equity'" target="_blank" class="px-3 py-1.5 rounded-lg bg-dark-600 hover:bg-dark-500 text-xs text-gray-300">📥 Equity CSV</a>
                  <a :href="'/api/backtest/runs/' + (run.run_id ?? run.id) + '/report'" target="_blank" class="px-3 py-1.5 rounded-lg bg-dark-600 hover:bg-dark-500 text-xs text-gray-300">📥 Report</a>
                  <a :href="'/api/backtest/runs/' + (run.run_id ?? run.id) + '/export'" target="_blank" class="px-3 py-1.5 rounded-lg bg-dark-600 hover:bg-dark-500 text-xs text-gray-300">📥 Export</a>
                </div>
                <div v-else class="text-xs text-gray-500">
                  {{ run.status === 'failed' ? '❌ Backtest failed — no artifacts' : (run.status === 'cancelled' ? 'Cancelled — no final artifacts' : '⏳ Backtest still running...') }}
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
