export const DISPLAY_TIME_ZONE = 'Europe/Moscow'
export const DISPLAY_TIME_ZONE_LABEL = 'MSK'

const DATE_TIME_FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const CHART_TIME_FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_TIME_ZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function toMillis(value: number | string | null | undefined): number | null {
  if (value == null || value === '') return null
  const ms = typeof value === 'number' ? (value > 1e12 ? value : value * 1000) : new Date(value).getTime()
  return Number.isFinite(ms) ? ms : null
}

export function formatDateTime(value: number | string | null | undefined): string {
  const ms = toMillis(value)
  if (ms == null) return '-'
  return `${DATE_TIME_FORMAT.format(new Date(ms))} ${DISPLAY_TIME_ZONE_LABEL}`
}

export function formatChartTime(value: number | string | object | null | undefined): string {
  let ms: number | null = null
  if (value && typeof value === 'object' && 'year' in value && 'month' in value && 'day' in value) {
    const day = value as { year: number; month: number; day: number }
    ms = Date.UTC(day.year, day.month - 1, day.day)
  } else {
    ms = toMillis(value as number | string | null | undefined)
  }
  if (ms == null) return '?'
  return `${CHART_TIME_FORMAT.format(new Date(ms))} ${DISPLAY_TIME_ZONE_LABEL}`
}
