import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '@/api/client'
import { useBacktestsStore } from './backtests'

vi.mock('@/api/client', () => ({
  runBacktest: vi.fn(),
}))

describe('backtest launch idempotency', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.runBacktest).mockReset()
  })

  it('reuses the request key after an uncertain failure and rotates after success', async () => {
    vi.mocked(api.runBacktest)
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ data: { run_id: 'run-1' } } as any)
      .mockResolvedValueOnce({ data: { run_id: 'run-2' } } as any)
    const store = useBacktestsStore()
    const payload = { strategy_id: 'strategy-1', from_time: '2026-01-01', to_time: '2026-01-02' }

    expect(await store.run(payload)).toBeNull()
    expect(await store.run(payload)).toEqual({ run_id: 'run-1' })
    expect(await store.run(payload)).toEqual({ run_id: 'run-2' })

    const firstKey = vi.mocked(api.runBacktest).mock.calls[0][1]
    const retryKey = vi.mocked(api.runBacktest).mock.calls[1][1]
    const nextKey = vi.mocked(api.runBacktest).mock.calls[2][1]
    expect(firstKey).toMatch(/^[0-9a-f-]{36}$/i)
    expect(retryKey).toBe(firstKey)
    expect(nextKey).not.toBe(firstKey)
  })

  it('uses a new request key when the launch payload changes', async () => {
    vi.mocked(api.runBacktest).mockRejectedValue(new Error('offline'))
    const store = useBacktestsStore()

    await store.run({ strategy_id: 'strategy-1', initial_capital: 1000 })
    await store.run({ strategy_id: 'strategy-1', initial_capital: 2000 })

    expect(vi.mocked(api.runBacktest).mock.calls[1][1])
      .not.toBe(vi.mocked(api.runBacktest).mock.calls[0][1])
  })
})
