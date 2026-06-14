import { describe, expect, it } from 'vitest'

import { normalizeMarketSymbolOptions } from './marketSymbols'

describe('market symbol options', () => {
  it('maps backend symbol payload into UI option shape and keeps stable quote metadata', () => {
    expect(normalizeMarketSymbolOptions({
      symbols: [
        { symbol: 'BTCUSDC', base_asset: 'BTC', quote_asset: 'USDC', exchange: 'bybit', market: 'spot' },
        { symbol: 'BTC-USDT-SWAP', base_asset: 'BTC', quote_asset: 'USDT', exchange: 'okx', market: 'futures', contract_type: 'linear' },
      ],
    })).toEqual([
      { symbol: 'BTCUSDC', baseAsset: 'BTC', quoteAsset: 'USDC', exchange: 'bybit', market: 'spot', contractType: null, label: 'BTCUSDC · BTC/USDC' },
      { symbol: 'BTC-USDT-SWAP', baseAsset: 'BTC', quoteAsset: 'USDT', exchange: 'okx', market: 'futures', contractType: 'linear', label: 'BTC-USDT-SWAP · BTC/USDT · linear' },
    ])
  })
})
