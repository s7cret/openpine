import { describe, expect, it } from 'vitest'

import { normalizeMarketSymbolOptions } from './marketSymbols'

describe('market symbol options', () => {
  it('maps backend symbol payload into UI option shape and keeps stable quote metadata', () => {
    expect(normalizeMarketSymbolOptions({
      symbols: [
        { symbol: 'BTCUSDC', base_asset: 'BTC', quote_asset: 'USDC', exchange: 'bybit', market: 'spot' },
        { symbol: 'ETHBTC', base_asset: 'ETH', quote_asset: 'BTC', exchange: 'bybit', market: 'spot' },
      ],
    })).toEqual([
      { symbol: 'BTCUSDC', baseAsset: 'BTC', quoteAsset: 'USDC', exchange: 'bybit', market: 'spot' },
      { symbol: 'ETHBTC', baseAsset: 'ETH', quoteAsset: 'BTC', exchange: 'bybit', market: 'spot' },
    ])
  })
})
