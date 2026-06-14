<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { backfillDataSeries, deleteDataOrders, deleteDataSeries, getDataHealth, getDataSummary, refreshDataSeries, searchMarketSymbols, type MarketSymbolOption } from '@/api/client'
import DateRangePicker from '@/components/DateRangePicker.vue'
import { coverageRangeLabels } from '@/lib/coverageRanges'
import { healthStatusClass, type DataHealthPayload } from '@/lib/dataHealth'
import { loadDataSummaryState } from '@/lib/dataSummaryState'

const summary = ref<any>(null)
const health = ref<DataHealthPayload | null>(null)
const loadError = ref('')
const loading = ref(false)
const filterExchange = ref('')
const filterMarket = ref('')
const filterTimeframe = ref('')
const filterStatus = ref('')
const actionId = ref<string | null>(null)
const actionStatus = ref<Record<string, any>>({})
const discoverExchange = ref('binance')
const discoverMarket = ref('spot')
const discoverQuery = ref('BTC')
const discoverResults = ref<MarketSymbolOption[]>([])
const discoverLoading = ref(false)
const discoverError = ref('')
const backfillDialog = ref(false)
const backfillRow = ref<any>(null)

function apiErrorMessage(e: any, fallback: string) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item: any) => item?.msg ?? JSON.stringify(item)).join('; ')
  if (detail) return JSON.stringify(detail)
  return e?.message ?? fallback
}

function markActionError(id: string, e: any, fallback: string) {
  const message = apiErrorMessage(e, fallback)
  actionStatus.value = { ...actionStatus.value, [id]: { status: 'failed', message } }
  loadError.value = message
}

const backfillFrom = ref('')
const backfillTo = ref('')
let timer: ReturnType<typeof setInterval>

const series = computed(() => summary.value?.series ?? [])
const sourceSeries = computed(() => {
  const source = series.value.filter((row: any) => (row.role ?? (row.timeframe === '1m' ? 'source' : 'derived')) === 'source')
  return source.length ? source : series.value
})
const visibleSeries = computed(() => sourceSeries.value.filter((row: any) => {
  if (filterExchange.value && row.exchange !== filterExchange.value) return false
  if (filterMarket.value && row.market_type !== filterMarket.value) return false
  if (filterTimeframe.value && row.timeframe !== filterTimeframe.value) return false
  if (filterStatus.value && row.status !== filterStatus.value) return false
  return true
}))
const exchangeOptions = computed(() => health.value?.exchanges ?? [])
const marketOptions = computed(() => {
  const markets = new Map<string, string>()
  for (const exchange of health.value?.exchanges ?? []) {
    for (const market of exchange.markets ?? []) markets.set(market.id, market.label)
  }
  return Array.from(markets, ([id, label]) => ({ id, label })).sort((a, b) => a.id.localeCompare(b.id))
})
const discoverMarketOptions = computed(() => {
  const exchange = exchangeOptions.value.find((item) => item.id === discoverExchange.value)
  return exchange?.markets.filter((market) => market.enabled) ?? []
})
const timeframeOptions = computed<string[]>(() => {
  const configured = health.value?.settings.timeframes as string[] | undefined
  if (configured?.length) return configured
  const values = sourceSeries.value.map((row: any) => String(row.timeframe ?? '')).filter((item: string) => item.length > 0)
  return Array.from(new Set<string>(values)).sort()
})
const statusOptions = computed<string[]>(() => {
  const values = sourceSeries.value.map((row: any) => String(row.status ?? '')).filter((item: string) => item.length > 0)
  return Array.from(new Set<string>(values)).sort()
})
const visibleTotalBars = computed(() => visibleSeries.value.reduce((total: number, row: any) => {
  const count = Number(row.bar_count ?? 0)
  return total + (Number.isFinite(count) ? count : 0)
}, 0))
const orders = computed(() => summary.value?.orders ?? { total: 0, by_symbol: [] })

onMounted(() => {
  load()
  timer = setInterval(() => load(false), 30000)
})
onUnmounted(() => clearInterval(timer))

watch(discoverExchange, () => {
  const firstMarket = discoverMarketOptions.value[0]?.id
  if (firstMarket && !discoverMarketOptions.value.some((market) => market.id === discoverMarket.value)) {
    discoverMarket.value = firstMarket
  }
  discoverResults.value = []
})

watch(health, () => {
  if (!exchangeOptions.value.some((exchange) => exchange.id === discoverExchange.value)) {
    discoverExchange.value = exchangeOptions.value[0]?.id ?? 'binance'
  }
  const firstMarket = discoverMarketOptions.value[0]?.id
  if (firstMarket && !discoverMarketOptions.value.some((market) => market.id === discoverMarket.value)) {
    discoverMarket.value = firstMarket
  }
})

async function discoverSymbols() {
  discoverLoading.value = true
  discoverError.value = ''
  try {
    discoverResults.value = await searchMarketSymbols(discoverQuery.value, discoverExchange.value, discoverMarket.value)
  } catch (e: any) {
    discoverResults.value = []
    discoverError.value = apiErrorMessage(e, 'Symbol discovery failed')
  } finally {
    discoverLoading.value = false
  }
}

async function load(showSpinner = true) {
  if (showSpinner) loading.value = true
  try {
    const state = await loadDataSummaryState(summary.value, async () => {
      const { data } = await getDataSummary()
      return data
    })
    summary.value = state.summary
    loadError.value = state.error
    try {
      const { data: healthData } = await getDataHealth()
      health.value = healthData
    } catch (e: any) {
      const message = apiErrorMessage(e, 'Market data health failed')
      loadError.value = state.error ? `${state.error}; ${message}` : message
    }
  } finally {
    loading.value = false
  }
}

async function refreshSeries(id: string) {
  actionId.value = id
  try {
    const { data } = await refreshDataSeries(id)
    actionStatus.value = { ...actionStatus.value, [id]: data }
    await load(false)
  } catch (e: any) {
    markActionError(id, e, 'Refresh failed')
  } finally {
    actionId.value = null
  }
}

function openBackfill(row: any) {
  backfillRow.value = row
  backfillFrom.value = toDateInput(row.earliest_ms ?? Date.now() - 30 * 24 * 3600 * 1000)
  backfillTo.value = toDateInput(Date.now())
  backfillDialog.value = true
}

async function runBackfill() {
  const row = backfillRow.value
  if (!row || !backfillFrom.value || !backfillTo.value) return
  actionId.value = row.id
  try {
    const { data } = await backfillDataSeries({
      symbol: row.symbol,
      timeframe: row.timeframe,
      from_time: dateOnlyToIso(backfillFrom.value, 'start'),
      to_time: dateOnlyToIso(backfillTo.value, 'end'),
      exchange: row.exchange,
      market_type: row.market_type,
    })
    actionStatus.value = {
      ...actionStatus.value,
      [row.id]: { status: data.status, bars_loaded: 0, message: `Backfill job ${String(data.job_id).slice(0, 8)} queued` },
    }
    backfillDialog.value = false
    await load(false)
  } catch (e: any) {
    markActionError(row.id, e, 'Backfill failed')
  } finally {
    actionId.value = null
  }
}

async function removeSeries(row: any) {
  if (!confirm(`Delete cached bars for ${row.symbol} ${row.timeframe}?`)) return
  actionId.value = row.id
  try {
    await deleteDataSeries(row.id)
    await load(false)
  } catch (e: any) {
    markActionError(row.id, e, 'Delete series failed')
  } finally {
    actionId.value = null
  }
}

async function removeOrders(symbol?: string, strategyId?: string, strategyName?: string, status?: string) {
  const parts = [symbol, strategyName, status].filter(Boolean)
  const label = parts.length ? `${parts.join(' / ')} orders` : 'all orders'
  if (!confirm(`Delete ${label}?`)) return
  try {
    await deleteDataOrders({ symbol, strategy_id: strategyId, status })
    await load(false)
  } catch (e: any) {
    loadError.value = apiErrorMessage(e, 'Delete orders failed')
  }
}

function refreshMessage(row: any) {
  const status = actionStatus.value[row.id]
  if (!status) return ''
  if (status.message) return status.message
  const bars = Number(status.bars_loaded ?? 0).toLocaleString()
  const ranges = status.coverage_ranges_after != null ? `, ${status.coverage_ranges_after} ranges` : ''
  return `${status.status}: ${bars} bars${ranges}`
}

function fmtBytes(bytes?: number) {
  const value = Number(bytes ?? 0)
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

function fmtDate(ms?: number | null) {
  if (!ms) return '—'
  return new Date(ms).toLocaleString()
}

function toDateInput(ms?: number | null) {
  const d = new Date(Number(ms ?? Date.now()))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function dateOnlyToIso(value: string, edge: 'start' | 'end') {
  const [year, month, day] = value.split('-').map(Number)
  const d = new Date(year, month - 1, day)
  if (edge === 'end') d.setHours(23, 59, 59, 999)
  else d.setHours(0, 0, 0, 0)
  if (edge === 'end' && d.getTime() > Date.now()) return new Date().toISOString()
  return d.toISOString()
}

function dataRangeLabels(ranges: any[] = []) {
  return coverageRangeLabels(ranges, fmtDate)
}

function statusClass(status: string) {
  if (status === 'actual') return 'bg-success/20 text-success'
  if (status === 'stale') return 'bg-warning/20 text-warning'
  return 'bg-gray-500/20 text-gray-400'
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-100">Market Data</h1>
        <p class="text-sm text-gray-500">Candles, cache footprint, database size, and execution orders.</p>
      </div>
      <button
        class="px-3 py-2 rounded-lg bg-dark-700 hover:bg-dark-600 text-sm text-gray-200"
        @click="load()"
      >
        Refresh
      </button>
    </div>

    <div
      v-if="loadError"
      class="rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
    >
      Market data refresh failed: {{ loadError }}
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <div class="bg-dark-800 border border-dark-500 rounded-xl p-4">
        <div class="text-xs text-gray-500 uppercase tracking-wider">Total Size</div>
        <div class="mt-2 text-xl font-bold text-gray-100">{{ fmtBytes(summary?.total_size_bytes) }}</div>
      </div>
      <div class="bg-dark-800 border border-dark-500 rounded-xl p-4">
        <div class="text-xs text-gray-500 uppercase tracking-wider">Database</div>
        <div class="mt-2 text-xl font-bold text-gray-100">{{ fmtBytes(summary?.database_size_bytes) }}</div>
      </div>
      <div class="bg-dark-800 border border-dark-500 rounded-xl p-4">
        <div class="text-xs text-gray-500 uppercase tracking-wider">Cache</div>
        <div class="mt-2 text-xl font-bold text-gray-100">{{ fmtBytes(summary?.cache_size_bytes) }}</div>
      </div>
      <div class="bg-dark-800 border border-dark-500 rounded-xl p-4">
        <div class="text-xs text-gray-500 uppercase tracking-wider">Bars</div>
        <div class="mt-2 text-xl font-bold text-gray-100">{{ visibleTotalBars.toLocaleString() }}</div>
      </div>
      <div class="bg-dark-800 border border-dark-500 rounded-xl p-4">
        <div class="text-xs text-gray-500 uppercase tracking-wider">Orders</div>
        <div class="mt-2 text-xl font-bold text-gray-100">{{ orders.total ?? 0 }}</div>
      </div>
    </div>

    <div class="bg-dark-800 rounded-xl border border-dark-500">
      <div class="px-4 py-3 border-b border-dark-500 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 class="text-sm font-semibold text-gray-300">Exchange Catalog</h2>
          <p class="text-xs text-gray-500">Native marketdata adapters, configured timeframes, stable-pair policy, and local cache status.</p>
        </div>
        <RouterLink to="/settings" class="text-xs text-accent hover:text-accent-light">Settings →</RouterLink>
      </div>
      <div class="p-4 space-y-4">
        <div class="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <div class="rounded-lg bg-dark-700/60 p-3">
            <div class="text-gray-500">Native exchanges</div>
            <div class="mt-1 text-lg font-semibold text-gray-100">{{ health?.totals.enabled_exchanges ?? '—' }}</div>
          </div>
          <div class="rounded-lg bg-dark-700/60 p-3">
            <div class="text-gray-500">Market types</div>
            <div class="mt-1 text-lg font-semibold text-gray-100">{{ health?.totals.market_types ?? '—' }}</div>
          </div>
          <div class="rounded-lg bg-dark-700/60 p-3">
            <div class="text-gray-500">Cached exchanges</div>
            <div class="mt-1 text-lg font-semibold text-gray-100">{{ health?.totals.cached_exchanges ?? 0 }}</div>
          </div>
          <div class="rounded-lg bg-dark-700/60 p-3">
            <div class="text-gray-500">Timeframes</div>
            <div class="mt-1 truncate text-sm font-mono text-gray-200">{{ health?.settings.timeframes.join(', ') ?? '—' }}</div>
          </div>
        </div>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <div
            v-for="exchange in exchangeOptions"
            :key="exchange.id"
            class="rounded-xl border border-dark-600 bg-dark-700/40 p-3"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="truncate text-sm font-semibold text-gray-200">{{ exchange.name }}</div>
                <div class="text-[11px] text-gray-500">{{ exchange.cached_series }} cached series</div>
              </div>
              <span :class="[healthStatusClass(exchange.status), 'shrink-0 rounded-full border px-2 py-0.5 text-[10px]']">{{ exchange.status }}</span>
            </div>
            <div class="mt-3 flex flex-wrap gap-1.5">
              <span
                v-for="market in exchange.markets"
                :key="market.id"
                :class="[healthStatusClass(market.status), 'rounded border px-1.5 py-0.5 text-[10px]']"
              >
                {{ market.id }} · {{ market.cached_series }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
      <div class="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 class="text-sm font-semibold text-gray-300">Discover Symbols</h2>
          <p class="text-xs text-gray-500">Live symbol search through the selected marketdata adapter. Results respect stable-pair settings by default.</p>
        </div>
        <span class="text-xs text-gray-500">/api/data/symbols</span>
      </div>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_1.4fr_auto]">
        <select v-model="discoverExchange" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option v-for="exchange in exchangeOptions" :key="exchange.id" :value="exchange.id">{{ exchange.name }}</option>
        </select>
        <select v-model="discoverMarket" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option v-for="market in discoverMarketOptions" :key="market.id" :value="market.id">{{ market.label }}</option>
        </select>
        <input
          v-model.trim="discoverQuery"
          class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200 placeholder-gray-500"
          placeholder="BTC, ETH, SOL..."
          @keydown.enter.prevent="discoverSymbols"
        />
        <button class="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-light disabled:opacity-50" :disabled="discoverLoading || !discoverQuery" @click="discoverSymbols">
          {{ discoverLoading ? 'Searching...' : 'Search' }}
        </button>
      </div>
      <div v-if="discoverError" class="mt-3 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">{{ discoverError }}</div>
      <div v-if="discoverResults.length" class="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        <div v-for="symbol in discoverResults" :key="`${symbol.exchange}-${symbol.market}-${symbol.symbol}`" class="rounded-lg border border-dark-600 bg-dark-700/50 p-3">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="truncate font-mono text-sm text-gray-200">{{ symbol.symbol }}</div>
              <div class="mt-1 text-xs text-gray-500">{{ symbol.exchange }} / {{ symbol.market }} · {{ symbol.baseAsset }}/{{ symbol.quoteAsset }}</div>
            </div>
            <span v-if="symbol.contractType" class="shrink-0 rounded bg-dark-600 px-2 py-0.5 text-[10px] uppercase text-gray-300">{{ symbol.contractType }}</span>
          </div>
          <div class="mt-2 text-xs text-gray-500">Use this symbol in Strategies or Backfill after cache creation.</div>
        </div>
      </div>
      <div v-else-if="!discoverLoading" class="mt-4 rounded-lg border border-dashed border-dark-600 px-3 py-3 text-sm text-gray-500">
        Search a pair to verify ticker availability before creating strategy/backfill jobs.
      </div>
    </div>

    <div class="bg-dark-800 rounded-xl border border-dark-500 p-4">
      <div class="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <h2 class="text-sm font-semibold text-gray-300">Candle filters</h2>
        <button class="self-start rounded bg-dark-600 px-2 py-1 text-xs text-gray-300 hover:bg-dark-500" @click="filterExchange = ''; filterMarket = ''; filterTimeframe = ''; filterStatus = ''">Reset</button>
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <select v-model="filterExchange" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option value="">All exchanges</option>
          <option v-for="exchange in exchangeOptions" :key="exchange.id" :value="exchange.id">{{ exchange.name }}</option>
        </select>
        <select v-model="filterMarket" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option value="">All markets</option>
          <option v-for="market in marketOptions" :key="market.id" :value="market.id">{{ market.id }}</option>
        </select>
        <select v-model="filterTimeframe" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option value="">All timeframes</option>
          <option v-for="tf in timeframeOptions" :key="tf" :value="tf">{{ tf }}</option>
        </select>
        <select v-model="filterStatus" class="rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200">
          <option value="">All statuses</option>
          <option v-for="status in statusOptions" :key="status" :value="status">{{ status }}</option>
        </select>
      </div>
    </div>

    <div class="bg-dark-800 rounded-xl border border-dark-500">
      <div class="px-4 py-3 border-b border-dark-500 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-300">Candles</h2>
        <span class="text-xs text-gray-500">{{ visibleSeries.length }} source series</span>
      </div>
      <div class="md:hidden divide-y divide-dark-600/60">
        <div v-if="loading" class="px-4 py-8 text-center text-gray-500">Loading...</div>
        <div v-else-if="visibleSeries.length === 0" class="px-4 py-8 text-center text-gray-500">No candle data</div>
        <div v-for="row in visibleSeries" :key="row.id" class="p-4 space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="font-mono text-gray-200 truncate">{{ row.symbol }}</div>
              <div class="text-xs text-gray-500">{{ row.exchange }} / {{ row.market_type }} / {{ row.timeframe }} / {{ row.role ?? 'source' }}</div>
            </div>
            <span :class="[statusClass(row.status), 'shrink-0 px-2 py-0.5 rounded-full text-xs font-medium']">{{ row.status }}</span>
          </div>
          <div class="grid grid-cols-2 gap-3 text-xs">
            <div><span class="text-gray-500">Bars</span><div class="text-gray-200 font-mono">{{ Number(row.bar_count ?? 0).toLocaleString() }}</div></div>
            <div><span class="text-gray-500">Size</span><div class="text-gray-200 font-mono">{{ fmtBytes(row.size_bytes) }}</div></div>
          </div>
          <div class="text-xs text-gray-400 break-words">{{ fmtDate(row.earliest_ms) }} → {{ fmtDate(row.latest_ms) }}</div>
          <div v-if="refreshMessage(row)" class="text-xs text-accent-light">{{ refreshMessage(row) }}</div>
          <div class="flex flex-wrap gap-1">
            <span v-for="(label, idx) in dataRangeLabels(row.ranges ?? [])" :key="idx" class="px-1.5 py-0.5 rounded bg-dark-600 text-[10px] text-gray-400">{{ label }}</span>
          </div>
          <div class="flex gap-2">
            <button class="flex-1 px-2 py-2 rounded bg-dark-600 hover:bg-dark-500 text-xs text-gray-200 disabled:opacity-50" :disabled="actionId === row.id" @click="refreshSeries(row.id)">Update</button>
            <button class="flex-1 px-2 py-2 rounded bg-accent/20 hover:bg-accent/30 text-xs text-accent-light disabled:opacity-50" :disabled="actionId === row.id" @click="openBackfill(row)">Backfill</button>
            <button class="flex-1 px-2 py-2 rounded bg-danger/20 hover:bg-danger/30 text-xs text-danger disabled:opacity-50" :disabled="actionId === row.id" @click="removeSeries(row)">Delete</button>
          </div>
        </div>
      </div>
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
              <th class="px-4 py-2.5 text-left">Pair</th>
              <th class="px-4 py-2.5 text-left">TF</th>
              <th class="px-4 py-2.5 text-left">Range</th>
              <th class="px-4 py-2.5 text-right">Bars</th>
              <th class="px-4 py-2.5 text-right">Size</th>
              <th class="px-4 py-2.5 text-left">Source</th>
              <th class="px-4 py-2.5 text-left">Status</th>
              <th class="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="8" class="px-4 py-8 text-center text-gray-500">Loading...</td>
            </tr>
            <tr v-else-if="visibleSeries.length === 0">
              <td colspan="8" class="px-4 py-8 text-center text-gray-500">No candle data</td>
            </tr>
            <tr v-for="row in visibleSeries" :key="row.id" class="border-b border-dark-600/50 hover:bg-dark-700/40">
              <td class="px-4 py-2.5">
                <div class="font-mono text-gray-200">{{ row.symbol }}</div>
                <div class="text-xs text-gray-500">{{ row.exchange }} / {{ row.market_type }} / {{ row.role ?? 'source' }}</div>
              </td>
              <td class="px-4 py-2.5 text-gray-300 font-mono">{{ row.timeframe }}</td>
              <td class="px-4 py-2.5 min-w-[260px]">
                <div class="text-gray-300 text-xs">{{ fmtDate(row.earliest_ms) }} → {{ fmtDate(row.latest_ms) }}</div>
                <div class="mt-1 flex flex-wrap gap-1">
                  <span
                    v-for="(label, idx) in dataRangeLabels(row.ranges ?? [])"
                    :key="idx"
                    class="px-1.5 py-0.5 rounded bg-dark-600 text-[10px] text-gray-400"
                  >
                    {{ label }}
                  </span>
                </div>
                <div v-if="refreshMessage(row)" class="mt-1 text-[10px] text-accent-light">{{ refreshMessage(row) }}</div>
              </td>
              <td class="px-4 py-2.5 text-right text-gray-200 font-mono">{{ Number(row.bar_count ?? 0).toLocaleString() }}</td>
              <td class="px-4 py-2.5 text-right text-gray-300 font-mono">{{ fmtBytes(row.size_bytes) }}</td>
              <td class="px-4 py-2.5 text-xs text-gray-400">{{ (row.sources ?? []).join(', ') }}</td>
              <td class="px-4 py-2.5">
                <span :class="[statusClass(row.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">
                  {{ row.status }}
                </span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <div class="flex justify-end gap-2">
                  <button
                    class="px-2 py-1 rounded bg-dark-600 hover:bg-dark-500 text-xs text-gray-200 disabled:opacity-50"
                    :disabled="actionId === row.id"
                    @click="refreshSeries(row.id)"
                  >
                    Update
                  </button>
                  <button
                    class="px-2 py-1 rounded bg-accent/20 hover:bg-accent/30 text-xs text-accent-light disabled:opacity-50"
                    :disabled="actionId === row.id"
                    @click="openBackfill(row)"
                  >
                    Backfill
                  </button>
                  <button
                    class="px-2 py-1 rounded bg-danger/20 hover:bg-danger/30 text-xs text-danger disabled:opacity-50"
                    :disabled="actionId === row.id"
                    @click="removeSeries(row)"
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="bg-dark-800 rounded-xl border border-dark-500">
      <div class="px-4 py-3 border-b border-dark-500 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-300">Orders</h2>
        <button
          class="px-2 py-1 rounded bg-danger/20 hover:bg-danger/30 text-xs text-danger disabled:opacity-40"
          :disabled="!orders.total"
          @click="removeOrders()"
        >
          Delete All
        </button>
      </div>
      <div class="md:hidden divide-y divide-dark-600/60">
        <div v-if="!(orders.by_strategy ?? orders.by_symbol ?? []).length" class="px-4 py-6 text-center text-gray-500 text-sm">
          No orders stored
        </div>
        <div
          v-for="row in (orders.by_strategy ?? orders.by_symbol)"
          :key="`${row.symbol}-${row.strategy_id ?? 'all'}-${row.status ?? ''}`"
          class="p-4 space-y-3"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="font-mono text-sm text-gray-200">{{ row.symbol }}</div>
              <div class="mt-1 break-words text-sm text-gray-300">{{ row.strategy_name ?? 'All strategies' }}</div>
              <div v-if="row.strategy_id" class="mt-0.5 break-all font-mono text-[10px] leading-snug text-gray-500">{{ row.strategy_id }}</div>
            </div>
            <span class="shrink-0 rounded bg-dark-600 px-2 py-0.5 font-mono text-xs text-gray-200">{{ row.count }}</span>
          </div>
          <div class="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span class="text-gray-500">Status</span>
              <div class="mt-1 text-gray-300">{{ row.status ?? '—' }}</div>
            </div>
            <div>
              <span class="text-gray-500">Latest</span>
              <div class="mt-1 text-gray-300">{{ fmtDate(row.latest_ms) }}</div>
            </div>
          </div>
          <button class="w-full rounded bg-danger/20 px-2 py-2 text-xs text-danger hover:bg-danger/30" @click="removeOrders(row.symbol, row.strategy_id, row.strategy_name, row.status)">
            Delete
          </button>
        </div>
      </div>
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
              <th class="px-4 py-2.5 text-left">Symbol</th>
              <th class="px-4 py-2.5 text-left">Strategy</th>
              <th class="px-4 py-2.5 text-left">Status</th>
              <th class="px-4 py-2.5 text-right">Orders</th>
              <th class="px-4 py-2.5 text-left">Latest</th>
              <th class="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!(orders.by_strategy ?? orders.by_symbol ?? []).length">
              <td colspan="6" class="px-4 py-6 text-center text-gray-500">No orders stored</td>
            </tr>
            <tr v-for="row in (orders.by_strategy ?? orders.by_symbol)" :key="`${row.symbol}-${row.strategy_id ?? 'all'}-${row.status ?? ''}`" class="border-b border-dark-600/50">
              <td class="px-4 py-2.5 text-gray-200 font-mono">{{ row.symbol }}</td>
              <td class="px-4 py-2.5">
                <div class="max-w-[220px] truncate text-gray-200">{{ row.strategy_name ?? 'All strategies' }}</div>
                <div v-if="row.strategy_id" class="font-mono text-[10px] text-gray-500 truncate">{{ row.strategy_id }}</div>
              </td>
              <td class="px-4 py-2.5 text-gray-400">{{ row.status ?? '—' }}</td>
              <td class="px-4 py-2.5 text-right text-gray-200 font-mono">{{ row.count }}</td>
              <td class="px-4 py-2.5 text-gray-400">{{ fmtDate(row.latest_ms) }}</td>
              <td class="px-4 py-2.5 text-right">
                <button class="px-2 py-1 rounded bg-danger/20 hover:bg-danger/30 text-xs text-danger" @click="removeOrders(row.symbol, row.strategy_id, row.strategy_name, row.status)">
                  Delete
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="backfillDialog" class="fixed inset-0 z-50 flex items-end justify-center bg-black/60 px-4 pb-4 sm:items-center sm:pb-0">
      <div class="w-full max-w-md rounded-xl border border-dark-500 bg-dark-800 p-4 shadow-2xl">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="text-sm font-semibold text-gray-200">Backfill candles</h3>
            <div class="mt-1 truncate font-mono text-xs text-gray-500">
              {{ backfillRow?.exchange }} / {{ backfillRow?.market_type }} / {{ backfillRow?.symbol }} / {{ backfillRow?.timeframe }}
            </div>
          </div>
          <button class="rounded px-2 py-1 text-sm text-gray-400 hover:bg-dark-600" @click="backfillDialog = false">×</button>
        </div>
        <div class="mt-4 grid gap-2">
          <div class="text-xs text-gray-500">Period</div>
          <DateRangePicker
            :from="backfillFrom"
            :to="backfillTo"
            :all-from="toDateInput(backfillRow?.earliest_ms)"
            @update:from="backfillFrom = $event"
            @update:to="backfillTo = $event"
          />
        </div>
        <div class="mt-4 flex gap-2">
          <button class="flex-1 rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-300 hover:bg-dark-500" @click="backfillDialog = false">Cancel</button>
          <button class="flex-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-light disabled:opacity-50" :disabled="!backfillFrom || !backfillTo || actionId === backfillRow?.id" @click="runBackfill">
            Start Backfill
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
