export type MarketMeta = {
  icon: string
  label: string
  cls: string
}

export const stableQuoteAssets = ['USDT', 'USDC', 'FDUSD', 'TUSD', 'DAI', 'USDP', 'BUSD', 'USD']

export const exchangeMeta: Record<string, MarketMeta & { markets: string[] }> = {
  binance: {
    icon: '◆',
    label: 'Binance',
    cls: 'border-[#F3BA2F]/30 bg-[#F3BA2F]/10 text-[#F3BA2F]',
    markets: ['spot', 'futures', 'margin', 'delivery'],
  },
  bybit: {
    icon: '◇',
    label: 'Bybit',
    cls: 'border-yellow-400/30 bg-yellow-400/10 text-yellow-300',
    markets: ['spot', 'futures'],
  },
  okx: {
    icon: '▦',
    label: 'OKX',
    cls: 'border-gray-300/30 bg-gray-300/10 text-gray-200',
    markets: ['spot', 'futures'],
  },
}

export const marketTypeMeta: Record<string, MarketMeta> = {
  spot: { icon: '●', label: 'Spot', cls: 'text-success' },
  futures: { icon: '⚡', label: 'Futures', cls: 'text-warning' },
  margin: { icon: '↔', label: 'Margin', cls: 'text-accent-light' },
  delivery: { icon: '◆', label: 'Delivery', cls: 'text-purple-300' },
}

export function marketTypeLabel(marketType?: string) {
  const meta = marketTypeMeta[(marketType ?? '').toLowerCase()]
  return meta ? `${meta.icon} ${meta.label}` : (marketType ?? '—')
}

export function marketTypeIcon(marketType?: string) {
  return marketTypeMeta[(marketType ?? '').toLowerCase()]?.icon ?? '•'
}

export function marketTypeClass(marketType?: string) {
  return marketTypeMeta[(marketType ?? '').toLowerCase()]?.cls ?? 'text-gray-400'
}

export function exchangeLabel(exchange?: string) {
  return exchangeMeta[(exchange ?? '').toLowerCase()]?.label ?? (exchange ?? '—')
}

export function exchangeIcon(exchange?: string) {
  return exchangeMeta[(exchange ?? '').toLowerCase()]?.icon ?? '•'
}

export function exchangeClass(exchange?: string) {
  return exchangeMeta[(exchange ?? '').toLowerCase()]?.cls ?? 'border-dark-500 bg-dark-600 text-gray-400'
}

export function baseAssetFromSymbol(symbol?: string) {
  const upper = (symbol ?? '').toUpperCase()
  const contractRoot = upper.split('_', 1)[0]
  const quote = stableQuoteAssets.find((q) => contractRoot.endsWith(q))
  return quote ? contractRoot.slice(0, -quote.length) : contractRoot
}

export function tickerIconUrl(asset?: string) {
  const base = (asset ?? '').toLowerCase()
  return base ? `https://assets.coincap.io/assets/icons/${base}@2x.png` : ''
}

export function tickerInitials(asset?: string) {
  return (asset ?? '?').slice(0, 3).toUpperCase()
}

const missingTickerIconKey = 'openpine.missingTickerIcons.v1'

export function loadMissingTickerIcons() {
  try {
    return new Set<string>(JSON.parse(localStorage.getItem(missingTickerIconKey) || '[]'))
  } catch {
    return new Set<string>()
  }
}

export function storeMissingTickerIcons(values: Set<string>) {
  try {
    localStorage.setItem(missingTickerIconKey, JSON.stringify(Array.from(values).slice(0, 500)))
  } catch {
    // Ignore storage failures; visual fallback still works for this session.
  }
}
