export type SettingsPayload = {
  timezone: string
  timezone_label: string
  marketdata: {
    stable_quotes_only: boolean
    stable_quote_assets: string[]
    symbol_search_limit: number
    timeframes: string[]
    default_timeframe: string
    supported_timeframes: string[]
  }
}

export type TimezoneOption = {
  value: string
  label: string
}

export type SettingsFormState = {
  timezone: string
  stableQuotesOnly: boolean
  stableQuoteAssets: string[]
  symbolSearchLimit: number
  timeframes: string[]
  defaultTimeframe: string
  supportedTimeframes: string[]
}

export type SettingsUpdatePayload = {
  timezone: string
  marketdata: {
    stable_quotes_only: boolean
    stable_quote_assets: string[]
    symbol_search_limit: number
    timeframes: string[]
    default_timeframe: string
  }
}

export const IANA_TIMEZONE_COUNT = 418
export const UNIQUE_CURRENT_UTC_OFFSET_COUNT = 37

export const COMMON_TIMEZONE_OPTIONS: TimezoneOption[] = [
  { value: 'UTC', label: 'UTC+00:00 — UTC / London' },
  { value: 'UTC+01:00', label: 'UTC+01:00 — Central Europe / West Africa' },
  { value: 'UTC+02:00', label: 'UTC+02:00 — Eastern Europe / South Africa' },
  { value: 'UTC+03:00', label: 'UTC+03:00 — Moscow / Middle East / East Africa' },
  { value: 'UTC+04:00', label: 'UTC+04:00 — Gulf / Caucasus' },
  { value: 'UTC+05:00', label: 'UTC+05:00 — Pakistan / Uzbekistan' },
  { value: 'UTC+05:30', label: 'UTC+05:30 — India / Sri Lanka' },
  { value: 'UTC+07:00', label: 'UTC+07:00 — Thailand / Vietnam / Indonesia' },
  { value: 'UTC+08:00', label: 'UTC+08:00 — Singapore / Hong Kong / China' },
  { value: 'UTC+09:00', label: 'UTC+09:00 — Japan / Korea' },
  { value: 'UTC+10:00', label: 'UTC+10:00 — Australia East / Vladivostok' },
  { value: 'UTC+12:00', label: 'UTC+12:00 — New Zealand' },
  { value: 'UTC-08:00', label: 'UTC-08:00 — US Pacific' },
  { value: 'UTC-06:00', label: 'UTC-06:00 — US Central / Mexico' },
  { value: 'UTC-05:00', label: 'UTC-05:00 — US Eastern / Colombia / Peru' },
  { value: 'UTC-03:00', label: 'UTC-03:00 — Brazil / Argentina' },
]

export const STABLE_QUOTE_PRESETS = ['USDT', 'USDC', 'USD', 'FDUSD', 'BUSD', 'TUSD', 'USDP', 'DAI']

const LEGACY_TIMEZONE_LABELS: Record<string, string> = {
  'Europe/Moscow': 'UTC+03:00 — Moscow / Middle East / East Africa',
  'Asia/Tokyo': 'UTC+09:00 — Japan / Korea',
  'Asia/Singapore': 'UTC+08:00 — Singapore / Hong Kong / China',
  'Asia/Dubai': 'UTC+04:00 — Gulf / Caucasus',
  'America/New_York': 'UTC-05:00 — US Eastern / Colombia / Peru',
  'America/Chicago': 'UTC-06:00 — US Central / Mexico',
  'America/Los_Angeles': 'UTC-08:00 — US Pacific',
}

export function timezoneOptionLabel(timezone: string): string {
  return COMMON_TIMEZONE_OPTIONS.find((option) => option.value === timezone)?.label
    ?? LEGACY_TIMEZONE_LABELS[timezone]
    ?? timezone
}

export function normalizeAssetList(value: string | string[]): string[] {
  const items = Array.isArray(value) ? value : value.split(',')
  return Array.from(new Set(
    items
      .map((item) => String(item).trim().toUpperCase())
      .filter(Boolean)
  ))
}

export function addStableQuoteAsset(assets: string[], asset: string): string[] {
  return normalizeAssetList([...assets, asset])
}

export function removeStableQuoteAsset(assets: string[], asset: string): string[] {
  const target = asset.trim().toUpperCase()
  return normalizeAssetList(assets).filter((item) => item !== target)
}

export function dedupeTimeframes(timeframes: string[]): string[] {
  return Array.from(new Set(timeframes.map((item) => item.trim()).filter(Boolean)))
}

export function normalizeSettingsPayload(payload: SettingsPayload): SettingsFormState {
  return {
    timezone: payload.timezone,
    stableQuotesOnly: payload.marketdata.stable_quotes_only,
    stableQuoteAssets: normalizeAssetList(payload.marketdata.stable_quote_assets),
    symbolSearchLimit: payload.marketdata.symbol_search_limit,
    timeframes: [...payload.marketdata.timeframes],
    defaultTimeframe: payload.marketdata.default_timeframe,
    supportedTimeframes: [...payload.marketdata.supported_timeframes],
  }
}

export function settingsPayloadToUpdate(form: SettingsFormState): SettingsUpdatePayload {
  const timeframes = dedupeTimeframes(form.timeframes)
  const withDefault = timeframes.includes(form.defaultTimeframe)
    ? timeframes
    : [...timeframes, form.defaultTimeframe]
  return {
    timezone: form.timezone.trim(),
    marketdata: {
      stable_quotes_only: form.stableQuotesOnly,
      stable_quote_assets: normalizeAssetList(form.stableQuoteAssets),
      symbol_search_limit: Number(form.symbolSearchLimit),
      timeframes: withDefault,
      default_timeframe: form.defaultTimeframe,
    },
  }
}
