import { afterEach, describe, expect, it, vi } from 'vitest'

import { createTerminalPoller } from './terminalPolling'

afterEach(() => {
  vi.useRealTimers()
})

describe('terminal poller', () => {
  it('stops immediately after done/completed and can be explicitly cleaned up', async () => {
    vi.useFakeTimers()
    const poll = vi.fn()
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({ status: 'done' })
    const onValue = vi.fn()
    const poller = createTerminalPoller({ poll, onValue, getStatus: (value) => value.status, intervalMs: 100 })

    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(poller.active()).toBe(true)
    await vi.advanceTimersByTimeAsync(100)

    expect(onValue).toHaveBeenCalledTimes(2)
    expect(poller.active()).toBe(false)
    await vi.advanceTimersByTimeAsync(500)
    expect(poll).toHaveBeenCalledTimes(2)
    poller.stop()
  })

  it('reports transient errors and keeps polling until terminal failure', async () => {
    vi.useFakeTimers()
    const poll = vi.fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ status: 'failed' })
    const onError = vi.fn()
    const poller = createTerminalPoller<{ status: string }>({ poll, onError, getStatus: (value) => value.status, intervalMs: 100 })

    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'network down' }))
    expect(poller.active()).toBe(true)
    await vi.advanceTimersByTimeAsync(100)
    expect(poller.active()).toBe(false)
  })

  it('runs terminal-only follow-up exactly once before stopping', async () => {
    const value = { status: 'completed', run_id: 'run-1' }
    const onTerminal = vi.fn()
    const poller = createTerminalPoller<{ status: string; run_id: string }>({
      poll: vi.fn().mockResolvedValue(value),
      getStatus: current => current.status,
      onTerminal,
    })

    poller.start()
    await vi.waitFor(() => expect(onTerminal).toHaveBeenCalledWith(value))
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(poller.active()).toBe(false)
  })
})
