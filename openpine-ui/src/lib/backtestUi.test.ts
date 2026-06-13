import { describe, expect, it, vi } from 'vitest'

import {
  confirmBacktestDelete,
  isTerminalBacktestStatus,
  shouldStopBacktestPolling,
} from './backtestUi'

describe('backtest UI helpers', () => {
  it('treats persisted done status as terminal', () => {
    expect(isTerminalBacktestStatus('done')).toBe(true)
    expect(isTerminalBacktestStatus('completed')).toBe(true)
    expect(isTerminalBacktestStatus('running')).toBe(false)
  })

  it('stops polling old completed runs when ws progress is unavailable', () => {
    expect(shouldStopBacktestPolling(null, { status: 'done' })).toBe(true)
    expect(shouldStopBacktestPolling(undefined, { status: 'completed' })).toBe(true)
    expect(shouldStopBacktestPolling(null, { status: 'running' })).toBe(false)
  })

  it('requires confirmation before deleting a backtest', () => {
    const confirm = vi.fn(() => false)

    expect(confirmBacktestDelete({ run_id: 'run-1', strategy_name: 'Demo' }, confirm)).toBeNull()
    expect(confirm).toHaveBeenCalledWith('Delete backtest Demo (run-1)?')
  })
})
