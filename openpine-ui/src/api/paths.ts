export type QueryValue = string | number | boolean | null | undefined

export function apiPath(...segments: string[]): string {
  return segments
    .map((segment, index) => {
      const trimmed = String(segment).replace(/^\/+|\/+$/g, '')
      if (!trimmed) return ''
      if (index === 0 && segment.startsWith('/')) return `/${trimmed}`
      return encodeURIComponent(trimmed)
    })
    .filter(Boolean)
    .join('/')
}

export function queryParams(params: Record<string, QueryValue>): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue
    qs.set(key, String(value))
  }
  return qs.toString()
}
