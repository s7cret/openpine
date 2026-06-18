import { describe, expect, it } from 'vitest'

import { dateInputFromMs, rangesOverlap, toTradeTimeMs, tradeBoundsMs } from './tradeChartRange'

describe('trade chart range helpers', () => {
  it('normalizes seconds, milliseconds, and ISO strings to milliseconds', () => {
    expect(toTradeTimeMs(1616976000)).toBe(1616976000000)
    expect(toTradeTimeMs(1616976000000)).toBe(1616976000000)
    expect(toTradeTimeMs('2021-03-29T00:00:00Z')).toBe(1616976000000)
  })

  it('derives a candle loading range from entry and exit times', () => {
    const day = 24 * 60 * 60 * 1000
    const bounds = tradeBoundsMs([
      { entry_time: 1616976000000, exit_time: 1617062400000 },
      { entry_time: 1760745600000, exit_time: 1761091200000 },
    ], day)

    expect(bounds).toEqual({
      fromMs: 1616889600000,
      toMs: 1761177600000,
    })
    expect(dateInputFromMs(bounds!.fromMs)).toBe('2021-03-28')
    expect(dateInputFromMs(bounds!.toMs)).toBe('2025-10-23')
  })

  it('detects whether current chart range already overlaps trades', () => {
    expect(rangesOverlap(
      { fromMs: 1000, toMs: 2000 },
      { fromMs: 1500, toMs: 3000 },
    )).toBe(true)
    expect(rangesOverlap(
      { fromMs: 1000, toMs: 2000 },
      { fromMs: 2500, toMs: 3000 },
    )).toBe(false)
  })
})
