export type DataHealthMarket = {
  id: string
  label: string
  enabled: boolean
  status: 'available' | 'actual' | 'stale' | 'cached' | 'disabled' | string
  cached_series: number
  actual_series?: number
  stale_series?: number
  symbols: string[]
  timeframes: string[]
}

export type DataHealthExchange = {
  id: string
  name: string
  rank: number
  enabled: boolean
  status: 'available' | 'actual' | 'stale' | 'cached' | 'disabled' | string
  cached_series: number
  actual_series?: number
  stale_series?: number
  markets: DataHealthMarket[]
}

export type DataHealthPayload = {
  source: string
  generated_at: number
  settings: {
    timeframes: string[]
    default_timeframe: string
    stable_quotes_only: boolean
    stable_quote_assets: string[]
  }
  totals: {
    exchanges: number
    enabled_exchanges: number
    market_types: number
    cached_series: number
    cached_exchanges: number
    cached_markets: number
    actual_series: number
    stale_series: number
  }
  exchanges: DataHealthExchange[]
}

export function summarizeDataHealth(payload: DataHealthPayload) {
  const cached = payload.totals.cached_exchanges
  const degradedExchanges = payload.exchanges
    .filter((item) => item.status === 'stale')
    .map((item) => item.name)
  return {
    exchangeLabel: `${payload.totals.enabled_exchanges} native / ${cached} cached`,
    cacheLabel: `${payload.totals.cached_series} series · ${payload.totals.actual_series} actual · ${payload.totals.stale_series} stale`,
    defaultTimeframe: payload.settings.default_timeframe,
    stableQuotesLabel: payload.settings.stable_quotes_only
      ? `Stable quotes: ${payload.settings.stable_quote_assets.join(', ')}`
      : 'Stable quote filter: off',
    degradedExchanges,
  }
}

export function healthStatusClass(status: string): string {
  if (status === 'actual') return 'bg-green-500/15 text-green-300 border-green-500/30'
  if (status === 'stale') return 'bg-yellow-500/15 text-yellow-300 border-yellow-500/30'
  if (status === 'disabled') return 'bg-gray-500/10 text-gray-500 border-gray-600/40'
  if (status === 'cached') return 'bg-blue-500/15 text-blue-300 border-blue-500/30'
  return 'bg-dark-600 text-gray-400 border-dark-500'
}
