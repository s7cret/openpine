const SUCCESS_STATUSES = new Set(['done', 'completed', 'success', 'succeeded'])
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function canonicalJobStatus(status?: string | null): string {
  const value = String(status ?? '').trim().toLowerCase()
  if (SUCCESS_STATUSES.has(value)) return 'completed'
  if (value === 'error') return 'failed'
  if (value === 'canceled') return 'cancelled'
  return value || 'unknown'
}

export function isSuccessfulJobStatus(status?: string | null): boolean {
  return canonicalJobStatus(status) === 'completed'
}

export function isTerminalJobStatus(status?: string | null): boolean {
  return TERMINAL_STATUSES.has(canonicalJobStatus(status))
}

export function jobStatusI18nKey(status?: string | null): string {
  const canonical = canonicalJobStatus(status)
  const suffix = canonical.charAt(0).toUpperCase() + canonical.slice(1)
  return `common.status${suffix}`
}
