import { describe, expect, it } from 'vitest'
import { formatUtcMs, parseUtcMs } from './utcMs'

describe('utcMs', () => {
  it('accepts unix ms and rejects ISO strings', () => {
    expect(parseUtcMs(1_700_000_000_000)).toBe(1_700_000_000_000)
    expect(formatUtcMs(0)).toBe('1970-01-01T00:00:00.000Z')
    expect(() => parseUtcMs('2026-08-15T00:00:00Z')).toThrow(/invalid utc ms/)
    expect(() => parseUtcMs(undefined)).toThrow(/invalid utc ms/)
  })
})
