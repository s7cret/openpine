<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart, ColorType, CrosshairMode } from 'lightweight-charts'
import DateRangePicker from './DateRangePicker.vue'
import type { IChartApi, ISeriesApi, CandlestickData, HistogramData, Time, SeriesMarker } from 'lightweight-charts'

interface Trade {
  side: string
  entry_price: number
  exit_price?: number | null
  entry_time?: number | string | null
  exit_time?: number | string | null
  pnl?: number | null
}

const props = defineProps<{
  symbol: string
  timeframe: string
  market: 'spot' | 'futures' | string
  trades?: Trade[]
}>()

const emit = defineEmits<{
  (e: 'update:from', val: string): void
  (e: 'update:to', val: string): void
  (e: 'visibleRange', range: { fromMs: number; toMs: number }): void
  (e: 'dataRange', range: { fromMs: number; toMs: number }): void
}>()

// Default: 1 month ago to now
const now = new Date()
const monthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
const dateFrom = ref(monthAgo.toISOString().slice(0, 10))
const dateTo = ref(now.toISOString().slice(0, 10))

const containerRef = ref<HTMLDivElement | null>(null)
const visibleRange = ref('')
let chart: IChartApi | null = null
let candleSeries: ISeriesApi<'Candlestick'> | null = null
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let resizeObserver: ResizeObserver | null = null

const DARK_BG = '#1a1a2e'
const DARK_TEXT = '#e2e8f0'

// Store loaded candle times for marker alignment
let candleTimes: number[] = []

function toTimestampMs(val: any): number | null {
  if (val == null) return null
  // BusinessDay object { year, month, day }
  if (typeof val === 'object' && 'year' in val && 'month' in val && 'day' in val) {
    return Date.UTC(val.year, val.month - 1, val.day)
  }
  // UTCTimestamp (seconds)
  const n = Number(val)
  return n > 1e12 ? n : n * 1000
}

function formatDate(val: any): string {
  const ms = toTimestampMs(val)
  if (!ms) return '?'
  const d = new Date(ms)
  const day = d.getDate()
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const month = months[d.getMonth()]
  const year = d.getFullYear()
  const h = d.getHours().toString().padStart(2, '0')
  const m = d.getMinutes().toString().padStart(2, '0')
  return `${day} ${month} ${year} ${h}:${m}`
}

function updateVisibleRange() {
  if (!chart) return
  try {
    const range = chart.timeScale().getVisibleRange()
    if (!range) { visibleRange.value = ''; return }
    const from = formatDate(range.from)
    const to = formatDate(range.to)
    visibleRange.value = `${from}  →  ${to}`

    // Emit visible range timestamps for parent filtering
    const fromMs = toTimestampMs(range.from)
    const toMs = toTimestampMs(range.to)
    if (fromMs != null && toMs != null) {
      emit('visibleRange', { fromMs, toMs })
    }
  } catch {
    // getVisibleRange may throw before data is loaded
  }
}

async function fetchKlines(symbol: string, interval: string, market: string) {
  const base = market === 'futures' || market === 'delivery'
    ? 'https://fapi.binance.com/fapi/v1/klines'
    : 'https://api.binance.com/api/v3/klines'

  const startTime = new Date(dateFrom.value + 'T00:00:00Z').getTime()
  const endTime = new Date(dateTo.value + 'T23:59:59Z').getTime()

  const allRaw: any[] = []
  let cursor = startTime
  const maxPages = 20

  for (let i = 0; i < maxPages; i++) {
    const url = `${base}?symbol=${symbol}&interval=${interval}&limit=1000&startTime=${cursor}&endTime=${endTime}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`Binance API ${res.status}`)
    const batch = await res.json()
    if (!batch.length) break
    allRaw.push(...batch)
    cursor = parseInt(batch[batch.length - 1][0]) + 1
    if (cursor >= endTime || batch.length < 1000) break
  }

  return allRaw
}

function mapKlines(raw: any[]): { candles: CandlestickData<Time>[]; volumes: HistogramData<Time>[] } {
  const candles: CandlestickData<Time>[] = []
  const volumes: HistogramData<Time>[] = []
  candleTimes = []
  for (const k of raw) {
    const time = (Math.floor(k[0] / 1000)) as Time
    candleTimes.push(Math.floor(k[0] / 1000))
    candles.push({
      time,
      open: parseFloat(k[1]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3]),
      close: parseFloat(k[4]),
    })
    const close = parseFloat(k[4])
    const open = parseFloat(k[1])
    volumes.push({
      time,
      value: parseFloat(k[5]),
      color: close >= open ? 'rgba(38,166,154,0.4)' : 'rgba(239,83,80,0.4)',
    })
  }
  return { candles, volumes }
}

function toTimestamp(val: number | string | null | undefined): number | null {
  if (val == null) return null
  const n = typeof val === 'string' ? new Date(val).getTime() : val
  return n > 1e12 ? Math.floor(n / 1000) : n
}

function findNearestCandle(tradeTime: number): Time | null {
  if (!candleTimes.length) return null
  let lo = 0
  let hi = candleTimes.length - 1
  let best = candleTimes[0]
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2)
    if (candleTimes[mid] <= tradeTime) {
      best = candleTimes[mid]
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  const diff = Math.abs(tradeTime - best)
  const maxGap = candleTimes.length > 1 ? (candleTimes[1] - candleTimes[0]) * 2 : 300
  return diff <= maxGap ? (best as Time) : null
}

function buildMarkers(): SeriesMarker<Time>[] {
  if (!props.trades?.length || !candleTimes.length) return []

  const markers: SeriesMarker<Time>[] = []

  for (const trade of props.trades) {
    const isBuy = trade.side === 'buy' || trade.side === 'long'
    const isSell = trade.side === 'sell' || trade.side === 'short'

    // Entry marker
    const entryTime = toTimestamp(trade.entry_time)
    if (entryTime != null) {
      const t = findNearestCandle(entryTime)
      if (t != null) {
        markers.push({
          time: t,
          position: isBuy ? 'belowBar' : 'aboveBar',
          color: isBuy ? '#26a69a' : '#ef5350',
          shape: isBuy ? 'arrowUp' : 'arrowDown',
          text: isBuy ? 'BUY' : 'SELL',
        })
      }
    }

    // Exit marker (for closed trades)
    if (trade.exit_price && trade.exit_time) {
      const exitTime = toTimestamp(trade.exit_time)
      if (exitTime != null) {
        const t = findNearestCandle(exitTime)
        if (t != null) {
          const pnlStr = trade.pnl != null ? (trade.pnl >= 0 ? `+${trade.pnl.toFixed(0)}` : trade.pnl.toFixed(0)) : ''
          markers.push({
            time: t,
            position: isBuy ? 'aboveBar' : 'belowBar',
            color: trade.pnl != null && trade.pnl >= 0 ? '#26a69a' : '#ef5350',
            shape: 'circle',
            text: pnlStr ? `EXIT ${pnlStr}` : 'EXIT',
          })
        }
      }
    }
  }

  markers.sort((a, b) => (a.time as number) - (b.time as number))
  return markers
}

async function loadData() {
  if (!chart || !props.symbol) return
  try {
    const raw = await fetchKlines(props.symbol, props.timeframe, props.market)
    const { candles, volumes } = mapKlines(raw)
    if (candleSeries) {
      candleSeries.setData(candles)
      const markers = buildMarkers()
      if (markers.length) {
        candleSeries.setMarkers(markers)
      }
    }
    if (volumeSeries) {
      volumeSeries.setData(volumes)
    }
    chart.timeScale().fitContent()
    // Delay slightly to ensure the chart has rendered the range
    requestAnimationFrame(() => updateVisibleRange())

    // Emit data range (what the DateRangePicker selected)
    const fromMs = new Date(dateFrom.value + 'T00:00:00Z').getTime()
    const toMs = new Date(dateTo.value + 'T23:59:59Z').getTime()
    emit('dataRange', { fromMs, toMs })
  } catch (e) {
    console.error('Chart data load failed', e)
  }
}

function updateMarkers() {
  if (!candleSeries) return
  const markers = buildMarkers()
  candleSeries.setMarkers(markers)
}

function initChart() {
  if (!containerRef.value) return
  chart = createChart(containerRef.value, {
    layout: {
      background: { type: ColorType.Solid, color: DARK_BG },
      textColor: DARK_TEXT,
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.04)' },
      horzLines: { color: 'rgba(255,255,255,0.04)' },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: 'rgba(255,255,255,0.1)',
    },
    timeScale: {
      borderColor: 'rgba(255,255,255,0.1)',
      timeVisible: true,
      secondsVisible: false,
    },
    autoSize: true,
  })

  // Subscribe to visible range changes (pan/zoom)
  chart.timeScale().subscribeVisibleTimeRangeChange(() => {
    updateVisibleRange()
  })

  candleSeries = chart.addCandlestickSeries({
    upColor: '#26a69a',
    downColor: '#ef5350',
    borderDownColor: '#ef5350',
    borderUpColor: '#26a69a',
    wickDownColor: '#ef5350',
    wickUpColor: '#26a69a',
  })

  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
  })
  chart.priceScale('volume').applyOptions({
    scaleMargins: { top: 0.8, bottom: 0 },
  })
}

onMounted(() => {
  initChart()
  loadData()

  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => {
      if (chart && containerRef.value) {
        chart.applyOptions({ width: containerRef.value.clientWidth, height: containerRef.value.clientHeight })
      }
    })
    resizeObserver.observe(containerRef.value)
  }
})

watch(() => [props.symbol, props.timeframe, props.market], () => {
  loadData()
})

watch(() => props.trades, () => {
  updateMarkers()
}, { deep: true })

// Reload when date range changes
watch([dateFrom, dateTo], () => {
  loadData()
})

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart) {
    chart.remove()
    chart = null
  }
  candleSeries = null
  volumeSeries = null
})
</script>

<template>
  <div class="relative flex flex-col h-full">
    <!-- Date range picker + visible range -->
    <div class="flex items-center justify-between mb-2 px-1">
      <DateRangePicker :from="dateFrom" :to="dateTo" @update:from="dateFrom = $event" @update:to="dateTo = $event" />
      <div v-if="visibleRange" class="text-[11px] text-gray-400 whitespace-nowrap ml-3">
        {{ visibleRange }}
      </div>
    </div>
    <div ref="containerRef" class="w-full h-full min-h-[256px] rounded-xl overflow-hidden border border-dark-600" />
  </div>
</template>
