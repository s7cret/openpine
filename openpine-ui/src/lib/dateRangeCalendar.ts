function parseDateOnly(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2]) - 1
  const day = Number(match[3])
  const date = new Date(year, month, day)
  if (
    date.getFullYear() !== year
    || date.getMonth() !== month
    || date.getDate() !== day
  ) return null
  return date
}

function monthStart(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

export function calendarMonthsForRange(
  from: string,
  to: string,
  fallback = new Date(),
): { left: Date; right: Date } {
  const left = monthStart(parseDateOnly(from) ?? fallback)
  const selectedTo = monthStart(parseDateOnly(to) ?? left)
  if (selectedTo > left) return { left, right: selectedTo }

  const right = new Date(left)
  right.setMonth(right.getMonth() + 1)
  return { left, right }
}

export function resolveLocalizedArray(
  value: unknown,
  resolve: (message: any) => string,
  fallback: readonly string[],
): string[] {
  if (!Array.isArray(value) || value.length !== fallback.length) return [...fallback]

  try {
    const resolved = value.map(message => (
      typeof message === 'string' ? message : resolve(message)
    ))
    if (resolved.some(label => typeof label !== 'string' || !label)) return [...fallback]
    return resolved
  } catch {
    return [...fallback]
  }
}
