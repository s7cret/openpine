<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useStrategiesStore } from '@/stores/strategies'
import { usePineFilesStore } from '@/stores/pineFiles'
import { useRoute } from 'vue-router'
import { searchBinanceSymbols, getPineArtifacts, getOrders, getPositions, getBacktestRuns, getBacktestTrades } from '@/api/client'
import CandleChart from '@/components/CandleChart.vue'

const store = useStrategiesStore()
const pineStore = usePineFilesStore()
const route = useRoute()
const showAdd = ref(false)
const showDetail = ref<string | null>(null)

// Filter state
const filterName = ref('')
const filterTicker = ref('')
const filterTimeframe = ref('')
const filterMarket = ref('')
const filterStatus = ref('')

const uniqueTickers = computed(() => {
  const set = new Set<string>()
  store.items.forEach((s: any) => { if (s.symbol) set.add(s.symbol) })
  return Array.from(set).sort()
})
const uniqueTimeframes = computed(() => {
  const set = new Set<string>()
  store.items.forEach((s: any) => { if (s.timeframe) set.add(s.timeframe) })
  return Array.from(set).sort()
})
const uniqueMarkets = computed(() => {
  const set = new Set<string>()
  store.items.forEach((s: any) => { if (s.market_type) set.add(s.market_type) })
  return Array.from(set).sort()
})
const uniqueStatuses = computed(() => {
  const set = new Set<string>()
  store.items.forEach((s: any) => { if (s.status) set.add(s.status) })
  return Array.from(set).sort()
})
const filteredStrategies = computed(() => {
  return store.items.filter((s: any) => {
    if (filterName.value && !(s.name ?? '').toLowerCase().includes(filterName.value.toLowerCase())) return false
    if (filterTicker.value && s.symbol !== filterTicker.value) return false
    if (filterTimeframe.value && s.timeframe !== filterTimeframe.value) return false
    if (filterMarket.value && s.market_type !== filterMarket.value) return false
    if (filterStatus.value && (s.status ?? 'idle') !== filterStatus.value) return false
    return true
  })
})

// Add form
const form = ref({
  name: '',
  pine_id: '',
  artifact_id: '',
  symbol: '',
  timeframe: '1h',
  exchange: 'binance',
  market_type: 'spot',
  params_json: '{}',
  mode: 'paper',
})

const timeframes = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w']
const marketTypes = ['spot', 'futures', 'margin', 'delivery']
const symbols = ref<string[]>([])
const symbolSearch = ref('')
const symbolsLoading = ref(false)
const showSymbolDropdown = ref(false)
const artifacts = ref<any[]>([])
const artifactsLoading = ref(false)

onMounted(async () => {
  await store.fetchAll()
  pineStore.fetchAll()
  // Auto-open strategy if navigated from dashboard with ?open=ID
  const openId = route.query.open as string
  if (openId) {
    const match = store.items.find((s: any) => s.strategy_id === openId)
    if (match) {
      await openDetail(match.strategy_id)
    }
  }
})

// When pine file selected, fetch its artifacts
watch(() => form.value.pine_id, async (pineId) => {
  if (!pineId) { artifacts.value = []; form.value.artifact_id = ''; return }
  artifactsLoading.value = true
  try {
    const { data } = await getPineArtifacts(pineId)
    artifacts.value = Array.isArray(data) ? data : []
    // Auto-select first artifact if only one
    if (artifacts.value.length === 1) {
      form.value.artifact_id = artifacts.value[0].artifact_id
    }
  } catch (e) { artifacts.value = [] }
  artifactsLoading.value = false
})

const filteredSymbols = computed(() => {
  if (!symbolSearch.value) return symbols.value.slice(0, 30)
  const q = symbolSearch.value.toLowerCase()
  return symbols.value.filter(s => s.toLowerCase().includes(q)).slice(0, 30)
})

let searchTimeout: ReturnType<typeof setTimeout> | null = null
watch(symbolSearch, (val) => {
  if (searchTimeout) clearTimeout(searchTimeout)
  if (val.length < 1) { symbols.value = []; return }
  searchTimeout = setTimeout(async () => {
    symbolsLoading.value = true
    symbols.value = await searchBinanceSymbols(val, form.value.market_type)
    symbolsLoading.value = false
  }, 300)
})

function selectSymbol(s: string) {
  form.value.symbol = s
  symbolSearch.value = ''
  showSymbolDropdown.value = false
}

function hideSymbolDropdown() {
  window.setTimeout(() => {
    showSymbolDropdown.value = false
  }, 200)
}

const createStatus = ref('')
const createLoading = ref(false)

async function addStrategy() {
  if (!form.value.name || !form.value.symbol || !form.value.pine_id || !form.value.artifact_id) {
    createStatus.value = '❌ Fill all required fields: name, pine file, artifact, symbol'
    return
  }
  createLoading.value = true
  createStatus.value = 'Creating strategy...'
  try {
    await store.create(form.value)
    createStatus.value = '✅ Strategy created!'
    showAdd.value = false
    form.value = { name: '', pine_id: '', artifact_id: '', symbol: '', timeframe: '1h', exchange: 'binance', market_type: 'spot', params_json: '{}', mode: 'paper' }
  } catch (e: any) {
    const msg = e?.response?.data?.detail ?? e?.message ?? 'Unknown error'
    createStatus.value = `❌ ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`
  } finally {
    createLoading.value = false
  }
}

async function openDetail(id: string) {
  showDetail.value = id
  await Promise.all([store.fetchOne(id), loadTrades(id)])
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    running: 'bg-success/20 text-success',
    active: 'bg-success/20 text-success',
    paused: 'bg-warning/20 text-warning',
    pending: 'bg-gray-500/20 text-gray-400',
    stopped: 'bg-danger/20 text-danger',
    idle: 'bg-gray-500/20 text-gray-400',
    error: 'bg-danger/20 text-danger',
  }
  return map[(status ?? '').toLowerCase()] ?? 'bg-gray-500/20 text-gray-400'
}

const controlButtons = [
  { action: 'start', label: '▶ Start', cls: 'bg-success/20 hover:bg-success/30 text-success' },
  { action: 'pause', label: '⏸ Pause', cls: 'bg-warning/20 hover:bg-warning/30 text-warning' },
  { action: 'stop', label: '⏹ Stop', cls: 'bg-danger/20 hover:bg-danger/30 text-danger' },
]

// Trades state
const tradeMode = ref('paper')
const tradeFilterSide = ref('')
const tradeFilterStatus = ref('')
const trades = ref<any[]>([])
const orders = ref<any[]>([])
const backtestTrades = ref<any[]>([])
const chartDateFrom = ref<number | null>(null)
const chartDateTo = ref<number | null>(null)
const chartVisibleFrom = ref<number | null>(null)
const chartVisibleTo = ref<number | null>(null)

async function loadTrades(strategyId: string) {
  try {
    const [ordersRes, positionsRes, btRunsRes] = await Promise.all([
      getOrders(strategyId),
      getPositions(strategyId),
      getBacktestRuns(strategyId, 1),
    ])
    orders.value = ordersRes.data ?? []
    trades.value = positionsRes.data?.recent_trades ?? []

    // Load backtest trades from latest completed run
    const btRuns = (btRunsRes.data ?? []).filter((r: any) => r.status === 'done' || r.status === 'completed')
    if (btRuns.length > 0) {
      try {
        const btTradesRes = await getBacktestTrades(btRuns[0].run_id)
        backtestTrades.value = btTradesRes.data ?? []
      } catch { backtestTrades.value = [] }
    } else {
      backtestTrades.value = []
    }
  } catch (e) {
    console.error('Trades load failed', e)
    trades.value = []
    orders.value = []
    backtestTrades.value = []
  }
}

const allTrades = computed<any[]>(() => {
  if (tradeMode.value === 'live') {
    return orders.value.map((o: any) => ({
      trade_id: o.order_id,
      side: o.side,
      qty: o.filled_quantity || o.qty,
      entry_price: o.avg_fill_price || o.limit_price,
      pnl: null,
      status: o.status,
      created_at: o.created_at,
    }))
  }
  // Paper mode: show backtest trades from latest completed run
  if (backtestTrades.value.length > 0) {
    return backtestTrades.value.map((t: any) => ({
      trade_id: t.trade_id,
      side: t.direction === 'long' ? 'buy' : t.direction === 'short' ? 'sell' : (t.side ?? ''),
      qty: t.qty,
      entry_price: t.entry_price,
      exit_price: t.exit_price,
      pnl: t.net_profit ?? t.pnl ?? null,
      status: t.exit_price ? 'closed' : 'open',
      entry_time: t.entry_time,
    }))
  }
  // Fallback: paper trades from positions
  return trades.value.map((t: any) => ({
    trade_id: t.trade_id,
    side: t.side,
    qty: t.qty,
    entry_price: t.entry_price,
    exit_price: t.exit_price,
    pnl: t.pnl,
    status: t.exit_price ? 'closed' : 'open',
    entry_time: t.entry_time,
  }))
})

const filteredTrades = computed(() => {
  return allTrades.value
    .filter((t: any) => {
      if (tradeFilterSide.value && (t.side ?? '').toLowerCase() !== tradeFilterSide.value) return false
      if (tradeFilterStatus.value && (t.status ?? '').toLowerCase() !== tradeFilterStatus.value) return false
      // Filter by chart visible range
      const tradeTime = t.entry_time ?? t.created_at
      if (tradeTime && chartDateFrom.value && chartDateTo.value) {
        const tMs = typeof tradeTime === 'number' ? (tradeTime > 1e12 ? tradeTime : tradeTime * 1000) : new Date(tradeTime).getTime()
        if (tMs < chartDateFrom.value || tMs > chartDateTo.value) return false
      }
      return true
    })
    .sort((a: any, b: any) => {
      const aTime = a.entry_time ?? a.created_at ?? 0
      const bTime = b.entry_time ?? b.created_at ?? 0
      const aMs = typeof aTime === 'number' ? aTime : new Date(aTime).getTime()
      const bMs = typeof bTime === 'number' ? bTime : new Date(bTime).getTime()
      return bMs - aMs // descending
    })
})

function onChartRangeChange(range: { fromMs: number; toMs: number }) {
  chartVisibleFrom.value = range.fromMs
  chartVisibleTo.value = range.toMs
}

function onDataRangeChange(range: { fromMs: number; toMs: number }) {
  chartDateFrom.value = range.fromMs
  chartDateTo.value = range.toMs
}

function formatTime(ts: number | string | null) {
  if (!ts) return '—'
  const d = typeof ts === 'number' ? new Date(ts > 1e12 ? ts : ts * 1000) : new Date(ts)
  return d.toLocaleString()
}

function tradeStatusBadge(status: string) {
  const map: Record<string, string> = {
    filled: 'bg-success/20 text-success',
    closed: 'bg-gray-500/20 text-gray-400',
    open: 'bg-accent/20 text-accent-light',
    pending: 'bg-warning/20 text-warning',
    cancelled: 'bg-danger/20 text-danger',
  }
  return map[(status ?? '').toLowerCase()] ?? 'bg-gray-500/20 text-gray-400'
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-gray-200">⚡ Strategies</h1>
      <button @click="showAdd = !showAdd" class="px-3 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg transition-colors">
        + Add Strategy
      </button>
    </div>

    <!-- Add Form -->
    <transition name="fade">
      <div v-if="showAdd" class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
        <!-- Row 1: name -->
        <input v-model="form.name" placeholder="Strategy name" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent" />

        <!-- Row 2: pine file + artifact -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <select v-model="form.pine_id" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option value="" disabled>Select Pine file</option>
            <option v-for="p in pineStore.items" :key="p.id ?? p.source_id" :value="p.id ?? p.source_id">
              {{ p.name ?? p.id }}
            </option>
          </select>
          <select v-model="form.artifact_id" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option value="" disabled>{{ artifactsLoading ? 'Loading artifacts...' : 'Select artifact' }}</option>
            <option v-for="a in artifacts" :key="a.artifact_id" :value="a.artifact_id">
              {{ a.artifact_id }} {{ a.compile_meta?.compile_status === 'OK' ? '✓' : '✗' }}
            </option>
          </select>
        </div>

        <!-- Row 3: exchange, market, timeframe -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <select v-model="form.exchange" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option value="binance">Binance</option>
          </select>
          <select v-model="form.market_type" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option v-for="mt in marketTypes" :key="mt" :value="mt">{{ mt }}</option>
          </select>
          <select v-model="form.timeframe" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option v-for="tf in timeframes" :key="tf" :value="tf">{{ tf }}</option>
          </select>
        </div>

        <!-- Row 4: ticker search -->
        <div class="relative">
          <input
            v-model="symbolSearch"
            :placeholder="form.symbol || 'Search ticker on Binance...'"
            @focus="showSymbolDropdown = true"
            @blur="hideSymbolDropdown"
            class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent"
          />
          <div v-if="showSymbolDropdown && (filteredSymbols.length || symbolsLoading)" class="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto bg-dark-700 border border-dark-500 rounded-lg shadow-xl">
            <div v-if="symbolsLoading" class="px-3 py-2 text-xs text-gray-500">Loading from Binance...</div>
            <div
              v-for="s in filteredSymbols"
              :key="s"
              @mousedown.prevent="selectSymbol(s)"
              class="px-3 py-1.5 text-sm text-gray-300 hover:bg-dark-600 cursor-pointer"
            >
              {{ s }}
            </div>
          </div>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 justify-end items-center">
          <span v-if="createStatus" class="text-xs" :class="createStatus.startsWith('❌') ? 'text-danger' : 'text-success'">{{ createStatus }}</span>
          <button @click="showAdd = false; createStatus = ''" class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200">Cancel</button>
          <button @click="addStrategy" :disabled="createLoading" class="px-4 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg disabled:opacity-50">
            {{ createLoading ? 'Creating...' : 'Create' }}
          </button>
        </div>
      </div>
    </transition>

    <!-- Filter Bar -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 p-3 flex flex-wrap gap-2 items-center">
      <input
        v-model="filterName"
        placeholder="Search by name..."
        class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent w-48"
      />
      <select v-model="filterTicker" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">All Tickers</option>
        <option v-for="t in uniqueTickers" :key="t" :value="t">{{ t }}</option>
      </select>
      <select v-model="filterTimeframe" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">All TFs</option>
        <option v-for="tf in uniqueTimeframes" :key="tf" :value="tf">{{ tf }}</option>
      </select>
      <select v-model="filterMarket" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">All Markets</option>
        <option v-for="m in uniqueMarkets" :key="m" :value="m">{{ m }}</option>
      </select>
      <select v-model="filterStatus" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">All Status</option>
        <option v-for="st in uniqueStatuses" :key="st" :value="st">{{ st }}</option>
      </select>
      <span v-if="filteredStrategies.length !== store.items.length" class="text-xs text-gray-500 ml-auto">
        {{ filteredStrategies.length }} / {{ store.items.length }}
      </span>
    </div>

    <!-- Table -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
            <th class="px-4 py-2.5 text-left">Strategy</th>
            <th class="px-4 py-2.5 text-left">Ticker</th>
            <th class="px-4 py-2.5 text-left">TF</th>
            <th class="px-4 py-2.5 text-left">Market</th>
            <th class="px-4 py-2.5 text-left">Status</th>
            <th class="px-4 py-2.5 text-center">Controls</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="filteredStrategies.length === 0">
            <td colspan="6" class="px-4 py-8 text-center text-gray-500">
              {{ store.loading ? 'Loading...' : (store.items.length === 0 ? 'No strategies yet' : 'No strategies match filters') }}
            </td>
          </tr>
          <tr
            v-for="s in filteredStrategies"
            :key="s.strategy_id ?? s.id"
            class="border-b border-dark-600/50 hover:bg-dark-700/50 cursor-pointer transition-colors"
            @click="openDetail(s.strategy_id ?? s.id)"
          >
            <td class="px-4 py-2.5 font-medium text-gray-200 max-w-[120px] sm:max-w-none truncate">{{ s.name ?? '—' }}</td>
            <td class="px-4 py-2.5 text-gray-400 font-mono">{{ s.symbol ?? '—' }}</td>
            <td class="px-4 py-2.5">
              <span class="px-1.5 py-0.5 rounded text-xs bg-dark-500 text-gray-300">{{ s.timeframe ?? '—' }}</span>
            </td>
            <td class="px-4 py-2.5">
              <span class="px-1.5 py-0.5 rounded text-xs bg-dark-500 text-gray-300">{{ s.market_type ?? '—' }}</span>
            </td>
            <td class="px-4 py-2.5">
              <span :class="[statusBadge(s.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">
                {{ s.status ?? 'idle' }}
              </span>
            </td>
            <td class="px-4 py-2.5" @click.stop>
              <div class="flex items-center justify-center gap-1">
                <button
                  v-for="btn in controlButtons"
                  :key="btn.action"
                  @click="store.control(s.strategy_id ?? s.id, btn.action)"
                  :class="[btn.cls, 'px-2 py-1 rounded text-xs transition-colors']"
                  :title="btn.label"
                >
                  {{ btn.label.split(' ')[0] }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Detail Modal -->
    <teleport to="body">
      <transition name="fade">
        <div v-if="showDetail" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" @click.self="showDetail = null">
          <div class="bg-dark-800 rounded-2xl border border-dark-500 w-full max-w-5xl max-h-[92vh] overflow-y-auto resize-y flex flex-col" style="min-height: 400px;">
            <div class="flex items-center justify-between px-5 py-4 border-b border-dark-500">
              <div>
                <h2 class="text-lg font-semibold text-gray-200">{{ store.current?.name ?? 'Strategy' }}</h2>
                <span class="text-xs text-gray-500">{{ store.current?.symbol ?? '' }} · {{ store.current?.timeframe ?? '' }} · {{ store.current?.market_type ?? '' }}</span>
              </div>
              <button @click="showDetail = null" class="p-1.5 rounded-lg hover:bg-dark-600 text-gray-400">✕</button>
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 p-5">
              <div><span class="text-xs text-gray-500">Mode</span><div class="text-sm font-bold text-accent-light">{{ store.current?.mode ?? '—' }}</div></div>
              <div><span class="text-xs text-gray-500">Status</span><div><span :class="[statusBadge(store.current?.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">{{ store.current?.status ?? 'idle' }}</span></div></div>
              <div><span class="text-xs text-gray-500">Enabled</span><div class="text-sm font-bold" :class="store.current?.enabled ? 'text-success' : 'text-gray-500'">{{ store.current?.enabled ? 'Yes' : 'No' }}</div></div>
              <div><span class="text-xs text-gray-500">Pine Source</span><div class="text-xs text-gray-400 font-mono truncate">{{ store.current?.pine_id ?? '—' }}</div></div>
              <div><span class="text-xs text-gray-500">Exchange</span><div class="text-sm text-gray-300">{{ store.current?.exchange ?? '—' }}</div></div>
              <div><span class="text-xs text-gray-500">Created</span><div class="text-xs text-gray-400">{{ store.current?.created_at ? new Date(store.current.created_at).toLocaleString() : '—' }}</div></div>
            </div>

            <div class="px-5 pb-3 flex-1 min-h-[300px]">
              <CandleChart
                :symbol="store.current?.symbol ?? 'BTCUSDT'"
                :timeframe="store.current?.timeframe ?? '15m'"
                :market="store.current?.market_type ?? 'spot'"
                :trades="allTrades"
                class="h-full"
                @visibleRange="onChartRangeChange"
                @dataRange="onDataRangeChange"
              />
            </div>

            <!-- Trades Section -->
            <div class="px-5 pb-3">
              <!-- Toggle: Paper / Live -->
              <div class="flex items-center gap-2 mb-3">
                <span class="text-xs text-gray-500">Trades:</span>
                <button
                  @click="tradeMode = 'paper'"
                  :class="[tradeMode === 'paper' ? 'bg-accent text-white' : 'bg-dark-600 text-gray-400', 'px-3 py-1 rounded-lg text-xs transition-colors']"
                >📄 Paper</button>
                <button
                  @click="tradeMode = 'live'"
                  :class="[tradeMode === 'live' ? 'bg-accent text-white' : 'bg-dark-600 text-gray-400', 'px-3 py-1 rounded-lg text-xs transition-colors']"
                >🔴 Live</button>
              </div>

              <!-- Filter bar for trades -->
              <div class="flex gap-2 mb-2 flex-wrap">
                <select v-model="tradeFilterSide" class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-300">
                  <option value="">All Sides</option>
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
                <select v-model="tradeFilterStatus" class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-300">
                  <option value="">All Status</option>
                  <option value="filled">Filled</option>
                  <option value="pending">Pending</option>
                  <option value="cancelled">Cancelled</option>
                </select>
                <span class="text-xs text-gray-500 self-center ml-auto">
                  {{ filteredTrades.length }} trades
                </span>
              </div>

              <!-- Trades Table -->
              <div class="bg-dark-900 rounded-xl border border-dark-600 overflow-hidden max-h-48 overflow-y-auto">
                <table class="w-full text-xs">
                  <thead>
                    <tr class="text-gray-500 uppercase tracking-wider border-b border-dark-600">
                      <th class="px-3 py-1.5 text-left">Time</th>
                      <th class="px-3 py-1.5 text-left">Side</th>
                      <th class="px-3 py-1.5 text-right">Qty</th>
                      <th class="px-3 py-1.5 text-right">Price</th>
                      <th class="px-3 py-1.5 text-right">PnL</th>
                      <th class="px-3 py-1.5 text-left">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="filteredTrades.length === 0">
                      <td colspan="6" class="px-3 py-4 text-center text-gray-500">No trades yet</td>
                    </tr>
                    <tr v-for="t in filteredTrades" :key="t.trade_id ?? t.order_id" class="border-b border-dark-700/50 hover:bg-dark-800">
                      <td class="px-3 py-1.5 text-gray-400">{{ formatTime(t.entry_time ?? t.created_at) }}</td>
                      <td class="px-3 py-1.5">
                        <span :class="(t.side ?? '').toLowerCase() === 'buy' ? 'text-success' : 'text-danger'">
                          {{ (t.side ?? '').toUpperCase() }}
                        </span>
                      </td>
                      <td class="px-3 py-1.5 text-right text-gray-300 font-mono">{{ t.qty ?? t.filled_quantity ?? '—' }}</td>
                      <td class="px-3 py-1.5 text-right text-gray-300 font-mono">{{ t.entry_price ?? t.avg_fill_price ?? t.limit_price ?? '—' }}</td>
                      <td class="px-3 py-1.5 text-right font-mono" :class="(t.pnl ?? 0) >= 0 ? 'text-success' : 'text-danger'">
                        {{ t.pnl != null ? t.pnl.toFixed(2) : '—' }}
                      </td>
                      <td class="px-3 py-1.5">
                        <span class="px-1.5 py-0.5 rounded text-xs" :class="tradeStatusBadge(t.status)">{{ t.status ?? '—' }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="px-5 pb-5 flex gap-2">
              <button v-for="btn in controlButtons" :key="btn.action" @click="store.control(showDetail!, btn.action)" :class="[btn.cls, 'px-4 py-2 rounded-lg text-sm font-medium transition-colors']">{{ btn.label }}</button>
              <button @click="async () => { await store.remove(showDetail!); showDetail = null }" class="ml-auto px-4 py-2 rounded-lg text-sm font-medium bg-dark-600 hover:bg-dark-500 text-gray-400 transition-colors">🗑 Delete</button>
            </div>
          </div>
        </div>
      </transition>
    </teleport>
  </div>
</template>
