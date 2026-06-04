import axios from 'axios'

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

// Pine Files
export const getPineFiles = () => api.get('/pine-sources')
export const getPineFile = (id: string) => api.get(`/pine-sources/${id}`)
export const createPineFile = (data: { name: string; source_text: string; source_type?: string }) => api.post('/pine-sources', data)
export const compilePineFile = (sourceId: string) => api.post(`/pine/${sourceId}/compile`)
export const getPineArtifacts = (sourceId: string) => api.get(`/pine/${sourceId}/artifacts`)
export const deletePineFile = (id: string) => api.delete(`/pine-sources/${id}`)

// Strategies
export const getStrategies = () => api.get('/strategies')
export const getStrategy = (id: string) => api.get(`/strategies/${id}`)
export const createStrategy = (data: any) => api.post('/strategies', data)
export const updateStrategy = (id: string, data: any) => api.patch(`/strategies/${id}`, data)
export const deleteStrategy = (id: string) => api.delete(`/strategies/${id}`)
export const controlStrategy = (id: string, action: string) => api.post(`/strategies/${id}/action?action=${action}`)

// Backtests
export const getBacktests = () => api.get('/backtest/runs')
export const getBacktest = (id: string) => api.get(`/backtest/runs/${id}`)
export const runBacktest = (data: any) => api.post('/backtest/run', data)
export const estimateBacktest = (params: { strategy_id: string; from_time: string; to_time: string }) =>
  api.get('/backtest/estimate', { params })
export const getBacktestProgress = (id: string) => api.get(`/backtest/progress/${id}`)
export const getBacktestRuns = (strategyId?: string, limit = 10) =>
  api.get('/backtest/runs', { params: { strategy_id: strategyId, limit } })
export const getBacktestTrades = (runId: string) =>
  api.get(`/backtest/runs/${runId}/trades`)
export const deleteBacktest = (runId: string) =>
  api.delete(`/backtest/runs/${runId}`)

// Orders & Positions
export const getOrders = (strategyId?: string, limit = 100) =>
  api.get('/orders', { params: { strategy_id: strategyId, limit } })
export const getPositions = (strategyId: string) =>
  api.get(`/positions/${strategyId}`)

// Binance ticker search (direct, no proxy)
export async function searchBinanceSymbols(query: string, market: string = 'spot'): Promise<string[]> {
  try {
    const endpoint = market === 'futures' || market === 'delivery'
      ? 'https://fapi.binance.com/fapi/v1/exchangeInfo'
      : 'https://api.binance.com/api/v3/exchangeInfo'
    const { data } = await axios.get(endpoint, { timeout: 10000 })
    const symbols = (data.symbols ?? [])
      .filter((s: any) => s.status === 'TRADING')
      .map((s: any) => s.symbol)
      .filter((s: string) => s.toLowerCase().includes(query.toLowerCase()))
    return symbols.slice(0, 50)
  } catch (e) {
    console.error('Binance symbols fetch failed', e)
    return []
  }
}
