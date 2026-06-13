import { describe, expect, it } from 'vitest'

import {
  clearStrategySymbolForMarketChange,
  selectStrategySymbol,
  strategyValidationMessage,
  type StrategyFormDraft,
} from './strategyForm'

function form(overrides: Partial<StrategyFormDraft> = {}): StrategyFormDraft {
  return {
    name: '',
    pine_id: '',
    artifact_id: '',
    symbol: '',
    timeframe: '1h',
    exchange: 'binance',
    market_type: 'spot',
    params_json: '{}',
    mode: 'paper',
    ...overrides,
  }
}

describe('strategy form helpers', () => {
  it('keeps the selected autocomplete symbol visible and submitted', () => {
    const draft = form()

    const visibleSearch = selectStrategySymbol(draft, { symbol: 'BTCUSDT', baseAsset: 'BTC', quoteAsset: 'USDT' })

    expect(draft.symbol).toBe('BTCUSDT')
    expect(visibleSearch).toBe('BTCUSDT')
  })

  it('clears stale symbols when market type changes', () => {
    const draft = form({ symbol: 'BTCUSDT' })

    const visibleSearch = clearStrategySymbolForMarketChange(draft)

    expect(draft.symbol).toBe('')
    expect(visibleSearch).toBe('')
  })

  it('reports missing compiled source separately from name and symbol', () => {
    expect(strategyValidationMessage(form({ name: 'Demo', symbol: 'BTCUSDT' }))).toBe('❌ Select a compiled Pine source before creating the strategy.')
    expect(strategyValidationMessage(form({ pine_id: 'src-1', artifact_id: 'art-1' }))).toBe('❌ Fill required fields: name and symbol.')
  })
})
