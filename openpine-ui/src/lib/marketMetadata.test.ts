import { describe, expect, it } from 'vitest'

import {
  canSearchSymbols,
  defaultMarketTypeForExchange,
  exchangeOptionLabel,
  exchangeSelectOptions,
  marketTypeOptionsForExchange,
  symbolLoadingLabel,
  symbolSearchPlaceholder,
  type MarketMetadataPayload,
} from './marketMetadata'

const metadata: MarketMetadataPayload = {
  source: 'test',
  market_types: [],
  exchanges: [
    {
      id: 'binance',
      name: 'Binance',
      rank: 1,
      status: 'native',
      native_adapter: true,
      openpine_enabled: true,
      symbol_search_supported: true,
      disabled_reason: null,
      market_types: [
        { id: 'spot', label: 'Spot', aliases: ['spot'], description: '', enabled_for_strategy_create: true },
        { id: 'futures', label: 'Futures', aliases: ['futures'], description: '', enabled_for_strategy_create: true },
        { id: 'options', label: 'Options', aliases: ['options'], description: '', enabled_for_strategy_create: false },
      ],
    },
    {
      id: 'bybit',
      name: 'Bybit',
      rank: 2,
      status: 'native',
      native_adapter: true,
      openpine_enabled: true,
      symbol_search_supported: true,
      disabled_reason: null,
      market_types: [
        { id: 'spot', label: 'Spot', aliases: ['spot'], description: '', enabled_for_strategy_create: true },
        { id: 'futures', label: 'Futures', aliases: ['futures'], description: '', enabled_for_strategy_create: true },
      ],
    },
    {
      id: 'okx',
      name: 'OKX',
      rank: 3,
      status: 'planned',
      native_adapter: false,
      openpine_enabled: false,
      symbol_search_supported: false,
      disabled_reason: 'planned_provider',
      market_types: [
        { id: 'spot', label: 'Spot', aliases: ['spot'], description: '', enabled_for_strategy_create: false },
      ],
    },
  ],
}

describe('market metadata UI helpers', () => {
  it('keeps listed exchanges and enables native adapters wired into OpenPine gateway', () => {
    expect(exchangeSelectOptions(metadata)).toEqual([
      { id: 'binance', label: 'Binance', disabled: false, reason: null },
      { id: 'bybit', label: 'Bybit', disabled: false, reason: null },
      { id: 'okx', label: 'OKX', disabled: true, reason: 'planned_provider' },
    ])
  })

  it('returns exchange-specific market types including disabled options products', () => {
    expect(marketTypeOptionsForExchange(metadata, 'binance')).toEqual([
      { id: 'spot', label: 'Spot', disabled: false },
      { id: 'futures', label: 'Futures', disabled: false },
      { id: 'options', label: 'Options', disabled: true },
    ])
  })

  it('resets selected market type if the exchange does not support it for strategy create', () => {
    expect(defaultMarketTypeForExchange(metadata, 'bybit', 'delivery')).toBe('spot')
    expect(defaultMarketTypeForExchange(metadata, 'binance', 'futures')).toBe('futures')
    expect(defaultMarketTypeForExchange(metadata, 'binance', 'options')).toBe('spot')
  })

  it('only enables symbol search for exchange metadata marked searchable', () => {
    expect(canSearchSymbols(metadata, 'binance')).toBe(true)
    expect(canSearchSymbols(metadata, 'bybit')).toBe(true)
    expect(symbolSearchPlaceholder(metadata, 'binance')).toBe('Search stable pair on Binance...')
    expect(symbolSearchPlaceholder(metadata, 'bybit')).toBe('Search stable pair on Bybit...')
    expect(symbolLoadingLabel(metadata, 'bybit')).toBe('Loading from Bybit...')
    expect(symbolLoadingLabel(metadata, 'binance')).toBe('Loading from Binance...')
  })

  it('renders disabled exchange labels from backend metadata reasons instead of hardcoded not-wired copy', () => {
    const okx = exchangeSelectOptions(metadata).find((option) => option.id === 'okx')

    expect(exchangeOptionLabel(okx!)).toBe('◆ OKX — planned provider')
    expect(exchangeOptionLabel(exchangeSelectOptions(metadata)[1])).toBe('◆ Bybit')
  })
})
