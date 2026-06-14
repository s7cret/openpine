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
  contractType: string | null
  label: string
}

export function marketSymbolLabel(item: Pick<MarketSymbolOption, 'symbol' | 'baseAsset' | 'quoteAsset' | 'contractType'>): string {
  const pair = `${item.baseAsset}/${item.quoteAsset}`
  return item.contractType ? `${item.symbol} · ${pair} · ${item.contractType}` : `${item.symbol} · ${pair}`
}

export function normalizeMarketSymbolOptions(payload: MarketSymbolSearchPayload): MarketSymbolOption[] {
  return (payload.symbols ?? []).map((item) => {
    const option = {
      symbol: String(item.symbol ?? '').toUpperCase(),
      baseAsset: String(item.base_asset ?? '').toUpperCase(),
      quoteAsset: String(item.quote_asset ?? '').toUpperCase(),
      exchange: String(item.exchange ?? payload.exchange ?? '').toLowerCase(),
      market: String(item.market ?? payload.market_type ?? '').toLowerCase(),
      contractType: item.contract_type ? String(item.contract_type).toLowerCase() : null,
    }
    return { ...option, label: marketSymbolLabel(option) }
  }).filter((item) => item.symbol && item.baseAsset && item.quoteAsset)
}
