export function isTerminalBacktestStatus(status?: string | null): boolean {
  return ['completed', 'done', 'failed', 'cancelled'].includes(String(status ?? '').toLowerCase())
}

export function shouldStopBacktestPolling(progress?: any, run?: any): boolean {
  return isTerminalBacktestStatus(progress?.status ?? run?.status)
}

export function confirmBacktestDelete(run: any, confirmFn: (message: string) => boolean = window.confirm): string | null {
  const id = run?.run_id ?? run?.id
  if (!id) return null
  const label = run?.strategy_name ?? run?.strategy_id ?? 'backtest'
  return confirmFn(`Delete backtest ${label} (${id})?`) ? id : null
}
