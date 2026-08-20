import { describe, expect, it } from 'vitest'
import {
  mtfSeriesValidationKey,
  newMtfSeriesRow,
  toMtfSeriesRequests,
} from './mtfSeries'

describe('explicit MTF series admission', () => {
  it('creates an empty row without silent symbol or timeframe defaults', () => {
    expect(newMtfSeriesRow()).toMatchObject({ symbol: '', timeframe: '' })
  })

  it('requires complete unique rows and serializes explicit values', () => {
    expect(mtfSeriesValidationKey([{ id: 1, symbol: 'BTCUSDT', timeframe: '' }])).toBe(
      'mtf.required',
    )
    expect(
      mtfSeriesValidationKey([
        { id: 1, symbol: 'btcusdt', timeframe: '1D' },
        { id: 2, symbol: 'BTCUSDT', timeframe: '1d' },
      ]),
    ).toBe('mtf.duplicate')
    expect(
      toMtfSeriesRequests([
        { id: 1, symbol: ' btcusdt ', timeframe: '1D' },
        { id: 2, symbol: 'ethusdt', timeframe: '4h' },
      ]),
    ).toEqual([
      { symbol: 'BTCUSDT', timeframe: '1D' },
      { symbol: 'ETHUSDT', timeframe: '4h' },
    ])
  })
})
