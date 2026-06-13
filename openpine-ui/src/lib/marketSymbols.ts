export type MarketSymbolPayloadItem = {
  exchange: string
  market: string
  symbol: string
  base_asset: string
  quote_asset: string
  active?: boolean
  contract_type?: string | null
}

export type MarketSymbolSearchPayload = {
  exchange?: string
  market_type?: string
  query?: string
  stable_quotes_only?: boolean
  stable_quote_assets?: string[]
  symbols?: MarketSymbolPayloadItem[]
}

export type MarketSymbolOption = {
  symbol: string
  baseAsset: string
  quoteAsset: string
  exchange: string
  market: string
}

export function normalizeMarketSymbolOptions(payload: MarketSymbolSearchPayload): MarketSymbolOption[] {
  return (payload.symbols ?? []).map((item) => ({
    symbol: String(item.symbol ?? '').toUpperCase(),
    baseAsset: String(item.base_asset ?? '').toUpperCase(),
    quoteAsset: String(item.quote_asset ?? '').toUpperCase(),
    exchange: String(item.exchange ?? payload.exchange ?? '').toLowerCase(),
    market: String(item.market ?? payload.market_type ?? '').toLowerCase(),
  })).filter((item) => item.symbol && item.baseAsset && item.quoteAsset)
}
