import axios from 'axios'
import { apiPath } from './paths'
import type { DataHealthPayload } from '@/lib/dataHealth'
import type { MarketMetadataPayload } from '@/lib/marketMetadata'
import { normalizeMarketSymbolOptions, type MarketSymbolOption, type MarketSymbolSearchPayload } from '@/lib/marketSymbols'
import type { SettingsPayload, SettingsUpdatePayload } from '@/lib/settings'

export type { MarketSymbolOption } from '@/lib/marketSymbols'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.response.use(
  (res) => res,
  (err) => Promise.reject(err),
)

export default api

// Dashboard
export const getDashboard = () => api.get('/dashboard')
export const getSettings = () => api.get<SettingsPayload>('/settings')
export const updateSettings = (data: SettingsUpdatePayload) => api.patch<SettingsPayload>('/settings', data)
export const getDataSummary = () => api.get('/data/summary')
export const getDataHealth = () => api.get<DataHealthPayload>('/data/health')
export const getDataMetadata = () => api.get<MarketMetadataPayload>('/data/metadata')
export const getDataSymbols = (params: { exchange: string; market_type: string; query?: string }) =>
  api.get<MarketSymbolSearchPayload>('/data/symbols', { params })
export const getDataKlines = (params: {
  exchange: string
  market_type: string
  symbol: string
  interval: string
  start_time: number
  end_time: number
  limit?: number
}) => api.get('/data/klines', { params })
export const getDataTicker24h = (params: { exchange: string; market_type: string; symbol: string }) =>
  api.get('/data/ticker24h', { params })
export async function searchMarketSymbols(query: string, exchange: string, marketType: string): Promise<MarketSymbolOption[]> {
  const { data } = await getDataSymbols({ exchange, market_type: marketType, query })
  return normalizeMarketSymbolOptions(data)
}
export const refreshDataSeries = (id: string) => api.post(apiPath('/data/series', id, 'refresh'))
export const backfillDataSeries = (data: { symbol: string; timeframe: string; from_time: string; to_time: string; exchange?: string; market_type?: string }) =>
  api.post('/data/backfill', data)
export const deleteDataSeries = (id: string) => api.delete(apiPath('/data/series', id))
export const deleteDataOrders = (params?: { symbol?: string; strategy_id?: string; status?: string }) =>
  api.delete('/data/orders', { params })

// Pine Files
export const getPineFiles = () => api.get('/pine-sources')
export const getPineFile = (id: string) => api.get(apiPath('/pine-sources', id))
export const createPineFile = (data: { name: string; source_text: string; source_type?: string }) => api.post('/pine-sources', data)
export const compilePineFile = (sourceId: string) => api.post(apiPath('/pine', sourceId, 'compile'))
export const getPineArtifacts = (sourceId: string) => api.get(apiPath('/pine', sourceId, 'artifacts'))
export const previewDeletePineFile = (id: string) => api.get(apiPath('/pine-sources', id, 'delete-preview'))
export const deletePineFile = (id: string) => api.delete(apiPath('/pine-sources', id))

// Strategies
export const getStrategies = () => api.get('/strategies')
export const getStrategy = (id: string) => api.get(apiPath('/strategies', id))
export const createStrategy = (data: any) => api.post('/strategies', data)
export const updateStrategy = (id: string, data: any) => api.patch(apiPath('/strategies', id), data)
export const previewDeleteStrategy = (id: string) => api.get(apiPath('/strategies', id, 'delete-preview'))
export const deleteStrategy = (id: string) => api.delete(apiPath('/strategies', id))
export const controlStrategy = (id: string, action: string) => api.post(apiPath('/strategies', id, 'action'), null, { params: { action } })

// Backtests
export const getBacktests = () => api.get('/backtest/runs')
export const getBacktest = (id: string) => api.get(apiPath('/backtest/runs', id))
export const runBacktest = (data: any) => api.post('/backtest/run', data)
export const estimateBacktest = (params: { strategy_id: string; from_time: string; to_time: string }) =>
  api.get('/backtest/estimate', { params })
export const getBacktestProgress = (id: string) => api.get(apiPath('/backtest/progress', id))
export const getBacktestRuns = (strategyId?: string, limit = 10) =>
  api.get('/backtest/runs', { params: { strategy_id: strategyId, limit } })
export const getBacktestTrades = (runId: string) =>
  api.get(apiPath('/backtest/runs', runId, 'trades'))
export const deleteBacktest = (runId: string) =>
  api.delete(apiPath('/backtest/runs', runId))
export const controlBacktest = (runId: string, action: string) =>
  api.post(apiPath('/backtest/runs', runId, 'action'), null, { params: { action } })

export type TvParityPreviewRequest = {
  candlesFile: File
  exchange?: string
  marketType?: string
  symbol: string
  timeframe: string
}

export type TvParityRunRequest = {
  strategyId: string
  source?: 'tradingview_csv' | 'exchange_data'
  candlesFile?: File | null
  tvChartFile?: File | null
  tvTradesFile?: File | null
  tvEquityFile?: File | null
  fromTime?: string
  toTime?: string
  compareFromTime?: string
  compareToTime?: string
  paramsOverrideJson?: string
  warmupBars?: number
  fullPrehistory?: boolean
  capturePlots?: boolean
  absTol?: number
  relTol?: number
  includeBaseColumns?: boolean
}

const multipartConfig = { headers: { 'Content-Type': 'multipart/form-data' } }

function appendFormValue(form: FormData, key: string, value: unknown) {
  if (value === null || value === undefined || value === '') return
  if (value instanceof File) {
    form.append(key, value)
    return
  }
  form.append(key, String(value))
}

export function previewTvParityCandles(request: TvParityPreviewRequest) {
  const form = new FormData()
  form.append('candles_file', request.candlesFile)
  appendFormValue(form, 'exchange', request.exchange ?? 'binance')
  appendFormValue(form, 'market_type', request.marketType ?? 'spot')
  appendFormValue(form, 'symbol', request.symbol)
  appendFormValue(form, 'timeframe', request.timeframe)
  return api.post('/tv-parity/preview-candles', form, multipartConfig)
}

export function runTvParity(request: TvParityRunRequest) {
  const form = new FormData()
  appendFormValue(form, 'source', request.source ?? 'tradingview_csv')
  appendFormValue(form, 'candles_file', request.candlesFile)
  appendFormValue(form, 'strategy_id', request.strategyId)
  appendFormValue(form, 'tv_chart_file', request.tvChartFile)
  appendFormValue(form, 'tv_trades_file', request.tvTradesFile)
  appendFormValue(form, 'tv_equity_file', request.tvEquityFile)
  appendFormValue(form, 'from_time', request.fromTime)
  appendFormValue(form, 'to_time', request.toTime)
  appendFormValue(form, 'compare_from_time', request.compareFromTime)
  appendFormValue(form, 'compare_to_time', request.compareToTime)
  appendFormValue(form, 'params_override_json', request.paramsOverrideJson)
  appendFormValue(form, 'warmup_bars', request.warmupBars)
  appendFormValue(form, 'full_prehistory', request.fullPrehistory)
  appendFormValue(form, 'capture_plots', request.capturePlots)
  appendFormValue(form, 'abs_tol', request.absTol)
  appendFormValue(form, 'rel_tol', request.relTol)
  appendFormValue(form, 'include_base_columns', request.includeBaseColumns)
  return api.post('/tv-parity/run', form, multipartConfig)
}

export const getTvParityRun = (runId: string) => api.get(apiPath('/tv-parity/runs', runId))
export const tvParityArtifactUrl = (runId: string, artifactName: string) =>
  `/api${apiPath('/tv-parity/runs', runId, 'artifacts', artifactName)}`

// --- Visualization endpoints (1-7) -----------------------------------------

export type TvParityChartDataPoint = {
  kind: 'openpine_equity' | 'tv_equity' | 'openpine_ohlc' | 'tv_ohlc' | 'signal' | 'marker'
  t: number
  v?: number
  o?: number
  h?: number
  l?: number
  c?: number
  [key: string]: unknown
}

export type TvParityChartData = {
  run_id: string
  series: TvParityChartDataPoint[]
  abs_tol: number
  rel_tol: number
  max_abs_delta: number | null
  mismatch_cells: number | null
  tv_equity: [number, number][]
  failures: any[]
  plots: Record<string, unknown>
  trades: Record<string, unknown>
  initial_equity: number | null
  final_equity: number | null
}

export type TvParityTopMismatch = {
  bar_time: number | null
  row_kind: string
  trade_index: number | null
  delta_entry_price: number
  delta_exit_price: number
  delta_qty: number
  delta_net_profit: number
  delta_entry_price_abs: number
  delta_net_profit_abs: number
}

export type TvParitySummaryCards = {
  run_id: string | null
  strategy_id: string | null
  status: string | null
  compare_from: number | null
  compare_to: number | null
  overall_status: 'match' | 'mismatch' | 'failed' | string
  trades_match: boolean
  trades_status: string
  plots_status: string
  equity_status: string
  max_abs_delta: number | null
  max_abs_delta_time_ms: number | null
  max_abs_delta_price: number | null
  mismatch_cells: number | null
  failure_count: number
  failures: any[]
  initial_equity: number | null
  final_equity: number | null
}

export type TvParityDiagnosticsCallout = {
  bar_time: number
  new_closed_trade: 1
  last_closed_profit: number | null
  last_closed_size: number | null
  last_closed_entry_price: number | null
  last_closed_exit_price: number | null
}

export const getTvParityChartData = (runId: string) =>
  api.get<TvParityChartData>(apiPath('/tv-parity/runs', runId, 'chart-data'))
export const getTvParityTopMismatches = (runId: string, limit = 20) =>
  api.get<{ total: number; limit: number; items: TvParityTopMismatch[] }>(
    apiPath('/tv-parity/runs', runId, 'mismatches/top'),
    { params: { limit } },
  )
export const getTvParitySummaryCards = (runId: string) =>
  api.get<TvParitySummaryCards>(apiPath('/tv-parity/runs', runId, 'summary-cards'))
export const getTvParityDiagnosticsCallouts = (runId: string) =>
  api.get<{ run_id: string; chart_path: string | null; callouts: TvParityDiagnosticsCallout[] }>(
    apiPath('/tv-parity/runs', runId, 'diagnostics/callouts'),
  )

export const tvParityReportUrl = (runId: string, kind: 'html' | 'png' | 'zip') =>
  `/api${apiPath('/tv-parity/runs', runId, `report.${kind === 'zip' ? 'zip' : kind}`)}`

export type TvParityHistoryEntry = {
  run_id: string
  strategy_id: string | null
  source: string | null
  status: string | null
  queued_at: number | null
  compare_from: number | null
  compare_to: number | null
  symbol: string | null
  exchange: string | null
  market_type: string | null
  timeframe: string | null
  valid_bars: number | null
  from_time: number | null
  to_time: number | null
  is_demo?: boolean
}

export type TvParityHistoryResponse = {
  items: TvParityHistoryEntry[]
  total: number
  limit: number
  strategy_id: string | null
  source: string | null
  include_demo?: boolean
}

export const listTvParityRuns = (params?: {
  strategy_id?: string
  source?: 'tradingview_csv' | 'exchange_data'
  include_demo?: boolean
  limit?: number
}) => api.get('/tv-parity/runs', { params })

// Orders & Positions
export const getOrders = (strategyId?: string, limit = 100) =>
  api.get('/orders', { params: { strategy_id: strategyId, limit } })
export const getPositions = (strategyId: string) =>
  api.get(apiPath('/positions', strategyId))

// Achievements
export interface AchievementItem {
  id: string
  tier: 'pro' | 'ultra' | 'hyper' | 'apex'
  icon: string
  title: string
  description: string
  metric: string
  target: number
  current: number
  reward: string
  hidden: boolean
  unlocked: boolean
  unlocked_at: number | null
  progress_pct: number
}

export interface AchievementSummary {
  total: number
  unlocked: number
  by_tier: Record<string, { done: number; of: number }>
}

export interface AchievementsResponse {
  summary: AchievementSummary
  items: AchievementItem[]
}

export const getAchievements = (locale: string = 'en', includeHidden = false) =>
  api.get<AchievementsResponse>('/achievements', { params: { locale, include_hidden: includeHidden } })

export const refreshAchievements = () =>
  api.post<AchievementsResponse>('/achievements/refresh')

// Version manifest
export type VersionModule = {
  name: string
  version: string | null
  installed: boolean
  path: string | null
  summary: string | null
}

export type VersionRuntime = {
  python: string | null
  platform: string | null
  machine: string | null
  node: string | null
}

export type VersionManifest = {
  modules: VersionModule[]
  runtime: VersionRuntime
}

export const getVersionManifest = () => api.get<VersionManifest>('/version')
