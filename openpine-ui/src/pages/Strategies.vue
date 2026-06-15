<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useStrategiesStore } from '@/stores/strategies'
import { usePineFilesStore } from '@/stores/pineFiles'
import { useRoute } from 'vue-router'
import { searchMarketSymbols, getDataMetadata, getDataTicker24h, getPineArtifacts, getOrders, getPositions, getBacktestRuns, getBacktestTrades, getSettings, type MarketSymbolOption } from '@/api/client'
import CandleChart from '@/components/CandleChart.vue'
import { formatDateTime } from '@/utils/time'
import {
  baseAssetFromSymbol,
  exchangeClass,
  exchangeIcon,
  exchangeLabel,
  loadMissingTickerIcons,
  marketTypeClass,
  marketTypeIcon,
  marketTypeLabel,
  storeMissingTickerIcons,
  tickerIconUrl,
  tickerInitials,
} from '@/lib/marketMeta'
import {
  clearStrategySymbolForMarketChange,
  isCreateDisabled,
  loadStrategySymbolOptions,
  newStrategyForm,
  selectStrategySymbol,
  strategyValidationMessage,
} from '@/lib/strategyForm'
import {
  canSearchSymbols as canSearchExchangeSymbols,
  defaultMarketTypeForExchange,
  EMPTY_MARKET_METADATA,
  exchangeOptionLabel as rawExchangeOptionLabel,
  exchangeSelectOptions,
  marketTypeOptionsForExchange,
  symbolLoadingLabel as rawSymbolLoadingLabel,
  symbolSearchPlaceholder as rawSymbolSearchPlaceholder,
} from '@/lib/marketMetadata'

const { t } = useI18n()
const store = useStrategiesStore()
const pineStore = usePineFilesStore()
const route = useRoute()
const showAdd = ref(false)
const showDetail = ref<string | null>(null)

// Filter state
const filterName = ref('')
const filterTicker = ref('')
const filterExchange = ref('')
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
const uniqueExchanges = computed(() => {
  const set = new Set<string>()
  store.items.forEach((s: any) => { if (s.exchange) set.add(s.exchange) })
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
    if (filterExchange.value && (s.exchange ?? 'binance') !== filterExchange.value) return false
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

const timeframes = ref(['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d'])
const defaultTimeframe = ref('1h')
const marketMetadata = ref(EMPTY_MARKET_METADATA)
const marketMetadataError = ref('')
const settingsError = ref('')
const exchangeOptions = computed(() => exchangeSelectOptions(marketMetadata.value))
const marketTypeOptions = computed(() => marketTypeOptionsForExchange(marketMetadata.value, form.value.exchange))
const symbolSearchEnabled = computed(() => canSearchExchangeSymbols(marketMetadata.value, form.value.exchange))
const symbolPlaceholder = computed(() => rawSymbolSearchPlaceholder(t, marketMetadata.value, form.value.exchange))
const symbols = ref<MarketSymbolOption[]>([])
const symbolSearch = ref('')
const symbolsLoading = ref(false)
const showSymbolDropdown = ref(false)
const artifacts = ref<any[]>([])
const artifactsLoading = ref(false)
const failedTickerIcons = ref<Set<string>>(loadMissingTickerIcons())
const ticker24h = ref<Record<string, any>>({})

function exchangeOptionLabel(option: any) {
  return rawExchangeOptionLabel(t, option)
}

function hasTickerIcon(asset?: string) {
  const base = (asset ?? '').toUpperCase()
  return Boolean(base && !failedTickerIcons.value.has(base))
}

function markTickerIconMissing(asset?: string) {
  const base = (asset ?? '').toUpperCase()
  if (!base) return
  failedTickerIcons.value = new Set([...failedTickerIcons.value, base])
  storeMissingTickerIcons(failedTickerIcons.value)
}

async function loadMarketMetadata() {
  marketMetadataError.value = ''
  try {
    const { data } = await getDataMetadata()
    if (data?.exchanges?.length) {
      marketMetadata.value = data
      form.value.market_type = defaultMarketTypeForExchange(marketMetadata.value, form.value.exchange, form.value.market_type)
    } else {
      marketMetadata.value = EMPTY_MARKET_METADATA
      marketMetadataError.value = t('strategies.metadataUnavailable')
    }
  } catch (e: any) {
    marketMetadata.value = EMPTY_MARKET_METADATA
    marketMetadataError.value = t('strategies.metadataUnavailableDetail', { error: apiErrorMessage(e, 'metadata request failed') })
  }
}

async function loadSettings() {
  settingsError.value = ''
  try {
    const { data } = await getSettings()
    if (data?.marketdata?.timeframes?.length) {
      timeframes.value = data.marketdata.timeframes
      defaultTimeframe.value = data.marketdata.default_timeframe || data.marketdata.timeframes[0] || '1h'
      form.value.timeframe = defaultTimeframe.value
    }
  } catch (e: any) {
    settingsError.value = t('strategies.settingsUnavailable', { error: apiErrorMessage(e, 'settings request failed') })
  }
}

onMounted(async () => {
  await Promise.all([loadMarketMetadata(), loadSettings()])
  await store.fetchAll()
  await pineStore.fetchAll()
  autoFillPineSource(true)
  // Auto-open strategy if navigated from dashboard with ?open=ID
  const openId = route.query.open as string
  if (openId) {
    const match = store.items.find((s: any) => s.strategy_id === openId)
    if (match) {
      await openDetail(match.strategy_id)
    }
  }
})

watch([showAdd, () => form.value.name, () => pineStore.items.length], () => {
  if (showAdd.value) autoFillPineSource(false)
})

watch(showDetail, (strategyId) => {
  if (tradesRefreshTimer) {
    clearInterval(tradesRefreshTimer)
    tradesRefreshTimer = null
  }
  if (strategyId) {
    tradesRefreshTimer = setInterval(() => loadTrades(strategyId), 15000)
  }
})

onUnmounted(() => {
  if (tradesRefreshTimer) clearInterval(tradesRefreshTimer)
})

// When pine file selected, fetch its artifacts
watch(() => form.value.pine_id, async (pineId) => {
  if (!pineId) { artifacts.value = []; form.value.artifact_id = ''; return }
  artifactsLoading.value = true
  try {
    const { data } = await getPineArtifacts(pineId)
    artifacts.value = Array.isArray(data) ? data : []
    const active = selectedPineSource.value?.active_artifact_id
    const preferred = artifacts.value.find((a: any) => a.artifact_id === active)
    form.value.artifact_id = preferred?.artifact_id ?? artifacts.value[0]?.artifact_id ?? ''
  } catch (e) { artifacts.value = [] }
  artifactsLoading.value = false
})

const selectedPineSource = computed(() => {
  return pineStore.items.find((p: any) => (p.id ?? p.source_id) === form.value.pine_id) ?? null
})

function autoFillPineSource(force: boolean) {
  const sources = pineStore.items.filter((p: any) => p.active_artifact_id || (p.id ?? p.source_id))
  if (!sources.length) return
  const exact = sources.find((p: any) => String(p.name ?? '').toLowerCase() === form.value.name.trim().toLowerCase())
  const source = exact ?? sources[0]
  const sourceId = source.id ?? source.source_id
  if (!sourceId) return
  if (force || exact || !form.value.pine_id) {
    form.value.pine_id = sourceId
    form.value.artifact_id = source.active_artifact_id ?? form.value.artifact_id
  }
}

const filteredSymbols = computed(() => {
  if (!symbolSearch.value) return symbols.value.slice(0, 30)
  const q = symbolSearch.value.toLowerCase()
  return symbols.value.filter(s => s.symbol.toLowerCase().includes(q) || s.baseAsset.toLowerCase().includes(q)).slice(0, 30)
})

let searchTimeout: ReturnType<typeof setTimeout> | null = null
watch(symbolSearch, (val) => {
  if (val !== form.value.symbol) {
    form.value.symbol = ''
    createStatus.value = ''
  }
  if (searchTimeout) clearTimeout(searchTimeout)
  if (!symbolSearchEnabled.value) { symbols.value = []; return }
  if (val.length < 1) { symbols.value = []; return }
  searchTimeout = setTimeout(async () => {
    const requested = val
    symbolsLoading.value = true
    const result = await loadStrategySymbolOptions<MarketSymbolOption>(
      requested,
      form.value.exchange,
      form.value.market_type,
      searchMarketSymbols,
    )
    if (symbolSearch.value === requested) {
      symbols.value = result.symbols
      if (result.error) createStatus.value = t('strategies.symbolSearchFailed', { error: result.error })
    }
    symbolsLoading.value = false
  }, 300)
})

watch(() => form.value.exchange, () => {
  form.value.market_type = defaultMarketTypeForExchange(marketMetadata.value, form.value.exchange, form.value.market_type)
  symbols.value = []
  symbolSearch.value = clearStrategySymbolForMarketChange(form.value)
  createStatus.value = ''
})

watch(() => form.value.market_type, () => {
  symbols.value = []
  symbolSearch.value = clearStrategySymbolForMarketChange(form.value)
  createStatus.value = ''
})

function selectSymbol(s: MarketSymbolOption) {
  symbolSearch.value = selectStrategySymbol(form.value, s)
  showSymbolDropdown.value = false
  createStatus.value = ''
}

function hideSymbolDropdown() {
  window.setTimeout(() => {
    showSymbolDropdown.value = false
  }, 200)
}

const createStatus = ref('')
const createLoading = ref(false)
const createDisabled = computed(() => isCreateDisabled(form.value, createLoading.value))
const createHint = computed(() => (createDisabled.value ? strategyValidationMessage(form.value) : ''))
const detailError = ref('')

function apiErrorMessage(e: any, fallback: string) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item: any) => item?.msg ?? JSON.stringify(item)).join('; ')
  if (detail) return JSON.stringify(detail)
  return e?.message ?? fallback
}

async function addStrategy() {
  // No silent auto-fill: the user MUST pick a Pine source (and an
  // artifact becomes available transitively).  Without this guard the
  // form would POST a strategy with whichever Pine file happened to be
  // first in the store, which felt like "I created a strategy without
  // choosing a Pine".  The Create button is already disabled in that
  // state, so this branch only fires on keyboard/Enter submits.
  const validationMessage = strategyValidationMessage(form.value)
  if (validationMessage) {
    createStatus.value = validationMessage
    return
  }
  createLoading.value = true
  createStatus.value = t('strategies.creating')
  try {
    await store.create(form.value)
    createStatus.value = t('strategies.created')
    showAdd.value = false
    form.value = newStrategyForm(defaultTimeframe.value, form.value.exchange, form.value.market_type)
    symbolSearch.value = ''
    symbols.value = []
  } catch (e: any) {
    createStatus.value = t('pineFiles.createFailedPrefix', { error: apiErrorMessage(e, 'Unknown error') })
  } finally {
    createLoading.value = false
  }
}

async function openDetail(id: string) {
  detailError.value = ''
  showDetail.value = id
  try {
    await store.fetchOne(id)
    await Promise.all([
      loadTrades(id),
      loadTicker24h(store.current?.exchange, store.current?.symbol, store.current?.market_type),
    ])
  } catch (e: any) {
    showDetail.value = null
    detailError.value = apiErrorMessage(e, t('strategies.strategyLoadFailed'))
  }
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
  { action: 'start', labelKey: 'strategies.start', cls: 'bg-success/20 hover:bg-success/30 text-success' },
  { action: 'pause', labelKey: 'strategies.pause', cls: 'bg-warning/20 hover:bg-warning/30 text-warning' },
]

function isRunning(s: any) {
  return String(s?.status ?? '').toLowerCase() === 'running' || s?.enabled === true
}

function visibleControlButtons(s: any) {
  return isRunning(s)
    ? controlButtons.filter((btn) => btn.action === 'pause')
    : controlButtons.filter((btn) => btn.action === 'start')
}

function buttonLabel(btn: { labelKey: string }): string {
  return t(btn.labelKey)
}

function tickerKey(exchange?: string, symbol?: string, market?: string) {
  return `${String(exchange ?? 'binance').toLowerCase()}:${String(market ?? 'spot').toLowerCase()}:${String(symbol ?? '').toUpperCase()}`
}

async function loadTicker24h(exchange?: string, symbol?: string, market?: string) {
  const s = String(symbol ?? '').toUpperCase()
  if (!s) return
  const ex = String(exchange ?? 'binance').toLowerCase()
  const m = String(market ?? 'spot').toLowerCase()
  const key = tickerKey(ex, s, m)
  if (ticker24h.value[key]) return
  try {
    const { data } = await getDataTicker24h({ exchange: ex, market_type: m, symbol: s })
    ticker24h.value = { ...ticker24h.value, [key]: data }
  } catch {
    // Best-effort market metadata for the strategy card.
  }
}

function marketStats(s: any) {
  return ticker24h.value[tickerKey(s?.exchange, s?.symbol, s?.market_type)] ?? null
}

function fmtCompact(value: any) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }).format(n)
}

function fmt24hChange(value: any) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function fmtPrice(value: any) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: 8 })
}

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
let tradesRefreshTimer: ReturnType<typeof setInterval> | null = null

async function loadTrades(strategyId: string) {
  try {
    const [ordersRes, positionsRes, btRunsRes] = await Promise.all([
      getOrders(strategyId),
      getPositions(strategyId),
      getBacktestRuns(strategyId, 20),
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
  } catch (e: any) {
    detailError.value = apiErrorMessage(e, t('strategies.tradesLoadFailed'))
    trades.value = []
    orders.value = []
    backtestTrades.value = []
  }
}

function normalizeTradeSide(side: any) {
  const value = String(side ?? '').toLowerCase()
  if (value === 'long') return 'buy'
  if (value === 'short') return 'sell'
  return value
}

function dedupeTradeRows(rows: any[]) {
  const seen = new Set<string>()
  return rows.filter((row) => {
    const key = String(row.trade_id ?? row.order_id ?? `${row.side}:${row.entry_time ?? row.created_at}:${row.entry_price}`)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function toTradeTimeMs(value: any): number | null {
  if (value == null || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isFinite(n)) return n > 1e12 ? n : n * 1000
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : null
}

function tradeOverlapsSelectedPeriod(trade: any) {
  if (chartDateFrom.value == null || chartDateTo.value == null) return true

  const entryMs = toTradeTimeMs(trade.entry_time ?? trade.created_at)
  const exitMs = toTradeTimeMs(trade.exit_time)
  const startMs = entryMs ?? exitMs
  const endMs = exitMs ?? entryMs
  if (startMs == null || endMs == null) return true

  return endMs >= chartDateFrom.value && startMs <= chartDateTo.value
}

const orderTradeRows = computed<any[]>(() => {
  return orders.value.map((o: any) => ({
      trade_id: o.order_id,
      order_id: o.order_id,
      source: 'order',
      side: normalizeTradeSide(o.side),
      qty: o.filled_quantity || o.qty,
      entry_price: o.avg_fill_price || o.limit_price,
      stop_price: o.stop_price,
      take_profit_price: o.take_profit_price,
      pnl: null,
      status: o.status,
      entry_time: o.created_at,
      created_at: o.created_at,
    }))
})

const backtestTradeRows = computed<any[]>(() => {
  return backtestTrades.value.map((t: any) => ({
    trade_id: t.trade_id,
    source: 'backtest',
    side: normalizeTradeSide(t.direction ?? t.side),
    qty: t.qty,
    entry_price: t.entry_price,
    exit_price: t.exit_price,
    pnl: t.net_profit ?? t.pnl ?? null,
    status: t.exit_price ? 'closed' : 'open',
    entry_time: t.entry_time,
  }))
})

const positionTradeRows = computed<any[]>(() => {
  return trades.value.map((t: any) => ({
    trade_id: t.trade_id,
    source: 'position',
    side: normalizeTradeSide(t.side),
    qty: t.qty,
    entry_price: t.entry_price,
    exit_price: t.exit_price,
    pnl: t.pnl,
    status: t.exit_price ? 'closed' : 'open',
    entry_time: t.entry_time,
  }))
})

const allTrades = computed<any[]>(() => {
  if (tradeMode.value === 'live') return orderTradeRows.value

  const historyRows = backtestTradeRows.value.length > 0 ? backtestTradeRows.value : positionTradeRows.value
  // Paper/live mini-backtest executions are stored as orders. Include them in
  // the strategy detail view so timer-driven paper fills appear on the chart
  // and in the trade list immediately after the runner writes them.
  return dedupeTradeRows([...orderTradeRows.value, ...historyRows])
})

const periodTrades = computed(() => {
  return allTrades.value.filter(tradeOverlapsSelectedPeriod)
})

const filteredTrades = computed(() => {
  return periodTrades.value
    .filter((t: any) => {
      if (tradeFilterSide.value && normalizeTradeSide(t.side) !== tradeFilterSide.value) return false
      if (tradeFilterStatus.value && (t.status ?? '').toLowerCase() !== tradeFilterStatus.value) return false
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
  return formatDateTime(ts).replace('-', '—')
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
      <h1 class="text-lg font-semibold text-gray-200">{{ t('strategies.title') }}</h1>
      <button @click="showAdd = !showAdd" class="px-3 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg transition-colors">
        {{ t('strategies.addStrategy') }}
      </button>
    </div>

    <div v-if="detailError" class="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
      {{ detailError }}
    </div>
    <div v-if="marketMetadataError || settingsError" class="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
      <div v-if="marketMetadataError">{{ marketMetadataError }}</div>
      <div v-if="settingsError">{{ settingsError }}</div>
    </div>

    <!-- Add Form -->
    <transition name="fade">
      <div v-if="showAdd" class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
        <!-- Row 1: name -->
        <input v-model="form.name" :placeholder="t('strategies.newStrategyName')" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent" />

        <!-- Row 2: exchange, market, timeframe -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <select v-model="form.exchange" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option
              v-for="exchange in exchangeOptions"
              :key="exchange.id"
              :value="exchange.id"
              :disabled="exchange.disabled"
            >
              {{ exchangeOptionLabel(exchange) }}
            </option>
          </select>
          <select v-model="form.market_type" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option
              v-for="mt in marketTypeOptions"
              :key="mt.id"
              :value="mt.id"
              :disabled="mt.disabled"
            >
              {{ mt.label }}{{ mt.disabled ? t('strategies.dataOnlySuffix') : '' }}
            </option>
          </select>
          <select v-model="form.timeframe" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent">
            <option v-for="tf in timeframes" :key="tf" :value="tf">{{ tf }}</option>
          </select>
        </div>

        <!-- Row 2b: Pine source + artifact.  Required for strategy creation.
             Previously this row was hidden and the form relied on
             autoFillPineSource picking the first pine file from the store;
             that left users with no way to (a) change the source or
             (b) know why the Create button was disabled. -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="text-[10px] uppercase tracking-wide text-gray-500">
              {{ t('strategies.pineSourceLabel') }}
            </label>
            <select
              v-model="form.pine_id"
              data-testid="strategy-pine-source"
              class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent"
            >
              <option value="">{{ t('strategies.pineSourcePlaceholder') }}</option>
              <option
                v-for="p in pineStore.items"
                :key="p.id ?? p.source_id"
                :value="p.id ?? p.source_id"
              >
                {{ p.name ?? (p.id ?? p.source_id) }}
              </option>
            </select>
            <div v-if="!pineStore.items.length" class="text-[10px] text-warning">
              {{ t('strategies.pineSourceEmpty') }}
            </div>
          </div>
          <!-- Artifact is auto-selected from the active compiled version of the
               chosen Pine source.  Hidden from the user; surfaced only as a
               read-only info chip so they can see what will be used. -->
          <div class="space-y-1" data-testid="strategy-pine-artifact-readonly">
            <label class="text-[10px] uppercase tracking-wide text-gray-500">
              {{ t('strategies.pineArtifactLabel') }}
            </label>
            <div
              class="w-full bg-dark-900 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-300 flex items-center gap-2"
            >
              <span class="text-[10px] uppercase tracking-wide text-gray-500 shrink-0">
                {{ t('strategies.pineArtifactAutoLabel') }}
              </span>
              <code
                v-if="form.artifact_id"
                class="font-mono text-xs text-accent truncate"
                :title="form.artifact_id"
              >{{ form.artifact_id }}</code>
              <span v-else class="text-xs text-gray-500">
                {{ artifactsLoading ? t('strategies.pineArtifactAutoLoading') : t('strategies.pineArtifactAutoEmpty') }}
              </span>
            </div>
            <div
              v-if="form.pine_id && !artifacts.length && !artifactsLoading"
              class="text-[10px] text-warning"
            >
              {{ t('strategies.pineArtifactEmpty') }}
            </div>
          </div>
        </div>

        <!-- Row 3: ticker search -->
        <div class="relative">
          <input
            v-model="symbolSearch"
            :placeholder="form.symbol || symbolPlaceholder"
            :disabled="!symbolSearchEnabled"
            @focus="showSymbolDropdown = true"
            @blur="hideSymbolDropdown"
            class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
          />
          <div v-if="showSymbolDropdown && (filteredSymbols.length || symbolsLoading)" class="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto bg-dark-700 border border-dark-500 rounded-lg shadow-xl">
            <div v-if="symbolsLoading" class="px-3 py-2 text-xs text-gray-500">{{ rawSymbolLoadingLabel(t, marketMetadata, form.exchange) }}</div>
            <div
              v-for="s in filteredSymbols"
              :key="s.symbol"
              @mousedown.prevent="selectSymbol(s)"
              class="flex items-center justify-between gap-3 px-3 py-2 text-sm text-gray-300 hover:bg-dark-600 cursor-pointer"
            >
              <span class="flex min-w-0 items-center gap-2">
                <img
                  v-if="hasTickerIcon(s.baseAsset)"
                  :src="tickerIconUrl(s.baseAsset)"
                  :alt="s.baseAsset"
                  class="h-5 w-5 rounded-full bg-dark-600 object-cover"
                  loading="lazy"
                  @error="markTickerIconMissing(s.baseAsset)"
                />
                <span v-else class="grid h-5 w-5 place-items-center rounded-full bg-dark-600 text-[9px] font-semibold text-gray-400">
                  {{ tickerInitials(s.baseAsset) }}
                </span>
                <span class="font-mono text-gray-200">{{ s.symbol }}</span>
              </span>
              <span class="shrink-0 text-right text-xs text-gray-500">
                {{ s.baseAsset }}/{{ s.quoteAsset }}
                <span v-if="s.contractType" class="ml-1 rounded bg-dark-500 px-1 py-0.5 text-[10px] uppercase text-gray-400">{{ s.contractType }}</span>
              </span>
            </div>
          </div>
        </div>

        <!-- Actions -->
        <div class="flex flex-wrap gap-2 justify-end items-center">
          <!-- Live validation hint: shown whenever Create is disabled and no
               explicit error has been raised yet, so the user understands
               why the button is greyed out before clicking it. -->
          <span v-if="createHint && !createStatus" class="min-w-0 flex-1 text-xs text-warning" data-testid="strategy-create-hint">{{ createHint }}</span>
          <span v-else-if="createStatus" class="min-w-0 flex-1 text-xs" :class="createStatus.startsWith('❌') ? 'text-danger' : 'text-success'">{{ createStatus }}</span>
          <button @click="showAdd = false; createStatus = ''" class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200">{{ t('strategies.cancel') }}</button>
          <button
            @click="addStrategy"
            :disabled="createDisabled"
            data-testid="strategy-create-button"
            class="px-4 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg disabled:opacity-50"
          >
            {{ createLoading ? t('strategies.creating') : t('common.create') }}
          </button>
        </div>
      </div>
    </transition>

    <!-- Filter Bar -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 p-3 grid grid-cols-2 gap-2 md:flex md:flex-wrap md:items-center">
      <input
        v-model="filterName"
        :placeholder="t('strategies.searchByName')"
        class="col-span-2 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent md:w-48"
      />
      <select v-model="filterTicker" class="min-w-0 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">{{ t('strategies.allTickers') }}</option>
        <option v-for="t in uniqueTickers" :key="t" :value="t">{{ t }}</option>
      </select>
      <select v-model="filterExchange" class="min-w-0 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">{{ t('strategies.allExchanges') }}</option>
        <option v-for="ex in uniqueExchanges" :key="ex" :value="ex">{{ exchangeLabel(ex) }}</option>
      </select>
      <select v-model="filterTimeframe" class="min-w-0 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">{{ t('strategies.allTfs') }}</option>
        <option v-for="tf in uniqueTimeframes" :key="tf" :value="tf">{{ tf }}</option>
      </select>
      <select v-model="filterMarket" class="min-w-0 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">{{ t('strategies.allMarkets') }}</option>
        <option v-for="m in uniqueMarkets" :key="m" :value="m">{{ m }}</option>
      </select>
      <select v-model="filterStatus" class="min-w-0 bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-accent">
        <option value="">{{ t('strategies.allStatus') }}</option>
        <option v-for="st in uniqueStatuses" :key="st" :value="st">{{ st }}</option>
      </select>
      <span v-if="filteredStrategies.length !== store.items.length" class="col-span-2 text-right text-xs text-gray-500 md:ml-auto">
        {{ filteredStrategies.length }} / {{ store.items.length }}
      </span>
    </div>

    <!-- Table -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 overflow-hidden">
      <div class="md:hidden divide-y divide-dark-600/60">
        <div v-if="filteredStrategies.length === 0" class="px-4 py-8 text-center text-gray-500">
          {{ store.loading ? t('common.loading') : (store.items.length === 0 ? t('strategies.noStrategies') : t('strategies.noMatch')) }}
        </div>
        <div
          v-for="s in filteredStrategies"
          :key="s.strategy_id ?? s.id"
          class="p-4 space-y-3"
          @click="openDetail(s.strategy_id ?? s.id)"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="truncate font-medium text-gray-200">{{ s.name ?? '—' }}</div>
              <div class="mt-1 flex min-w-0 items-center gap-2 font-mono text-xs text-gray-500">
                <img
                  v-if="s.symbol && hasTickerIcon(baseAssetFromSymbol(s.symbol))"
                  :src="tickerIconUrl(baseAssetFromSymbol(s.symbol))"
                  :alt="baseAssetFromSymbol(s.symbol)"
                  class="h-5 w-5 rounded-full bg-dark-600 object-cover"
                  loading="lazy"
                  @error="markTickerIconMissing(baseAssetFromSymbol(s.symbol))"
                />
                <span v-else-if="s.symbol" class="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-dark-600 text-[9px] font-semibold text-gray-400">
                  {{ tickerInitials(baseAssetFromSymbol(s.symbol)) }}
                </span>
                <span class="truncate">{{ s.symbol ?? '—' }}</span>
              </div>
            </div>
            <span :class="[statusBadge(s.status), 'shrink-0 px-2 py-0.5 rounded-full text-xs font-medium']">
              {{ s.status ?? 'idle' }}
            </span>
          </div>

          <div class="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span class="text-gray-500">{{ t('strategies.exchange') }}</span>
              <div class="mt-1">
                <span :class="[exchangeClass(s.exchange), 'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-medium']">
                  <span>{{ exchangeIcon(s.exchange) }}</span>
                  <span>{{ exchangeLabel(s.exchange) }}</span>
                </span>
              </div>
            </div>
            <div>
              <span class="text-gray-500">{{ t('strategies.market') }}</span>
              <div class="mt-1">
                <span class="inline-flex items-center gap-1 rounded bg-dark-500 px-1.5 py-0.5 text-gray-300">
                  <span :class="marketTypeClass(s.market_type)">{{ marketTypeIcon(s.market_type) }}</span>
                  <span>{{ s.market_type ?? '—' }}</span>
                </span>
              </div>
            </div>
            <div>
              <span class="text-gray-500">{{ t('strategies.thTf') }}</span>
              <div class="mt-1 font-mono text-gray-200">{{ s.timeframe ?? '—' }}</div>
            </div>
            <div>
              <span class="text-gray-500">{{ t('strategies.mode') }}</span>
              <div class="mt-1 text-gray-200">{{ s.mode ?? '—' }}</div>
            </div>
          </div>

          <div class="grid grid-cols-1 gap-2 pt-1" @click.stop>
            <button
              v-for="btn in visibleControlButtons(s)"
              :key="btn.action"
              @click="store.control(s.strategy_id ?? s.id, btn.action)"
              :class="[btn.cls, 'min-w-0 rounded px-2 py-2 text-xs transition-colors']"
              :title="buttonLabel(btn)"
            >
              {{ buttonLabel(btn) }}
            </button>
          </div>
        </div>
      </div>
      <table class="hidden md:table w-full text-sm">
        <thead>
          <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thStrategy') }}</th>
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thTicker') }}</th>
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thExchange') }}</th>
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thTf') }}</th>
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thMarket') }}</th>
            <th class="px-4 py-2.5 text-left">{{ t('strategies.thStatus') }}</th>
            <th class="px-4 py-2.5 text-center">{{ t('strategies.thControls') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="filteredStrategies.length === 0">
            <td colspan="7" class="px-4 py-8 text-center text-gray-500">
              {{ store.loading ? t('common.loading') : (store.items.length === 0 ? t('strategies.noStrategies') : t('strategies.noMatch')) }}
            </td>
          </tr>
          <tr
            v-for="s in filteredStrategies"
            :key="s.strategy_id ?? s.id"
            class="border-b border-dark-600/50 hover:bg-dark-700/50 cursor-pointer transition-colors"
            @click="openDetail(s.strategy_id ?? s.id)"
          >
            <td class="px-4 py-2.5 font-medium text-gray-200 max-w-[120px] sm:max-w-none truncate">{{ s.name ?? '—' }}</td>
            <td class="px-4 py-2.5 text-gray-400 font-mono">
              <span class="flex items-center gap-2">
                <img
                  v-if="s.symbol && hasTickerIcon(baseAssetFromSymbol(s.symbol))"
                  :src="tickerIconUrl(baseAssetFromSymbol(s.symbol))"
                  :alt="baseAssetFromSymbol(s.symbol)"
                  class="h-5 w-5 rounded-full bg-dark-600 object-cover"
                  loading="lazy"
                  @error="markTickerIconMissing(baseAssetFromSymbol(s.symbol))"
                />
                <span v-else-if="s.symbol" class="grid h-5 w-5 place-items-center rounded-full bg-dark-600 text-[9px] font-semibold text-gray-400">
                  {{ tickerInitials(baseAssetFromSymbol(s.symbol)) }}
                </span>
                <span>{{ s.symbol ?? '—' }}</span>
              </span>
            </td>
            <td class="px-4 py-2.5">
              <span :class="[exchangeClass(s.exchange), 'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium']">
                <span>{{ exchangeIcon(s.exchange) }}</span>
                <span>{{ exchangeLabel(s.exchange) }}</span>
              </span>
            </td>
            <td class="px-4 py-2.5">
              <span class="px-1.5 py-0.5 rounded text-xs bg-dark-500 text-gray-300">{{ s.timeframe ?? '—' }}</span>
            </td>
            <td class="px-4 py-2.5">
              <span class="inline-flex items-center gap-1 rounded bg-dark-500 px-1.5 py-0.5 text-xs text-gray-300">
                <span :class="marketTypeClass(s.market_type)">{{ marketTypeIcon(s.market_type) }}</span>
                <span>{{ s.market_type ?? '—' }}</span>
              </span>
            </td>
            <td class="px-4 py-2.5">
              <span :class="[statusBadge(s.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">
                {{ s.status ?? 'idle' }}
              </span>
            </td>
            <td class="px-4 py-2.5" @click.stop>
              <div class="flex items-center justify-center gap-1">
                <button
                  v-for="btn in visibleControlButtons(s)"
                  :key="btn.action"
                  @click="store.control(s.strategy_id ?? s.id, btn.action)"
                  :class="[btn.cls, 'px-2 py-1 rounded text-xs transition-colors']"
                  :title="buttonLabel(btn)"
                >
                  {{ buttonLabel(btn).split(' ')[0] }}
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
        <div v-if="showDetail" class="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/60" @click.self="showDetail = null">
          <div class="bg-dark-800 rounded-2xl border border-dark-500 w-full max-w-5xl max-h-[94vh] overflow-y-auto resize-y flex flex-col" style="min-height: 400px;">
            <div class="flex items-start justify-between gap-3 px-4 py-4 sm:px-5 border-b border-dark-500">
              <div class="min-w-0">
                <h2 class="text-lg font-semibold text-gray-200">{{ store.current?.name ?? t('strategies.detailTitle') }}</h2>
                <span class="block truncate text-xs text-gray-500">{{ store.current?.symbol ?? '' }} · {{ store.current?.timeframe ?? '' }} · {{ exchangeLabel(store.current?.exchange) }} · {{ marketTypeLabel(store.current?.market_type) }}</span>
              </div>
              <button @click="showDetail = null" class="shrink-0 p-1.5 rounded-lg hover:bg-dark-600 text-gray-400">✕</button>
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 sm:p-5">
              <div><span class="text-xs text-gray-500">{{ t('strategies.mode') }}</span><div class="text-sm font-bold text-accent-light">{{ store.current?.mode ?? '—' }}</div></div>
              <div><span class="text-xs text-gray-500">{{ t('strategies.status') }}</span><div><span :class="[statusBadge(store.current?.status), 'px-2 py-0.5 rounded-full text-xs font-medium']">{{ store.current?.status ?? 'idle' }}</span></div></div>
              <div><span class="text-xs text-gray-500">{{ t('strategies.enabled') }}</span><div class="text-sm font-bold" :class="store.current?.enabled ? 'text-success' : 'text-gray-500'">{{ store.current?.enabled ? t('common.yes') : t('common.no') }}</div></div>
              <div>
                <span class="text-xs text-gray-500">{{ t('strategies.exchange') }}</span>
                <div>
                  <span :class="[exchangeClass(store.current?.exchange), 'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium']">
                    <span>{{ exchangeIcon(store.current?.exchange) }}</span>
                    <span>{{ exchangeLabel(store.current?.exchange) }}</span>
                  </span>
                </div>
              </div>
              <div><span class="text-xs text-gray-500">{{ t('strategies.market') }}</span><div class="text-sm text-gray-300">{{ marketTypeLabel(store.current?.market_type) }}</div></div>
              <div><span class="text-xs text-gray-500">{{ t('strategies.volume24h') }}</span><div class="text-sm font-bold text-gray-200">{{ fmtCompact(marketStats(store.current)?.quoteVolume ?? marketStats(store.current)?.volume) }}</div></div>
              <div>
                <span class="text-xs text-gray-500">{{ t('strategies.change24h') }}</span>
                <div class="text-sm font-bold" :class="Number(marketStats(store.current)?.priceChangePercent ?? 0) >= 0 ? 'text-success' : 'text-danger'">
                  {{ fmt24hChange(marketStats(store.current)?.priceChangePercent) }}
                </div>
              </div>
              <div><span class="text-xs text-gray-500">{{ t('strategies.created') }}</span><div class="text-xs text-gray-400">{{ formatTime(store.current?.created_at ?? null) }}</div></div>
            </div>

            <div class="px-4 sm:px-5 pb-3 flex-1 min-h-[300px]">
              <CandleChart
                :exchange="store.current?.exchange ?? 'binance'"
                :symbol="store.current?.symbol ?? 'BTCUSDT'"
                :timeframe="store.current?.timeframe ?? '15m'"
                :market="store.current?.market_type ?? 'spot'"
                :trades="periodTrades"
                class="h-full"
                @visibleRange="onChartRangeChange"
                @dataRange="onDataRangeChange"
              />
            </div>

            <!-- Trades Section -->
            <div class="px-4 sm:px-5 pb-3">
              <!-- Toggle: Paper / Live -->
              <div class="flex items-center gap-2 mb-3">
                <span class="text-xs text-gray-500">{{ t('strategies.tradesLabel') }}</span>
                <button
                  @click="tradeMode = 'paper'"
                  :class="[tradeMode === 'paper' ? 'bg-accent text-white' : 'bg-dark-600 text-gray-400', 'px-3 py-1 rounded-lg text-xs transition-colors']"
                >{{ t('strategies.paper') }}</button>
                <button
                  @click="tradeMode = 'live'"
                  :class="[tradeMode === 'live' ? 'bg-accent text-white' : 'bg-dark-600 text-gray-400', 'px-3 py-1 rounded-lg text-xs transition-colors']"
                >{{ t('strategies.live') }}</button>
              </div>

              <!-- Filter bar for trades -->
              <div class="flex gap-2 mb-2 flex-wrap">
                <select v-model="tradeFilterSide" class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-300">
                  <option value="">{{ t('strategies.allSides') }}</option>
                  <option value="buy">{{ t('strategies.buy') }}</option>
                  <option value="sell">{{ t('strategies.sell') }}</option>
                </select>
                <select v-model="tradeFilterStatus" class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-300">
                  <option value="">{{ t('strategies.allTradeStatus') }}</option>
                  <option value="filled">{{ t('strategies.filled') }}</option>
                  <option value="closed">{{ t('strategies.closed') }}</option>
                  <option value="pending">{{ t('strategies.pending') }}</option>
                  <option value="cancelled">{{ t('strategies.cancelled') }}</option>
                </select>
                <span class="text-xs text-gray-500 self-center ml-auto">
                  {{ t('strategies.tradesCount', { count: filteredTrades.length }) }}
                </span>
              </div>

              <!-- Trades Table -->
              <div class="md:hidden bg-dark-900 rounded-xl border border-dark-600 overflow-y-auto max-h-64">
                <div v-if="filteredTrades.length === 0" class="px-3 py-4 text-center text-xs text-gray-500">{{ t('strategies.noTrades') }}</div>
                <div v-else class="divide-y divide-dark-700/60">
                  <div v-for="tItem in filteredTrades" :key="tItem.trade_id ?? tItem.order_id" class="p-3">
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="text-xs text-gray-400">{{ formatTime(tItem.entry_time ?? tItem.created_at) }}</div>
                        <div class="mt-1 flex items-center gap-2">
                          <span :class="(tItem.side ?? '').toLowerCase() === 'buy' ? 'text-success' : 'text-danger'" class="text-sm font-medium">
                            {{ (tItem.side ?? '').toUpperCase() || '—' }}
                          </span>
                          <span class="text-xs text-gray-500">{{ t('strategies.qty') }}</span>
                          <span class="font-mono text-xs text-gray-300">{{ tItem.qty ?? tItem.filled_quantity ?? '—' }}</span>
                        </div>
                      </div>
                      <span class="shrink-0 px-1.5 py-0.5 rounded text-xs" :class="tradeStatusBadge(tItem.status)">{{ tItem.status ?? '—' }}</span>
                    </div>
                    <div class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                      <div>
                        <span class="text-gray-500">{{ t('strategies.price') }}</span>
                        <div class="mt-0.5 font-mono text-gray-300">{{ fmtPrice(tItem.entry_price ?? tItem.avg_fill_price ?? tItem.limit_price) }}</div>
                      </div>
                      <div>
                        <span class="text-gray-500">{{ t('strategies.pnl') }}</span>
                        <div class="mt-0.5 font-mono" :class="(tItem.pnl ?? 0) >= 0 ? 'text-success' : 'text-danger'">
                          {{ tItem.pnl != null ? tItem.pnl.toFixed(2) : '—' }}
                        </div>
                      </div>
                      <div>
                        <span class="text-gray-500">{{ t('strategies.sl') }}</span>
                        <div class="mt-0.5 font-mono text-danger">{{ fmtPrice(tItem.stop_price) }}</div>
                      </div>
                      <div>
                        <span class="text-gray-500">{{ t('strategies.tp') }}</span>
                        <div class="mt-0.5 font-mono text-success">{{ fmtPrice(tItem.take_profit_price) }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="hidden md:block bg-dark-900 rounded-xl border border-dark-600 overflow-x-auto overflow-y-auto max-h-48">
                <table class="w-full text-xs">
                  <thead>
                    <tr class="text-gray-500 uppercase tracking-wider border-b border-dark-600">
                      <th class="px-3 py-1.5 text-left">{{ t('strategies.thTime') }}</th>
                      <th class="px-3 py-1.5 text-left">{{ t('strategies.thSide') }}</th>
                      <th class="px-3 py-1.5 text-right">{{ t('strategies.thQty') }}</th>
                      <th class="px-3 py-1.5 text-right">{{ t('strategies.thPrice') }}</th>
                      <th class="px-3 py-1.5 text-right">{{ t('strategies.thSl') }}</th>
                      <th class="px-3 py-1.5 text-right">{{ t('strategies.thTp') }}</th>
                      <th class="px-3 py-1.5 text-right">{{ t('strategies.thPnl') }}</th>
                      <th class="px-3 py-1.5 text-left">{{ t('strategies.thStatusTrades') }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="filteredTrades.length === 0">
                      <td colspan="8" class="px-3 py-4 text-center text-gray-500">{{ t('strategies.noTrades') }}</td>
                    </tr>
                    <tr v-for="tItem in filteredTrades" :key="tItem.trade_id ?? tItem.order_id" class="border-b border-dark-700/50 hover:bg-dark-800">
                      <td class="px-3 py-1.5 text-gray-400">{{ formatTime(tItem.entry_time ?? tItem.created_at) }}</td>
                      <td class="px-3 py-1.5">
                        <span :class="(tItem.side ?? '').toLowerCase() === 'buy' ? 'text-success' : 'text-danger'">
                          {{ (tItem.side ?? '').toUpperCase() }}
                        </span>
                      </td>
                      <td class="px-3 py-1.5 text-right text-gray-300 font-mono">{{ tItem.qty ?? tItem.filled_quantity ?? '—' }}</td>
                      <td class="px-3 py-1.5 text-right text-gray-300 font-mono">{{ fmtPrice(tItem.entry_price ?? tItem.avg_fill_price ?? tItem.limit_price) }}</td>
                      <td class="px-3 py-1.5 text-right text-danger font-mono">{{ fmtPrice(tItem.stop_price) }}</td>
                      <td class="px-3 py-1.5 text-right text-success font-mono">{{ fmtPrice(tItem.take_profit_price) }}</td>
                      <td class="px-3 py-1.5 text-right font-mono" :class="(tItem.pnl ?? 0) >= 0 ? 'text-success' : 'text-danger'">
                        {{ tItem.pnl != null ? tItem.pnl.toFixed(2) : '—' }}
                      </td>
                      <td class="px-3 py-1.5">
                        <span class="px-1.5 py-0.5 rounded text-xs" :class="tradeStatusBadge(tItem.status)">{{ tItem.status ?? '—' }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="px-4 sm:px-5 pb-5 grid grid-cols-2 gap-2 sm:flex">
              <button v-for="btn in visibleControlButtons(store.current)" :key="btn.action" @click="store.control(showDetail!, btn.action)" :class="[btn.cls, 'px-3 sm:px-4 py-2 rounded-lg text-sm font-medium transition-colors']">{{ buttonLabel(btn) }}</button>
              <button @click="async () => { await store.remove(showDetail!); showDetail = null }" class="col-span-2 sm:ml-auto px-3 sm:px-4 py-2 rounded-lg text-sm font-medium bg-dark-600 hover:bg-dark-500 text-gray-400 transition-colors">{{ t('strategies.delete') }}</button>
            </div>
          </div>
        </div>
      </transition>
    </teleport>
  </div>
</template>
