<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getDataMetadata,
  getStrategies,
  getTvParityRun,
  previewTvParityCandles,
  runTvParity,
  tvParityArtifactUrl,
} from '@/api/client'
import { EMPTY_MARKET_METADATA, exchangeLabel } from '@/lib/marketMetadata'

const { t } = useI18n()
const strategies = ref<any[]>([])
const selectedStrategyId = ref('')
const candlesFile = ref<File | null>(null)
const tvChartFile = ref<File | null>(null)
const tvTradesFile = ref<File | null>(null)
const tvEquityFile = ref<File | null>(null)
const candlesInput = ref<HTMLInputElement | null>(null)
const tvChartInput = ref<HTMLInputElement | null>(null)
const tvTradesInput = ref<HTMLInputElement | null>(null)
const tvEquityInput = ref<HTMLInputElement | null>(null)
const preview = ref<any | null>(null)
const result = ref<any | null>(null)
const lockedPeriod = ref<{ from_time: number; to_time: number } | null>(null)
const loading = ref(false)
const runLoading = ref(false)
const status = ref('')

const form = ref({
  source: 'tradingview_csv',
  fromTime: '',
  compareFromTime: '',
  compareToTime: '',
  warmupBars: 0,
  fullPrehistory: false,
  capturePlots: true,
  absTol: 0.000001,
  relTol: 0.000000001,
  includeBaseColumns: false,
})

const marketMetadata = ref(EMPTY_MARKET_METADATA)
const marketMetadataError = ref('')

const selectedStrategy = computed(() =>
  strategies.value.find((item: any) => (item.strategy_id ?? item.id) === selectedStrategyId.value) ?? null,
)
const selectedStrategyMarketContext = computed(() => {
  const strategy = selectedStrategy.value
  if (!strategy) return null
  const exchange = strategy.exchange ?? ''
  const marketType = strategy.market_type ?? ''
  return {
    exchange,
    exchangeLabel: exchangeLabel(marketMetadata.value, exchange),
    marketType,
    marketTypeLabel: marketTypeLabel(exchange, marketType),
    symbol: strategy.symbol ?? '',
    timeframe: strategy.timeframe ?? '',
  }
})
const artifacts = computed(() => result.value?.artifacts ?? [])
const isExchangeDataSource = computed(() => form.value.source === 'exchange_data')

onMounted(fetchPageData)

async function fetchPageData() {
  await Promise.all([fetchStrategies(), fetchMarketMetadata()])
}

async function fetchStrategies() {
  const { data } = await getStrategies()
  strategies.value = data.items ?? data.strategies ?? data ?? []
}

async function fetchMarketMetadata() {
  marketMetadataError.value = ''
  try {
    const { data } = await getDataMetadata()
    if (data?.exchanges?.length) {
      marketMetadata.value = data
    } else {
      marketMetadata.value = EMPTY_MARKET_METADATA
      marketMetadataError.value = t('tvParity.metadataUnavailable')
    }
  } catch (err: any) {
    marketMetadata.value = EMPTY_MARKET_METADATA
    marketMetadataError.value = t('tvParity.metadataUnavailableDetail', { error: apiErrorMessage(err, 'metadata request failed') })
  }
}

function marketTypeLabel(exchangeId: string, marketTypeId: string) {
  const exchange = marketMetadata.value.exchanges.find((item) => item.id === exchangeId)
  return exchange?.market_types.find((item) => item.id === marketTypeId)?.label ?? (marketTypeId || '—')
}

function fileFromTarget(target: EventTarget | null) {
  const input = target as HTMLInputElement | null
  return input?.files?.[0] ?? null
}

function fileName(file: File | null) {
  return file?.name ?? 'No file selected'
}

function apiErrorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail ?? error?.message ?? fallback
}

function openFilePicker(input: HTMLInputElement | null) {
  input?.click()
}

function setCandlesFile(target: EventTarget | null) {
  candlesFile.value = fileFromTarget(target)
}

function setTvChartFile(target: EventTarget | null) {
  tvChartFile.value = fileFromTarget(target)
}

function setTvTradesFile(target: EventTarget | null) {
  tvTradesFile.value = fileFromTarget(target)
}

function setTvEquityFile(target: EventTarget | null) {
  tvEquityFile.value = fileFromTarget(target)
}

async function previewCandles() {
  if (!candlesFile.value) {
    status.value = t('tvParity.uploadTvCsv')
    return
  }
  const context = selectedStrategyMarketContext.value
  if (!context) {
    status.value = t('tvParity.selectCompiled')
    return
  }
  if (!context.symbol || !context.timeframe || !context.exchange || !context.marketType) {
    status.value = t('tvParity.missingMarket')
    return
  }
  loading.value = true
  status.value = t('tvParity.previewingStatus')
  try {
    const { data } = await previewTvParityCandles({
      candlesFile: candlesFile.value,
      exchange: context.exchange,
      marketType: context.marketType,
      symbol: context.symbol,
      timeframe: context.timeframe,
    })
    preview.value = data
    lockedPeriod.value = data.locked_period ?? { from_time: data.from_time, to_time: data.to_time }
    form.value.compareFromTime = String(lockedPeriod.value?.from_time ?? '')
    form.value.compareToTime = String(lockedPeriod.value?.to_time ?? '')
    status.value = t('tvParity.previewedLocked', { count: data.valid_bars?.toLocaleString?.() ?? data.valid_bars })
  } catch (err: any) {
    status.value = t('tvParity.previewFailed', { error: err.response?.data?.detail ?? err.message })
  } finally {
    loading.value = false
  }
}

async function queueRun() {
  if (!selectedStrategyId.value) {
    status.value = t('tvParity.selectCompiled2')
    return
  }
  const context = selectedStrategyMarketContext.value
  if (!context?.exchange || !context.marketType || !context.symbol || !context.timeframe) {
    status.value = t('tvParity.missingMarket')
    return
  }
  if (!candlesFile.value && !isExchangeDataSource.value) {
    status.value = t('tvParity.uploadTvCsv')
    return
  }
  if (isExchangeDataSource.value && (!form.value.compareFromTime || !form.value.compareToTime)) {
    status.value = t('tvParity.exchangeDataRequiresRange')
    return
  }
  runLoading.value = true
  status.value = t('tvParity.queueingMessage')
  try {
    const { data } = await runTvParity({
      strategyId: selectedStrategyId.value,
      source: form.value.source as 'tradingview_csv' | 'exchange_data',
      candlesFile: isExchangeDataSource.value ? null : candlesFile.value,
      tvChartFile: tvChartFile.value,
      tvTradesFile: tvTradesFile.value,
      tvEquityFile: tvEquityFile.value,
      fromTime: isExchangeDataSource.value && form.value.fullPrehistory ? form.value.fromTime || undefined : undefined,
      compareFromTime: form.value.compareFromTime || undefined,
      compareToTime: form.value.compareToTime || undefined,
      capturePlots: form.value.capturePlots,
      warmupBars: form.value.warmupBars,
      fullPrehistory: isExchangeDataSource.value ? form.value.fullPrehistory : false,
      absTol: form.value.absTol,
      relTol: form.value.relTol,
      includeBaseColumns: form.value.includeBaseColumns,
    })
    result.value = data
    lockedPeriod.value = data.locked_period ?? lockedPeriod.value
    status.value = t('tvParity.queued', { id: data.run_id })
    setTimeout(() => refreshResult(data.run_id), 1500)
  } catch (err: any) {
    status.value = t('tvParity.runFailed', { error: err.response?.data?.detail ?? err.message })
  } finally {
    runLoading.value = false
  }
}

async function refreshResult(runId?: string) {
  const id = runId ?? result.value?.run_id
  if (!id) return
  const { data } = await getTvParityRun(id)
  result.value = data
}

function fmtMs(ms?: number | null) {
  if (!ms) return '—'
  return new Date(ms).toISOString().replace('T', ' ').replace('.000Z', 'Z')
}

function artifactHref(artifact: any) {
  return artifact.download_url || tvParityArtifactUrl(result.value?.run_id ?? '', artifact.name)
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h1 class="text-lg font-semibold text-gray-200">{{ t('tvParity.title') }}</h1>
        <p class="mt-1 text-sm text-gray-400">
          {{ t('tvParity.subtitle') }}
        </p>
      </div>
      <button
        :disabled="!result?.run_id"
        @click="refreshResult()"
        class="px-3 py-1.5 rounded-lg text-sm border border-dark-500 text-gray-300 disabled:opacity-40 hover:border-accent"
      >
        {{ t('tvParity.refreshResult') }}
      </button>
    </div>

    <div v-if="status" class="rounded-lg border border-dark-500 bg-dark-800 px-3 py-2 text-sm text-gray-300">
      {{ status }}
    </div>
    <div v-if="marketMetadataError" class="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
      {{ marketMetadataError }}
    </div>

    <section class="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="xl:col-span-2 bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-4">
        <h2 class="text-sm font-semibold text-gray-200">{{ t('tvParity.sectionInputs') }}</h2>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.dataSource') }}
            <select v-model="form.source" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200">
              <option value="tradingview_csv">{{ t('tvParity.tradingviewCsv') }}</option>
              <option value="exchange_data">{{ t('tvParity.exchangeData') }}</option>
            </select>
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.strategy') }}
            <select v-model="selectedStrategyId" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200">
              <option value="">{{ t('tvParity.selectStrategy') }}</option>
              <option v-for="strategy in strategies" :key="strategy.strategy_id ?? strategy.id" :value="strategy.strategy_id ?? strategy.id">
                {{ strategy.name ?? strategy.strategy_id ?? strategy.id }} · {{ strategy.symbol }} {{ strategy.timeframe }}
              </option>
            </select>
          </label>
          <label v-if="!isExchangeDataSource" class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.tradingviewCsv') }}
            <span class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <button type="button" class="inline-flex w-max rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-200 hover:bg-dark-500" @click="openFilePicker(candlesInput)">
                {{ t('tvParity.chooseFile') }}
              </button>
              <span class="min-w-0 truncate text-sm text-gray-300">{{ fileName(candlesFile) }}</span>
              <input ref="candlesInput" type="file" accept=".csv,text/csv" class="hidden" @change="setCandlesFile($event.target)" />
            </span>
          </label>
          <div class="md:col-span-2 rounded-lg border border-dark-500 bg-dark-700/40 p-3 text-xs text-gray-300">
            <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{{ t('tvParity.strategyContext') }}</div>
            <div v-if="selectedStrategyMarketContext" class="grid grid-cols-2 lg:grid-cols-4 gap-2">
              <div>
                <div class="text-gray-500">{{ t('tvParity.exchange') }}</div>
                <div class="font-mono text-gray-200">{{ selectedStrategyMarketContext.exchangeLabel }}</div>
              </div>
              <div>
                <div class="text-gray-500">{{ t('tvParity.marketType') }}</div>
                <div class="font-mono text-gray-200">{{ selectedStrategyMarketContext.marketTypeLabel }}</div>
              </div>
              <div>
                <div class="text-gray-500">{{ t('tvParity.symbol') }}</div>
                <div class="font-mono text-gray-200">{{ selectedStrategyMarketContext.symbol || '—' }}</div>
              </div>
              <div>
                <div class="text-gray-500">{{ t('tvParity.timeframe') }}</div>
                <div class="font-mono text-gray-200">{{ selectedStrategyMarketContext.timeframe || '—' }}</div>
              </div>
            </div>
            <div v-else class="text-gray-500">{{ t('tvParity.selectCompiledHint') }}</div>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.tvChartCsv') }}
            <span class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <button type="button" class="inline-flex w-max rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-200 hover:bg-dark-500" @click="openFilePicker(tvChartInput)">
                {{ t('tvParity.chooseFile') }}
              </button>
              <span class="min-w-0 truncate text-sm text-gray-300">{{ fileName(tvChartFile) }}</span>
              <input ref="tvChartInput" type="file" accept=".csv,text/csv" class="hidden" @change="setTvChartFile($event.target)" />
            </span>
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.tvTradesCsv') }}
            <span class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <button type="button" class="inline-flex w-max rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-200 hover:bg-dark-500" @click="openFilePicker(tvTradesInput)">
                {{ t('tvParity.chooseFile') }}
              </button>
              <span class="min-w-0 truncate text-sm text-gray-300">{{ fileName(tvTradesFile) }}</span>
              <input ref="tvTradesInput" type="file" accept=".csv,text/csv" class="hidden" @change="setTvTradesFile($event.target)" />
            </span>
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.tvEquityCsv') }}
            <span class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <button type="button" class="inline-flex w-max rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-200 hover:bg-dark-500" @click="openFilePicker(tvEquityInput)">
                {{ t('tvParity.chooseFile') }}
              </button>
              <span class="min-w-0 truncate text-sm text-gray-300">{{ fileName(tvEquityFile) }}</span>
              <input ref="tvEquityInput" type="file" accept=".csv,text/csv" class="hidden" @change="setTvEquityFile($event.target)" />
            </span>
          </label>
        </div>

        <div class="flex flex-wrap gap-2">
          <button :disabled="loading || isExchangeDataSource" @click="previewCandles" class="px-3 py-2 rounded-lg bg-dark-600 hover:bg-dark-500 text-sm text-gray-200 disabled:opacity-50">
            {{ loading ? t('tvParity.previewing') : t('tvParity.previewCandles') }}
          </button>
          <button :disabled="runLoading" @click="queueRun" class="px-3 py-2 rounded-lg bg-accent hover:bg-accent-dark text-sm text-white disabled:opacity-50">
            {{ runLoading ? t('tvParity.queueing') : t('tvParity.runTvParity') }}
          </button>
        </div>
      </div>

      <aside class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
        <h2 class="text-sm font-semibold text-gray-200">{{ t('tvParity.sectionLocked') }}</h2>
        <div class="rounded-lg bg-dark-700/60 border border-dark-500 p-3 text-xs text-gray-300">
          <div class="uppercase tracking-wide text-gray-500">{{ t('tvParity.from') }}</div>
          <div class="font-mono">{{ fmtMs(lockedPeriod?.from_time) }}</div>
          <div class="mt-3 uppercase tracking-wide text-gray-500">{{ t('tvParity.to') }}</div>
          <div class="font-mono">{{ fmtMs(lockedPeriod?.to_time) }}</div>
        </div>
        <div v-if="isExchangeDataSource" class="rounded-lg border border-dark-500 bg-dark-700/40 p-3 text-xs text-gray-300">
          <label class="flex items-center gap-2">
            <input v-model="form.fullPrehistory" type="checkbox" /> {{ t('tvParity.fullPrehistory') }}
          </label>
          <p class="mt-1 text-gray-500">
            {{ t('tvParity.preHistoryHint') }}
          </p>
        </div>
        <div class="grid grid-cols-1 gap-2">
          <label v-if="isExchangeDataSource && form.fullPrehistory" class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.preHistoryFrom') }}
            <input v-model="form.fromTime" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200" :placeholder="t('tvParity.preHistoryPlaceholder')" />
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.compareFrom') }}
            <input v-model="form.compareFromTime" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200" />
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.compareTo') }}
            <input v-model="form.compareToTime" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200" />
          </label>
          <label class="space-y-1 text-xs text-gray-400">
            {{ t('tvParity.warmupBars') }}
            <input v-model.number="form.warmupBars" type="number" min="0" class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200" />
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-300">
            <input v-model="form.capturePlots" type="checkbox" /> {{ t('tvParity.capturePlots') }}
          </label>
          <label class="flex items-center gap-2 text-xs text-gray-300">
            <input v-model="form.includeBaseColumns" type="checkbox" /> {{ t('tvParity.includeBase') }}
          </label>
        </div>
      </aside>
    </section>

    <section class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-200">{{ t('tvParity.sectionResult') }}</h2>
        <span class="text-xs text-gray-500">{{ result?.status ?? t('tvParity.noRunYet') }}</span>
      </div>

      <div v-if="preview" class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <div class="rounded-lg bg-dark-700/60 p-3">
          <div class="text-gray-500">{{ t('tvParity.validBars') }}</div>
          <div class="font-mono text-gray-200">{{ preview.valid_bars }}</div>
        </div>
        <div class="rounded-lg bg-dark-700/60 p-3">
          <div class="text-gray-500">{{ t('tvParity.invalidRows') }}</div>
          <div class="font-mono text-gray-200">{{ preview.invalid_rows }}</div>
        </div>
        <div class="rounded-lg bg-dark-700/60 p-3">
          <div class="text-gray-500">{{ t('tvParity.duplicates') }}</div>
          <div class="font-mono text-gray-200">{{ preview.duplicate_timestamps }}</div>
        </div>
        <div class="rounded-lg bg-dark-700/60 p-3">
          <div class="text-gray-500">{{ t('tvParity.source') }}</div>
          <div class="font-mono text-gray-200">{{ preview.source }}</div>
        </div>
      </div>

      <div v-if="result?.comparison" class="rounded-lg border border-dark-500 bg-dark-700/40 p-3 text-xs text-gray-300">
        <div class="font-medium text-gray-200">{{ t('tvParity.comparison') }}</div>
        <div class="mt-1">{{ t('tvParity.failuresCount', { count: result.comparison.failures?.length ?? 0 }) }}</div>
      </div>

      <div v-if="artifacts.length" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        <a
          v-for="artifact in artifacts"
          :key="artifact.name"
          :href="artifactHref(artifact)"
          class="rounded-lg border border-dark-500 bg-dark-700/50 px-3 py-2 text-sm text-accent-light hover:border-accent"
          target="_blank"
          rel="noopener"
        >
          {{ artifact.name }}
          <span class="block text-xs text-gray-500">{{ artifact.filename }} · {{ t('tvParity.artifactSize', { bytes: artifact.size_bytes }) }} · {{ artifact.download_url }}</span>
        </a>
      </div>
      <div v-else class="text-sm text-gray-500">{{ t('tvParity.artifactsEmpty') }}</div>
    </section>
  </div>
</template>
