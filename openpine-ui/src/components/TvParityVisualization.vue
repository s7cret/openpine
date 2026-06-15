<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getTvParityChartData,
  getTvParityDiagnosticsCallouts,
  getTvParitySummaryCards,
  getTvParityTopMismatches,
  tvParityReportUrl,
  type TvParityChartData,
  type TvParityDiagnosticsCallout,
  type TvParitySummaryCards,
  type TvParityTopMismatch,
} from '@/api/client'

const props = defineProps<{ runId: string }>()
const { t } = useI18n()

const summary = ref<TvParitySummaryCards | null>(null)
const chart = ref<TvParityChartData | null>(null)
const mismatches = ref<TvParityTopMismatch[]>([])
const callouts = ref<TvParityDiagnosticsCallout[]>([])
const loading = ref(false)
const error = ref('')
const mismatchesLimit = ref(20)

// (6) time-range scrubber state
const scrubFrom = ref<number | null>(null)
const scrubTo = ref<number | null>(null)

// (3) leaderboard sort
const sortBy = ref<'delta_net_profit_abs' | 'delta_entry_price_abs'>('delta_net_profit_abs')
const calloutsExpanded = ref(false)

const canvasRef = ref<HTMLCanvasElement | null>(null)
const canvasSize = ref({ width: 720, height: 220 })

const visiblePoints = computed(() => {
  if (!chart.value) return []
  return chart.value.series.filter((row) => {
    const t = Number(row.t)
    if (Number.isNaN(t)) return true
    if (scrubFrom.value !== null && t < scrubFrom.value) return false
    if (scrubTo.value !== null && t > scrubTo.value) return false
    return true
  })
})

const visibleCallouts = computed(() => {
  return callouts.value.filter((row) => {
    if (scrubFrom.value !== null && row.bar_time < scrubFrom.value) return false
    if (scrubTo.value !== null && row.bar_time > scrubTo.value) return false
    return true
  })
})

const sortedMismatches = computed(() => {
  const key = sortBy.value
  return [...mismatches.value].sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0))
})

const chartBounds = computed(() => {
  const rows = visiblePoints.value
  if (!rows.length) return null
  const xs: number[] = []
  const ys: number[] = []
  for (const row of rows) {
    const t = Number(row.t)
    if (Number.isNaN(t)) continue
    xs.push(t)
    if (typeof row.v === 'number') ys.push(row.v)
  }
  if (!xs.length || !ys.length) return null
  return {
    tMin: Math.min(...xs),
    tMax: Math.max(...xs),
    yMin: Math.min(...ys),
    yMax: Math.max(...ys),
  }
})

const heatmapBuckets = computed(() => {
  if (!chart.value) return []
  const matches = sortedMismatches.value
  if (!matches.length) return []
  // Bucket by 10 equal-time slices across the chart's [tMin, tMax] window.
  const bounds = chartBounds.value
  if (!bounds) return []
  const buckets = 10
  const tMin = bounds.tMin
  const tMax = bounds.tMax
  if (tMax === tMin) return []
  const span = (tMax - tMin) / buckets
  const sums = new Array(buckets).fill(0)
  const counts = new Array(buckets).fill(0)
  for (const row of matches) {
    if (row.bar_time == null) continue
    let idx = Math.floor((row.bar_time - tMin) / span)
    if (idx >= buckets) idx = buckets - 1
    if (idx < 0) idx = 0
    sums[idx] += row.delta_net_profit_abs
    counts[idx] += 1
  }
  return sums.map((sum, idx) => ({
    idx,
    from: tMin + idx * span,
    to: tMin + (idx + 1) * span,
    intensity: counts[idx] ? sum / counts[idx] : 0,
    count: counts[idx],
  }))
})

const maxHeatIntensity = computed(() => {
  return heatmapBuckets.value.reduce((acc, b) => Math.max(acc, b.intensity), 0) || 1
})

onMounted(() => {
  resizeCanvas()
  window.addEventListener('resize', resizeCanvas)
  loadAll()
})

watch(
  () => props.runId,
  () => {
    scrubFrom.value = null
    scrubTo.value = null
    loadAll()
  },
)

watch([scrubFrom, scrubTo], () => {
  drawCanvas()
})

function resizeCanvas() {
  if (!canvasRef.value) return
  const parent = canvasRef.value.parentElement
  if (!parent) return
  const width = Math.max(320, parent.clientWidth)
  canvasSize.value = { width, height: 240 }
  if (canvasRef.value) {
    canvasRef.value.width = width * 2
    canvasRef.value.height = canvasSize.value.height * 2
    canvasRef.value.style.width = `${width}px`
    canvasRef.value.style.height = `${canvasSize.value.height}px`
  }
  drawCanvas()
}

async function loadAll() {
  if (!props.runId) return
  loading.value = true
  error.value = ''
  try {
    const [cardsResp, chartResp, mismatchesResp, calloutsResp] = await Promise.all([
      getTvParitySummaryCards(props.runId),
      getTvParityChartData(props.runId),
      getTvParityTopMismatches(props.runId, mismatchesLimit.value),
      getTvParityDiagnosticsCallouts(props.runId),
    ])
    summary.value = cardsResp.data
    chart.value = chartResp.data
    mismatches.value = mismatchesResp.data.items
    callouts.value = calloutsResp.data.callouts
    requestAnimationFrame(drawCanvas)
  } catch (err: any) {
    error.value = err?.response?.data?.detail ?? err?.message ?? 'viz load failed'
  } finally {
    loading.value = false
  }
}

function drawCanvas() {
  const canvas = canvasRef.value
  const bounds = chartBounds.value
  if (!canvas || !bounds) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const w = canvas.width
  const h = canvas.height
  ctx.clearRect(0, 0, w, h)
  // Background
  ctx.fillStyle = '#101418'
  ctx.fillRect(0, 0, w, h)
  // Y grid
  ctx.strokeStyle = '#1f2a35'
  ctx.fillStyle = '#7e94a7'
  ctx.font = '12px ui-sans-serif, system-ui, sans-serif'
  ctx.lineWidth = 1
  const ySteps = 4
  for (let i = 0; i <= ySteps; i += 1) {
    const y = (h * i) / ySteps
    ctx.beginPath()
    ctx.moveTo(40 * 2, y)
    ctx.lineTo(w, y)
    ctx.stroke()
    const value = bounds.yMax - ((bounds.yMax - bounds.yMin) * i) / ySteps
    ctx.fillText(value.toFixed(0), 4, y + 4)
  }
  // X axis labels
  const xSteps = 4
  for (let i = 0; i <= xSteps; i += 1) {
    const x = 40 * 2 + ((w - 40 * 2) * i) / xSteps
    const t = bounds.tMin + ((bounds.tMax - bounds.tMin) * i) / xSteps
    ctx.fillText(new Date(t).toISOString().slice(0, 10), x - 30, h - 4)
  }
  const xScale = (t: number) =>
    40 * 2 + ((w - 40 * 2) * (t - bounds.tMin)) / (bounds.tMax - bounds.tMin || 1)
  const yScale = (v: number) =>
    ((bounds.yMax - v) / (bounds.yMax - bounds.yMin || 1)) * h

  // OpenPine equity (green) and TV equity (blue dashed)
  const drawSeries = (color: string, dash: number[], extractor: (row: any) => [number, number] | null) => {
    let prev: [number, number] | null = null
    ctx.strokeStyle = color
    ctx.setLineDash(dash)
    ctx.lineWidth = 3
    ctx.beginPath()
    let drew = false
    for (const row of visiblePoints.value) {
      const pt = extractor(row)
      if (!pt) continue
      if (!drew) {
        ctx.moveTo(xScale(pt[0]), yScale(pt[1]))
        drew = true
      } else if (prev) {
        ctx.lineTo(xScale(pt[0]), yScale(pt[1]))
      }
      prev = pt
    }
    ctx.stroke()
    ctx.setLineDash([])
  }
  drawSeries('#3ad29f', [], (row) =>
    row.kind === 'openpine_equity' && typeof row.v === 'number' ? [row.t, row.v] : null,
  )
  drawSeries('#5aa9ff', [8, 6], (row) =>
    row.kind === 'tv_equity' && typeof row.v === 'number' ? [row.t, row.v] : null,
  )

  // Diagnostics callouts (P092 markers) as bright yellow dots.
  ctx.fillStyle = '#ffd166'
  ctx.strokeStyle = '#101418'
  ctx.lineWidth = 2
  for (const callout of visibleCallouts.value) {
    const x = xScale(callout.bar_time)
    ctx.beginPath()
    ctx.arc(x, 8, 6, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
  }

  // Top mismatch markers (red triangles) — sized by delta.
  ctx.fillStyle = '#ff7479'
  for (const mismatch of sortedMismatches.value.slice(0, 50)) {
    if (mismatch.bar_time == null) continue
    const x = xScale(mismatch.bar_time)
    if (Number.isNaN(x)) continue
    ctx.beginPath()
    ctx.moveTo(x, h - 8)
    ctx.lineTo(x - 5, h - 2)
    ctx.lineTo(x + 5, h - 2)
    ctx.closePath()
    ctx.fill()
  }
}

function onScrubChange(kind: 'from' | 'to') {
  // clamp: from <= to
  if (scrubFrom.value !== null && scrubTo.value !== null && scrubFrom.value > scrubTo.value) {
    if (kind === 'from') scrubTo.value = scrubFrom.value
    else scrubFrom.value = scrubTo.value
  }
  drawCanvas()
}

function resetScrub() {
  scrubFrom.value = null
  scrubTo.value = null
  drawCanvas()
}

function fmtMs(ms?: number | null) {
  if (!ms) return '—'
  return new Date(ms).toISOString().replace('T', ' ').replace('.000Z', 'Z')
}

function formatDelta(value: number | null | undefined, digits = 6) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toExponential(digits)
}

function formatTimeDelta(ms: number | null | undefined) {
  if (ms === null || ms === undefined || Number.isNaN(ms) || ms <= 0) return '—'
  // 86_400_000 ms = 1 day.  Round to nearest hour for compactness.
  const dayMs = 86_400_000
  const days = ms / dayMs
  if (days >= 1) {
    const hours = Math.round((ms % dayMs) / 3_600_000)
    return hours === 0
      ? `+${days.toFixed(0)}d`
      : `+${days.toFixed(0)}d ${hours}h`
  }
  const hours = Math.round(ms / 3_600_000)
  if (hours >= 1) return `+${hours}h`
  const minutes = Math.round(ms / 60_000)
  return `+${minutes}m`
}

function formatPct(initial: number | null, final: number | null) {
  if (!initial || !final) return '—'
  return `${(((final - initial) / initial) * 100).toFixed(3)}%`
}

function overallBadgeClass(overall: string | undefined) {
  if (overall === 'match') return 'bg-success/20 text-success'
  if (overall === 'mismatch' || overall === 'failed') return 'bg-error/20 text-error'
  return 'bg-dark-600 text-gray-300'
}

function drillIntoMismatch(mismatch: TvParityTopMismatch) {
  if (mismatch.bar_time == null) return
  scrubFrom.value = mismatch.bar_time - 7 * 24 * 60 * 60 * 1000
  scrubTo.value = mismatch.bar_time + 7 * 24 * 60 * 60 * 1000
  onScrubChange('from')
}
</script>

<template>
  <section class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-semibold text-gray-200">
        {{ t('tvParity.viz.title') }}
      </h2>
      <div class="flex flex-wrap items-center gap-2">
        <a
          :href="tvParityReportUrl(runId, 'html')"
          target="_blank"
          rel="noopener"
          class="rounded border border-accent/40 bg-accent/10 px-2 py-1 text-xs text-accent-light hover:border-accent"
        >
          {{ t('tvParity.viz.openHtml') }}
        </a>
        <a
          :href="tvParityReportUrl(runId, 'png')"
          target="_blank"
          rel="noopener"
          class="rounded border border-accent/40 bg-accent/10 px-2 py-1 text-xs text-accent-light hover:border-accent"
        >
          {{ t('tvParity.viz.openPng') }}
        </a>
        <a
          :href="tvParityReportUrl(runId, 'zip')"
          class="rounded border border-accent/40 bg-accent/10 px-2 py-1 text-xs text-accent-light hover:border-accent"
        >
          {{ t('tvParity.viz.downloadZip') }}
        </a>
      </div>
    </div>

    <div v-if="error" class="rounded border border-error/40 bg-error/10 px-3 py-2 text-xs text-error">
      {{ error }}
    </div>

    <!-- (5) Summary cards -->
    <div v-if="summary" class="grid grid-cols-2 md:grid-cols-4 gap-2">
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.status') }}
        </div>
        <div
          class="mt-1 inline-block rounded px-1.5 py-0.5 text-[11px] font-medium"
          :class="overallBadgeClass(summary.overall_status)"
        >
          {{ summary.overall_status }}
        </div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.trades') }}
        </div>
        <div class="mt-1 font-mono text-sm text-gray-200">{{ summary.trades_status }}</div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.maxDeltaPrice') }}
        </div>
        <div class="mt-1 font-mono text-sm text-gray-200">
          {{ formatDelta(summary.max_abs_delta_price ?? summary.max_abs_delta) }}
        </div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.maxDeltaTime') }}
        </div>
        <div class="mt-1 font-mono text-sm text-gray-200">
          {{ formatTimeDelta(summary.max_abs_delta_time_ms) }}
        </div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.failures') }}
        </div>
        <div class="mt-1 font-mono text-sm text-gray-200">{{ summary.failure_count }}</div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3 col-span-2">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.pnl') }}
        </div>
        <div class="mt-1 font-mono text-sm text-gray-200">
          ${{ summary.initial_equity?.toLocaleString?.() ?? '—' }} →
          ${{ summary.final_equity?.toLocaleString?.() ?? '—' }}
          <span class="ml-2 text-xs text-gray-400">
            {{ formatPct(summary.initial_equity, summary.final_equity) }}
          </span>
        </div>
      </div>
      <div class="rounded-lg border border-dark-500 bg-dark-700/50 p-3 col-span-2">
        <div class="text-[10px] uppercase tracking-wide text-gray-500">
          {{ t('tvParity.viz.window') }}
        </div>
        <div class="mt-1 font-mono text-xs text-gray-300">
          {{ fmtMs(summary.compare_from) }} → {{ fmtMs(summary.compare_to) }}
        </div>
      </div>
    </div>

    <!-- (1) Equity overlay + (4) diagnostics callouts + (3) markers -->
    <div class="rounded-lg border border-dark-500 bg-dark-700/30 p-3">
      <div class="mb-2 flex items-center justify-between">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-gray-400">
          {{ t('tvParity.viz.equity') }}
        </h3>
        <div class="flex flex-wrap items-center gap-3 text-[10px] text-gray-400">
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-3 rounded-sm" style="background:#3ad29f"></span>
            {{ t('tvParity.viz.legendOpenpine') }}
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-3 rounded-sm" style="background:#5aa9ff"></span>
            {{ t('tvParity.viz.legendTv') }}
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full" style="background:#ffd166"></span>
            {{ t('tvParity.viz.legendCallout') }}
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2" style="background:#ff7479; clip-path: polygon(50% 0, 0 100%, 100% 100%)"></span>
            {{ t('tvParity.viz.legendMismatch') }}
          </span>
        </div>
      </div>
      <div class="relative w-full">
        <canvas ref="canvasRef" class="block w-full"></canvas>
      </div>
      <div v-if="!loading && !visiblePoints.length" class="py-4 text-center text-xs text-gray-500">
        {{ t('tvParity.viz.noData') }}
      </div>
    </div>

    <!-- (6) Time-range scrubber -->
    <div class="rounded-lg border border-dark-500 bg-dark-700/30 p-3">
      <div class="mb-2 flex items-center justify-between">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-gray-400">
          {{ t('tvParity.viz.scrubTitle') }}
        </h3>
        <button
          type="button"
          class="rounded border border-dark-500 px-2 py-1 text-[10px] text-gray-400 hover:border-accent"
          @click="resetScrub"
        >
          {{ t('tvParity.viz.scrubReset') }}
        </button>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-gray-300">
        <label class="flex flex-col gap-1">
          <span class="text-[10px] uppercase tracking-wide text-gray-500">
            {{ t('tvParity.viz.scrubFrom') }}
          </span>
          <input
            type="datetime-local"
            :value="scrubFrom ? new Date(scrubFrom).toISOString().slice(0, 16) : ''"
            @change="(e) => { const v = (e.target as HTMLInputElement).value; scrubFrom = v ? new Date(v).getTime() : null; onScrubChange('from') }"
            class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-200"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-[10px] uppercase tracking-wide text-gray-500">
            {{ t('tvParity.viz.scrubTo') }}
          </span>
          <input
            type="datetime-local"
            :value="scrubTo ? new Date(scrubTo).toISOString().slice(0, 16) : ''"
            @change="(e) => { const v = (e.target as HTMLInputElement).value; scrubTo = v ? new Date(v).getTime() : null; onScrubChange('to') }"
            class="bg-dark-700 border border-dark-500 rounded px-2 py-1 text-xs text-gray-200"
          />
        </label>
      </div>
    </div>

    <!-- (4) Diagnostics callouts strip — collapsed by default to keep the
         equity chart visible.  Header click toggles the body. -->
    <div class="rounded-lg border border-dark-500 bg-dark-700/30 p-3">
      <button
        type="button"
        class="mb-2 flex w-full items-center justify-between text-left"
        :aria-expanded="calloutsExpanded"
        @click="calloutsExpanded = !calloutsExpanded"
      >
        <span class="flex items-center gap-2">
          <svg
            class="h-3 w-3 text-gray-500 transition-transform"
            :class="calloutsExpanded ? 'rotate-90' : ''"
            viewBox="0 0 16 16"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M6 3l5 5-5 5V3z" />
          </svg>
          <span class="text-xs font-semibold uppercase tracking-wide text-gray-400">
            {{ t('tvParity.viz.calloutsTitle') }}
          </span>
        </span>
        <span class="text-[10px] text-gray-500">
          {{ t('tvParity.viz.calloutsCount', { count: visibleCallouts.length }) }}
          <span v-if="!calloutsExpanded" class="ml-1 text-gray-600">— click to expand</span>
        </span>
      </button>
      <div v-if="!visibleCallouts.length" class="text-xs text-gray-500">
        {{ t('tvParity.viz.calloutsEmpty') }}
      </div>
      <div v-else-if="calloutsExpanded" class="overflow-x-auto">
        <table class="w-full min-w-[640px] text-xs">
          <thead>
            <tr class="text-left text-gray-500 border-b border-dark-500">
              <th class="py-1 pr-2 font-medium">bar_time</th>
              <th class="py-1 pr-2 font-medium text-right">profit</th>
              <th class="py-1 pr-2 font-medium text-right">size</th>
              <th class="py-1 pr-2 font-medium text-right">entry</th>
              <th class="py-1 pr-2 font-medium text-right">exit</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in visibleCallouts"
              :key="row.bar_time"
              class="border-b border-dark-500/40 hover:bg-dark-700/30 cursor-pointer"
              @click="scrubFrom = row.bar_time - 7 * 24 * 60 * 60 * 1000; scrubTo = row.bar_time + 7 * 24 * 60 * 60 * 1000; onScrubChange('from')"
            >
              <td class="py-1 pr-2 font-mono text-gray-200">{{ fmtMs(row.bar_time) }}</td>
              <td class="py-1 pr-2 font-mono text-right text-gray-200">
                {{ row.last_closed_profit ?? '—' }}
              </td>
              <td class="py-1 pr-2 font-mono text-right text-gray-200">
                {{ row.last_closed_size ?? '—' }}
              </td>
              <td class="py-1 pr-2 font-mono text-right text-gray-200">
                {{ row.last_closed_entry_price ?? '—' }}
              </td>
              <td class="py-1 pr-2 font-mono text-right text-gray-200">
                {{ row.last_closed_exit_price ?? '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- (2) Heatmap + (3) leaderboard -->
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-3">
      <div class="rounded-lg border border-dark-500 bg-dark-700/30 p-3 xl:col-span-1">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
          {{ t('tvParity.viz.heatmapTitle') }}
        </h3>
        <div v-if="!heatmapBuckets.length" class="text-xs text-gray-500">
          {{ t('tvParity.viz.heatmapEmpty') }}
        </div>
        <div v-else class="space-y-1">
          <div
            v-for="bucket in heatmapBuckets"
            :key="bucket.idx"
            class="flex items-center gap-2"
          >
            <span class="w-24 shrink-0 font-mono text-[10px] text-gray-400">
              {{ fmtMs(bucket.from).slice(5, 10) }}
            </span>
            <div class="h-3 flex-1 overflow-hidden rounded bg-dark-600">
              <div
                class="h-full"
                :style="{
                  width: `${Math.min(100, (bucket.intensity / maxHeatIntensity) * 100)}%`,
                  background: `rgba(255, 116, 121, ${bucket.count ? 0.35 + 0.55 * (bucket.intensity / maxHeatIntensity) : 0.1})`,
                }"
                :title="`count=${bucket.count} mean|Δ|=${bucket.intensity.toExponential(3)}`"
              ></div>
            </div>
            <span class="w-12 shrink-0 text-right font-mono text-[10px] text-gray-400">
              {{ bucket.count }}
            </span>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-dark-500 bg-dark-700/30 p-3 xl:col-span-2">
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-gray-400">
            {{ t('tvParity.viz.leaderboardTitle') }}
          </h3>
          <div class="flex items-center gap-1 text-[10px] text-gray-400">
            <span>{{ t('tvParity.viz.sortBy') }}:</span>
            <select
              v-model="sortBy"
              class="rounded border border-dark-500 bg-dark-700 px-1 py-0.5 text-[10px] text-gray-200"
            >
              <option value="delta_net_profit_abs">Δ net_profit</option>
              <option value="delta_entry_price_abs">Δ entry</option>
            </select>
            <select
              v-model.number="mismatchesLimit"
              @change="loadAll"
              class="rounded border border-dark-500 bg-dark-700 px-1 py-0.5 text-[10px] text-gray-200"
            >
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </div>
        </div>
        <div v-if="!sortedMismatches.length" class="text-xs text-gray-500">
          {{ t('tvParity.viz.leaderboardEmpty') }}
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full min-w-[520px] text-xs">
            <thead>
              <tr class="text-left text-gray-500 border-b border-dark-500">
                <th class="py-1 pr-2 font-medium">#</th>
                <th class="py-1 pr-2 font-medium">bar_time</th>
                <th class="py-1 pr-2 font-medium">row</th>
                <th class="py-1 pr-2 font-medium text-right">Δ entry</th>
                <th class="py-1 pr-2 font-medium text-right">Δ exit</th>
                <th class="py-1 pr-2 font-medium text-right">Δ net_profit</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in sortedMismatches"
                :key="row.bar_time ?? idx"
                class="border-b border-dark-500/40 cursor-pointer hover:bg-dark-700/40"
                @click="drillIntoMismatch(row)"
              >
                <td class="py-1 pr-2 font-mono text-gray-500">{{ idx + 1 }}</td>
                <td class="py-1 pr-2 font-mono text-gray-200">{{ fmtMs(row.bar_time) }}</td>
                <td class="py-1 pr-2 font-mono text-gray-300">{{ row.row_kind }}</td>
                <td class="py-1 pr-2 font-mono text-right text-gray-200">
                  {{ formatDelta(row.delta_entry_price) }}
                </td>
                <td class="py-1 pr-2 font-mono text-right text-gray-200">
                  {{ formatDelta(row.delta_exit_price) }}
                </td>
                <td class="py-1 pr-2 font-mono text-right text-gray-200">
                  {{ formatDelta(row.delta_net_profit) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>
