import { describe, expect, it } from 'vitest'

import {
  clearStrategySymbolForMarketChange,
  isCreateDisabled,
  loadStrategySymbolOptions,
  newStrategyForm,
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

  it('blocks Create until the user has picked a Pine source AND an artifact', () => {
    // Empty form -> blocked (both name/symbol and compiled source missing)
    expect(isCreateDisabled(form())).toBe(true)
    // Name+symbol only -> still blocked (no Pine source chosen)
    expect(isCreateDisabled(form({ name: 'Demo', symbol: 'BTCUSDT' }))).toBe(true)
    // Pine source + artifact but no name/symbol -> still blocked
    expect(isCreateDisabled(form({ pine_id: 'src-1', artifact_id: 'art-1' }))).toBe(true)
    // Full form -> enabled
    expect(
      isCreateDisabled(form({ name: 'Demo', symbol: 'BTCUSDT', pine_id: 'src-1', artifact_id: 'art-1' })),
    ).toBe(false)
  })

  it('forces Create to stay disabled while a create request is in flight', () => {
    expect(isCreateDisabled(form({ name: 'Demo', symbol: 'BTCUSDT', pine_id: 'src-1', artifact_id: 'art-1' }), true)).toBe(true)
  })

  it('Create stays disabled when only artifact_id is set (no Pine source picked)', () => {
    // Reproduces the "I created a strategy without picking a Pine" bug:
    // previously autoFillPineSource(true) on submit would fill pine_id
    // from the store and POST a strategy with an arbitrary Pine file.
    expect(isCreateDisabled(form({ name: 'Demo', symbol: 'BTCUSDT', artifact_id: 'art-1' }))).toBe(true)
  })

  it('resets a created strategy form to the configured default timeframe', () => {
    expect(newStrategyForm('3m', 'bybit', 'futures')).toEqual({
      name: '',
      pine_id: '',
      artifact_id: '',
      symbol: '',
      timeframe: '3m',
      exchange: 'bybit',
      market_type: 'futures',
      params_json: '{}',
      mode: 'paper',
    })
  })

  it('returns a safe empty symbol result when backend search fails', async () => {
    const result = await loadStrategySymbolOptions(
      'BTC',
      'coinbase',
      'spot',
      async () => { throw new Error('backend down') },
    )

    expect(result).toEqual({ symbols: [], error: 'backend down' })
  })
})
