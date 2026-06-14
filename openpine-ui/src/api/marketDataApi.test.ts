import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import api, { getDataKlines, getDataTicker24h, searchMarketSymbols } from './client'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..')

describe('provider-backed market data API', () => {
  it('routes chart klines through the OpenPine gateway', () => {
    const spy = vi.spyOn(api, 'get').mockReturnValue({} as any)

    getDataKlines({
      exchange: 'bybit',
      market_type: 'delivery',
      symbol: 'BTCUSD',
      interval: '1h',
      start_time: 1,
      end_time: 2,
      limit: 200000,
    })

    expect(spy).toHaveBeenCalledWith('/data/klines', {
      params: {
        exchange: 'bybit',
        market_type: 'delivery',
        symbol: 'BTCUSD',
        interval: '1h',
        start_time: 1,
        end_time: 2,
        limit: 200000,
      },
    })
    spy.mockRestore()
  })

  it('routes 24h ticker stats through the OpenPine gateway', () => {
    const spy = vi.spyOn(api, 'get').mockReturnValue({} as any)

    getDataTicker24h({ exchange: 'bybit', market_type: 'delivery', symbol: 'BTCUSD' })

    expect(spy).toHaveBeenCalledWith('/data/ticker24h', {
      params: { exchange: 'bybit', market_type: 'delivery', symbol: 'BTCUSD' },
    })
    spy.mockRestore()
  })

  it('surfaces symbol discovery failures instead of returning a fake empty list', async () => {
    const err = new Error('symbol backend offline')
    const spy = vi.spyOn(api, 'get').mockRejectedValue(err)

    await expect(searchMarketSymbols('BTC', 'bybit', 'futures')).rejects.toThrow('symbol backend offline')

    spy.mockRestore()
  })

  it('does not leave strategy/chart components coupled to browser-side Binance REST', () => {
    const chartSource = readFileSync(resolve(srcRoot, 'components/CandleChart.vue'), 'utf8')
    const strategiesSource = readFileSync(resolve(srcRoot, 'pages/Strategies.vue'), 'utf8')

    expect(chartSource).not.toContain('@/api/binance')
    expect(chartSource).not.toContain('binanceKlinesRequestUrl')
    expect(strategiesSource).not.toContain('@/api/binance')
    expect(strategiesSource).not.toContain('binanceTicker24hUrl')
  })

  it('does not ship fake market metadata fallbacks or page-level console logging', () => {
    const checkedFiles = [
      'api/client.ts',
      'pages/Strategies.vue',
      'pages/TvParity.vue',
      'stores/strategies.ts',
      'stores/dashboard.ts',
    ]

    for (const file of checkedFiles) {
      const source = readFileSync(resolve(srcRoot, file), 'utf8')
      expect(source, file).not.toContain("source: 'fallback'")
      expect(source, file).not.toContain('fallbackMarketMetadata')
      expect(source, file).not.toContain('console.error')
    }
  })
})
