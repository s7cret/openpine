import { describe, expect, it } from 'vitest'

import { apiPath, queryParams } from './paths'

describe('api path builders', () => {
  it('encodes path segments instead of interpolating raw ids', () => {
    expect(apiPath('/pine', 'source/id?', 'artifacts', 'artifact#1')).toBe('/pine/source%2Fid%3F/artifacts/artifact%231')
  })

  it('omits nullish query params and encodes values', () => {
    expect(queryParams({ action: 'start now', strategy_id: undefined, status: null, symbol: 'BTC/USDT' })).toBe('action=start+now&symbol=BTC%2FUSDT')
  })
})
