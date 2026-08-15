export function parseUtcMs(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return Math.trunc(value)
  }
  throw new Error('invalid utc ms timestamp')
}

export function formatUtcMs(value: unknown): string {
  const ms = parseUtcMs(value)
  return new Date(ms).toISOString()
}
