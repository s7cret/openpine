export interface MtfSeriesRow {
  id: number
  symbol: string
  timeframe: string
}

export interface MtfSeriesRequest {
  symbol: string
  timeframe: string
}

let nextMtfSeriesId = 1

export function newMtfSeriesRow(): MtfSeriesRow {
  return { id: nextMtfSeriesId++, symbol: '', timeframe: '' }
}

export function mtfSeriesValidationKey(rows: MtfSeriesRow[]): string {
  const keys = new Set<string>()
  for (const row of rows) {
    const symbol = row.symbol.trim().toUpperCase()
    const timeframe = row.timeframe.trim()
    if (!symbol || !timeframe) return 'mtf.required'
    const key = `${symbol}:${timeframe.toLowerCase()}`
    if (keys.has(key)) return 'mtf.duplicate'
    keys.add(key)
  }
  return ''
}

export function toMtfSeriesRequests(rows: MtfSeriesRow[]): MtfSeriesRequest[] {
  return rows.map((row) => ({
    symbol: row.symbol.trim().toUpperCase(),
    timeframe: row.timeframe.trim(),
  }))
}
