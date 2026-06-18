export type TradeTimeLike = {
  entry_time?: number | string | null
  exit_time?: number | string | null
  created_at?: number | string | null
}

export type TimeRangeMs = {
  fromMs: number
  toMs: number
}

export function toTradeTimeMs(value: number | string | null | undefined): number | null {
  if (value == null || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isFinite(n)) return n > 1e12 ? n : n * 1000
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : null
}

export function tradeBoundsMs(trades: TradeTimeLike[], paddingMs = 0): TimeRangeMs | null {
  let minMs: number | null = null
  let maxMs: number | null = null

  for (const trade of trades) {
    const entryMs = toTradeTimeMs(trade.entry_time ?? trade.created_at)
    const exitMs = toTradeTimeMs(trade.exit_time)
    for (const value of [entryMs, exitMs]) {
      if (value == null) continue
      minMs = minMs == null ? value : Math.min(minMs, value)
      maxMs = maxMs == null ? value : Math.max(maxMs, value)
    }
  }

  if (minMs == null || maxMs == null) return null
  return { fromMs: minMs - paddingMs, toMs: maxMs + paddingMs }
}

export function rangesOverlap(a: TimeRangeMs, b: TimeRangeMs): boolean {
  return a.toMs >= b.fromMs && a.fromMs <= b.toMs
}

export function dateInputFromMs(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}
