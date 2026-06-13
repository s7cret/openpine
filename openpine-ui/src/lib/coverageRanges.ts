export type CoverageRange = {
  from_ms?: number | null
  to_ms?: number | null
  collapsed?: number | null
}

export function formatCoverageRange(range: CoverageRange, formatDate: (ms?: number | null) => string): string {
  if (range.collapsed) return `+${range.collapsed} ranges`
  return `${formatDate(range.from_ms)} → ${formatDate(range.to_ms)}`
}

export function coverageRangeLabels(
  ranges: CoverageRange[],
  formatDate: (ms?: number | null) => string,
): string[] {
  const labels = ranges.map((range) => formatCoverageRange(range, formatDate))
  if (labels.length <= 3) return labels

  const collapsedTotal = ranges
    .slice(1, -1)
    .reduce((total, range) => total + Number(range.collapsed ?? 1), 0)

  return [labels[0], `+${collapsedTotal} ranges`, labels[labels.length - 1]]
}
