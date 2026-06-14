import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'

import api, {
  getTvParityRun,
  previewTvParityCandles,
  runTvParity,
  tvParityArtifactUrl,
} from './client'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..')

function csvFile(name = 'candles.csv') {
  return new File(['time,open,high,low,close\n1,1,1,1,1\n'], name, { type: 'text/csv' })
}

describe('TV Parity API and UI contracts', () => {
  it('previews TradingView candle CSVs through multipart upload', () => {
    const spy = vi.spyOn(api, 'post').mockReturnValue({} as any)

    previewTvParityCandles({
      candlesFile: csvFile(),
      exchange: 'binance',
      marketType: 'spot',
      symbol: 'BTCUSDT',
      timeframe: '1m',
    })

    expect(spy).toHaveBeenCalledWith(
      '/tv-parity/preview-candles',
      expect.any(FormData),
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } }),
    )
    const form = spy.mock.calls[0][1] as FormData
    expect(form.get('candles_file')).toBeInstanceOf(File)
    expect(form.get('market_type')).toBe('spot')
    expect(form.get('symbol')).toBe('BTCUSDT')
    spy.mockRestore()
  })

  it('queues TV parity runs with candles, optional TV exports, tolerances, and locked period', () => {
    const spy = vi.spyOn(api, 'post').mockReturnValue({} as any)

    runTvParity({
      strategyId: 'strat_1',
      source: 'tradingview_csv',
      candlesFile: csvFile(),
      tvChartFile: csvFile('chart.csv'),
      tvTradesFile: csvFile('trades.csv'),
      tvEquityFile: csvFile('equity.csv'),
      compareFromTime: '2024-01-01',
      compareToTime: '2024-02-01',
      capturePlots: true,
      warmupBars: 50,
      absTol: 0.01,
      relTol: 0.001,
    })

    expect(spy).toHaveBeenCalledWith(
      '/tv-parity/run',
      expect.any(FormData),
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } }),
    )
    const form = spy.mock.calls[0][1] as FormData
    expect(form.get('source')).toBe('tradingview_csv')
    expect(form.get('candles_file')).toBeInstanceOf(File)
    expect(form.get('strategy_id')).toBe('strat_1')
    expect(form.get('tv_chart_file')).toBeInstanceOf(File)
    expect(form.get('tv_trades_file')).toBeInstanceOf(File)
    expect(form.get('tv_equity_file')).toBeInstanceOf(File)
    expect(form.get('compare_from_time')).toBe('2024-01-01')
    expect(form.get('capture_plots')).toBe('true')
    expect(form.get('warmup_bars')).toBe('50')
    expect(form.get('abs_tol')).toBe('0.01')
    spy.mockRestore()
  })

  it('queues exchange-data TV parity runs with full pre-history flag and no candle upload', () => {
    const spy = vi.spyOn(api, 'post').mockReturnValue({} as any)

    runTvParity({
      strategyId: 'strat_1',
      source: 'exchange_data',
      fromTime: '2023-01-01',
      compareFromTime: '2024-01-01',
      compareToTime: '2024-02-01',
      fullPrehistory: true,
      warmupBars: 0,
    })

    const form = spy.mock.calls[0][1] as FormData
    expect(form.get('source')).toBe('exchange_data')
    expect(form.get('candles_file')).toBeNull()
    expect(form.get('from_time')).toBe('2023-01-01')
    expect(form.get('compare_from_time')).toBe('2024-01-01')
    expect(form.get('full_prehistory')).toBe('true')
    spy.mockRestore()
  })

  it('loads TV parity run metadata and constructs safe artifact download URLs', () => {
    const spy = vi.spyOn(api, 'get').mockReturnValue({} as any)

    getTvParityRun('run_1')

    expect(spy).toHaveBeenCalledWith('/tv-parity/runs/run_1')
    expect(tvParityArtifactUrl('run 1/unsafe', 'comparison_json')).toBe(
      '/api/tv-parity/runs/run%201%2Funsafe/artifacts/comparison_json',
    )
    spy.mockRestore()
  })

  it('registers TV Parity Lab route and nav before Data', () => {
    const routerSource = readFileSync(resolve(srcRoot, 'router/index.ts'), 'utf8')
    const layoutSource = readFileSync(resolve(srcRoot, 'layouts/AppLayout.vue'), 'utf8')
    const pageSource = readFileSync(resolve(srcRoot, 'pages/TvParity.vue'), 'utf8')

    const routeIndex = routerSource.indexOf("path: '/tv-parity'")
    const dataRouteIndex = routerSource.indexOf("path: '/data'")
    const navIndex = layoutSource.indexOf("path: '/tv-parity'")
    const dataNavIndex = layoutSource.indexOf("path: '/data'")

    expect(routeIndex).toBeGreaterThan(-1)
    expect(dataRouteIndex).toBeGreaterThan(routeIndex)
    expect(navIndex).toBeGreaterThan(-1)
    expect(dataNavIndex).toBeGreaterThan(navIndex)
    expect(pageSource).toContain('previewTvParityCandles')
    expect(pageSource).toContain('runTvParity')
    expect(pageSource).toContain('lockedPeriod')
    expect(pageSource).toContain('artifact.download_url')
  })

  it('keeps TV parity inputs app-controlled instead of browser-locale editable controls', () => {
    const pageSource = readFileSync(resolve(srcRoot, 'pages/TvParity.vue'), 'utf8')

    expect(pageSource).not.toContain('<input v-model="form.exchange"')
    expect(pageSource).not.toContain('<input v-model="form.marketType"')
    expect(pageSource).not.toContain('<input v-model="form.symbol"')
    expect(pageSource).not.toContain('<input v-model="form.timeframe"')
    expect(pageSource).not.toContain('<select v-model="form.exchange"')
    expect(pageSource).not.toContain('<select v-model="form.marketType"')
    expect(pageSource).toContain('selectedStrategyMarketContext')
    expect(pageSource).toContain('Strategy market context')
    expect(pageSource).toContain('Choose file')
    expect(pageSource).toContain('No file selected')
    expect(pageSource).toContain('class="hidden"')
    expect(pageSource).toContain("source: 'tradingview_csv'")
    expect(pageSource).toContain("value=\"exchange_data\"")
    expect(pageSource).toContain('form.fullPrehistory')
    expect(pageSource).toContain('Full pre-history')
  })
})
