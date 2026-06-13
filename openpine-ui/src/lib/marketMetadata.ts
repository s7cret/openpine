export type MarketTypeMetadata = {
  id: string
  label: string
  aliases: string[]
  description: string
  enabled_for_strategy_create: boolean
}

export type ExchangeMetadata = {
  id: string
  name: string
  rank: number
  status: string
  native_adapter: boolean
  openpine_enabled: boolean
  symbol_search_supported: boolean
  disabled_reason: string | null
  market_types: MarketTypeMetadata[]
}

export type MarketMetadataPayload = {
  source: string
  market_types: MarketTypeMetadata[]
  exchanges: ExchangeMetadata[]
}

export type ExchangeSelectOption = {
  id: string
  label: string
  disabled: boolean
  reason: string | null
}

export type MarketTypeSelectOption = {
  id: string
  label: string
  disabled: boolean
}

export function exchangeSelectOptions(metadata: MarketMetadataPayload | null): ExchangeSelectOption[] {
  return [...(metadata?.exchanges ?? [])]
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
    .map((exchange) => ({
      id: exchange.id,
      label: exchange.name,
      disabled: !exchange.openpine_enabled,
      reason: exchange.disabled_reason ?? null,
    }))
}

export function exchangeById(metadata: MarketMetadataPayload | null, exchangeId: string): ExchangeMetadata | null {
  return metadata?.exchanges.find((exchange) => exchange.id === exchangeId) ?? null
}

export function exchangeDisabledReasonLabel(reason: string | null): string | null {
  if (!reason) return null
  return reason.replace(/[_-]+/g, ' ')
}

export function exchangeOptionLabel(option: ExchangeSelectOption): string {
  const reason = exchangeDisabledReasonLabel(option.reason)
  return `◆ ${option.label}${option.disabled ? ` — ${reason ?? 'not wired'}` : ''}`
}

export function marketTypeOptionsForExchange(
  metadata: MarketMetadataPayload | null,
  exchangeId: string,
): MarketTypeSelectOption[] {
  const exchange = exchangeById(metadata, exchangeId)
  return (exchange?.market_types ?? []).map((marketType) => ({
    id: marketType.id,
    label: marketType.label,
    disabled: !marketType.enabled_for_strategy_create,
  }))
}

export function defaultMarketTypeForExchange(
  metadata: MarketMetadataPayload | null,
  exchangeId: string,
  currentMarketType: string,
): string {
  const exchange = exchangeById(metadata, exchangeId)
  const marketTypes = exchange?.market_types ?? []
  const current = marketTypes.find((marketType) => marketType.id === currentMarketType)
  if (current?.enabled_for_strategy_create) return current.id
  return marketTypes.find((marketType) => marketType.enabled_for_strategy_create)?.id ?? marketTypes[0]?.id ?? 'spot'
}

export function canSearchSymbols(metadata: MarketMetadataPayload | null, exchangeId: string): boolean {
  const exchange = exchangeById(metadata, exchangeId)
  return Boolean(exchange?.openpine_enabled && exchange?.symbol_search_supported)
}

export function exchangeLabel(metadata: MarketMetadataPayload | null, exchangeId: string): string {
  return exchangeById(metadata, exchangeId)?.name ?? exchangeId
}

export function symbolSearchPlaceholder(metadata: MarketMetadataPayload | null, exchangeId: string): string {
  const label = exchangeLabel(metadata, exchangeId)
  if (!canSearchSymbols(metadata, exchangeId)) return `Ticker search unavailable for ${label}`
  return `Search stable pair on ${label}...`
}

export function symbolLoadingLabel(metadata: MarketMetadataPayload | null, exchangeId: string): string {
  return `Loading from ${exchangeLabel(metadata, exchangeId)}...`
}
