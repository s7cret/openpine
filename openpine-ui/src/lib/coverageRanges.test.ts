import { describe, expect, it } from 'vitest'

import { coverageRangeLabels, formatCoverageRange } from './coverageRanges'

const fmt = (ms?: number | null) => String(ms ?? '—')

describe('coverage range labels', () => {
  it('formats regular and collapsed ranges', () => {
    expect(formatCoverageRange({ from_ms: 1, to_ms: 2 }, fmt)).toBe('1 → 2')
    expect(formatCoverageRange({ collapsed: 12 }, fmt)).toBe('+12 ranges')
  })

  it('keeps data table range cells bounded', () => {
    expect(coverageRangeLabels([
      { from_ms: 1, to_ms: 2 },
      { from_ms: 3, to_ms: 4 },
      { collapsed: 24 },
      { from_ms: 5, to_ms: 6 },
      { from_ms: 7, to_ms: 8 },
    ], fmt)).toEqual(['1 → 2', '+26 ranges', '7 → 8'])
  })
})
