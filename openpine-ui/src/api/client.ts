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

// Orders & Positions
export const getOrders = (strategyId?: string, limit = 100) =>
  api.get('/orders', { params: { strategy_id: strategyId, limit } })
export const getPositions = (strategyId: string) =>
  api.get(apiPath('/positions', strategyId))
