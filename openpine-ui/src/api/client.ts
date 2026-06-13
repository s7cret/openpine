import axios from 'axios'
import { apiPath } from './paths'
import type { MarketMetadataPayload } from '@/lib/marketMetadata'
import { normalizeMarketSymbolOptions, type MarketSymbolOption, type MarketSymbolSearchPayload } from '@/lib/marketSymbols'

export type { MarketSymbolOption } from '@/lib/marketSymbols'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error('[API]', err.response?.status, err.message, err.response?.data)
    return Promise.reject(err)
  }
)

export default api

// Dashboard
export const getDashboard = () => api.get('/dashboard')
export const getDataSummary = () => api.get('/data/summary')
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
  try {
    const { data } = await getDataSymbols({ exchange, market_type: marketType, query })
    return normalizeMarketSymbolOptions(data)
  } catch (e) {
    console.error('Market symbols fetch failed', e)
    return []
  }
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

// Orders & Positions
export const getOrders = (strategyId?: string, limit = 100) =>
  api.get('/orders', { params: { strategy_id: strategyId, limit } })
export const getPositions = (strategyId: string) =>
  api.get(apiPath('/positions', strategyId))
