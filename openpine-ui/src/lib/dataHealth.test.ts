import { describe, expect, it } from 'vitest'

import { summarizeDataHealth, type DataHealthPayload } from './dataHealth'

const health: DataHealthPayload = {
  source: 'test',
  generated_at: 1,
  settings: {
    timeframes: ['1m', '3m'],
    default_timeframe: '3m',
    stable_quotes_only: true,
    stable_quote_assets: ['USDT', 'USDC'],
  },
  totals: {
    exchanges: 10,
    enabled_exchanges: 10,
    market_types: 35,
    cached_series: 3,
    cached_exchanges: 2,
    cached_markets: 3,
    actual_series: 2,
    stale_series: 1,
  },
  exchanges: [
    {
      id: 'binance',
      name: 'Binance',
      rank: 1,
      enabled: true,
      status: 'stale',
      cached_series: 2,
      markets: [
        { id: 'spot', label: 'Spot', enabled: true, status: 'actual', cached_series: 1, symbols: ['BTCUSDT'], timeframes: ['1m'] },
        { id: 'futures', label: 'Futures', enabled: true, status: 'stale', cached_series: 1, symbols: ['BTCUSDT'], timeframes: ['1h'] },
      ],
    },
    {
      id: 'bybit',
      name: 'Bybit',
      rank: 2,
      enabled: true,
      status: 'available',
      cached_series: 0,
      markets: [
        { id: 'spot', label: 'Spot', enabled: true, status: 'available', cached_series: 0, symbols: [], timeframes: [] },
      ],
    },
  ],
}

describe('data health helpers', () => {
  it('summarizes cached versus available exchanges without pretending uncached venues are broken', () => {
    expect(summarizeDataHealth(health)).toEqual({
      exchangeLabel: '10 native / 2 cached',
      cacheLabel: '3 series · 2 actual · 1 stale',
      defaultTimeframe: '3m',
      stableQuotesLabel: 'Stable quotes: USDT, USDC',
      degradedExchanges: ['Binance'],
    })
  })
})
