/** Display-only helpers. Never modify source rows, metrics, verdicts or exports. */
export type PlotRow = { t: number; v?: number; kind: string }
export type PlotBounds = { tMin: number; tMax: number; yMin: number; yMax: number }
const equity = (r: PlotRow) => r.kind === 'openpine_equity' || r.kind === 'tv_equity'
const valid = (r: PlotRow) => Number.isFinite(r.t) && Math.abs(r.t) <= 8.64e15
  && typeof r.v === 'number' && Number.isFinite(r.v)

export function finiteEquityBounds(rows: Iterable<PlotRow>): PlotBounds | null {
  let tMin = Infinity, tMax = -Infinity, yMin = Infinity, yMax = -Infinity
  for (const row of rows) {
    if (!equity(row) || !valid(row)) continue
    const value = row.v as number
    tMin = Math.min(tMin, row.t); tMax = Math.max(tMax, row.t)
    yMin = Math.min(yMin, value); yMax = Math.max(yMax, value)
  }
  return tMin === Infinity ? null : { tMin, tMax, yMin, yMax }
}

/** At most four original points per horizontal bucket: first/min/max/last.
 * Retains spikes and endpoints; supports interleaved and unsorted series.
 * Missing/non-finite points are omitted, as in the existing line renderer.
 */
export function sampleEquityForDisplay<T extends PlotRow>(
  rows: Iterable<T>, kind: 'openpine_equity' | 'tv_equity', bounds: PlotBounds, width: number,
): T[] {
  if (!Number.isFinite(bounds.tMin) || !Number.isFinite(bounds.tMax) || bounds.tMax < bounds.tMin) return []
  const columns = Number.isFinite(width) ? Math.max(1, Math.min(4096, Math.floor(width))) : 1
  type Point = { row: T; index: number }
  type Bucket = { first: Point; last: Point; min: Point; max: Point }
  const buckets: (Bucket | undefined)[] = new Array(columns)
  const earlier = (a: Point, b: Point) => a.row.t < b.row.t || (a.row.t === b.row.t && a.index < b.index)
  let index = 0
  for (const row of rows) {
    const point = { row, index: index++ }
    if (row.kind !== kind || !valid(row) || row.t < bounds.tMin || row.t > bounds.tMax) continue
    const x = bounds.tMax === bounds.tMin ? 0 : Math.min(columns - 1,
      Math.floor((row.t - bounds.tMin) * columns / (bounds.tMax - bounds.tMin)))
    const bucket = buckets[x]
    if (!bucket) buckets[x] = { first: point, last: point, min: point, max: point }
    else {
      if (earlier(point, bucket.first)) bucket.first = point
      if (earlier(bucket.last, point)) bucket.last = point
      if ((row.v as number) < (bucket.min.row.v as number)) bucket.min = point
      if ((row.v as number) > (bucket.max.row.v as number)) bucket.max = point
    }
  }
  const sampled: Point[] = []
  for (const bucket of buckets) {
    if (!bucket) continue
    const points = new Map<number, Point>()
    for (const p of [bucket.first, bucket.min, bucket.max, bucket.last]) points.set(p.index, p)
    sampled.push(...[...points.values()].sort((a, b) => a.row.t - b.row.t || a.index - b.index))
  }
  return sampled.map(p => p.row)
}
