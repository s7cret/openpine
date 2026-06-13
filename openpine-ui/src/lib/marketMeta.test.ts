import { describe, expect, it } from 'vitest'

import { baseAssetFromSymbol } from './marketMeta'

describe('market metadata helpers', () => {
  it('extracts base asset from spot, futures, and COIN-M delivery symbols', () => {
    expect(baseAssetFromSymbol('BTCUSDT')).toBe('BTC')
    expect(baseAssetFromSymbol('ETHUSDC')).toBe('ETH')
    expect(baseAssetFromSymbol('BTCUSD_PERP')).toBe('BTC')
    expect(baseAssetFromSymbol('ETHUSD_240927')).toBe('ETH')
  })
})
