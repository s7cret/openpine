import { useI18n } from 'vue-i18n'

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

export const EMPTY_MARKET_METADATA: MarketMetadataPayload = {
  source: 'unloaded',
  market_types: [],
  exchanges: [],
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

/**
 * Map backend `disabled_reason` enum (e.g. "data_only", "not_wired") to a
 * locale-aware label. Falls back to a humanised version of the raw token
 * when no mapping is known.
 */
export function exchangeDisabledReasonLabel(t: (key: string) => string, reason: string | null): string {
  if (!reason) return ''
  const key = `marketMeta.${reason}`
  const known = t(key)
  if (known !== key) return known
  return reason.replace(/[_-]+/g, ' ')
}

export function exchangeOptionLabel(t: (key: string) => string, option: ExchangeSelectOption): string {
  if (option.disabled) {
    const reason = exchangeDisabledReasonLabel(t, option.reason) || t('marketMeta.notWired')
    return t('marketMeta.exchangeBadgeDisabled', { name: option.label, reason })
  }
  return t('marketMeta.exchangeBadge', { name: option.label })
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

export function symbolSearchPlaceholder(
  t: (key: string) => string,
  metadata: MarketMetadataPayload | null,
  exchangeId: string,
): string {
  const label = exchangeLabel(metadata, exchangeId)
  if (!canSearchSymbols(metadata, exchangeId)) {
    return t('marketMeta.symbolSearchUnavailable', { exchange: label })
  }
  return t('marketMeta.symbolSearchPlaceholder', { exchange: label })
}

export function symbolLoadingLabel(
  t: (key: string) => string,
  metadata: MarketMetadataPayload | null,
  exchangeId: string,
): string {
  return t('marketMeta.symbolLoading', { exchange: exchangeLabel(metadata, exchangeId) })
}

/**
 * Convenience helper for callers that already have an `useI18n()` instance.
 * Returns a bound version of {@link exchangeOptionLabel} so call sites stay
 * short.
 */
export function makeExchangeOptionLabel(t: (key: string) => string) {
  return (option: ExchangeSelectOption) => exchangeOptionLabel(t, option)
}

/**
 * Convenience helper — `useI18n()` returns `{ t, ... }`; pass it through.
 * Use it in <script setup> for typed access without retyping the binding.
 */
export function useMarketMetaI18n() {
  const { t } = useI18n()
  return {
    t,
    exchangeOptionLabel: makeExchangeOptionLabel(t),
    symbolSearchPlaceholder: (metadata: MarketMetadataPayload | null, exchangeId: string) =>
      symbolSearchPlaceholder(t, metadata, exchangeId),
    symbolLoadingLabel: (metadata: MarketMetadataPayload | null, exchangeId: string) =>
      symbolLoadingLabel(t, metadata, exchangeId),
    exchangeDisabledReasonLabel: (reason: string | null) => exchangeDisabledReasonLabel(t, reason),
  }
}
