import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import api, { getDataKlines, getDataTicker24h } from './client'

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

  it('does not leave strategy/chart components coupled to browser-side Binance REST', () => {
    const chartSource = readFileSync(resolve(srcRoot, 'components/CandleChart.vue'), 'utf8')
    const strategiesSource = readFileSync(resolve(srcRoot, 'pages/Strategies.vue'), 'utf8')

    expect(chartSource).not.toContain('@/api/binance')
    expect(chartSource).not.toContain('binanceKlinesRequestUrl')
    expect(strategiesSource).not.toContain('@/api/binance')
    expect(strategiesSource).not.toContain('binanceTicker24hUrl')
  })
})
