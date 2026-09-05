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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }

describe('polling generation boundaries', () => {
  it.each(['resolve', 'reject'] as const)('ignores %s after stop/disposal', async (outcome) => {
    vi.useFakeTimers()
    const request = deferred<{ status: string }>()
    const onValue = vi.fn(), onTerminal = vi.fn(), onError = vi.fn()
    const poll = vi.fn(() => request.promise)
    const poller = createTerminalPoller({ poll, onValue, onTerminal, onError, getStatus: v => v.status })
    poller.start()
    poller.stop()
    if (outcome === 'resolve') request.resolve({ status: 'done' })
    else request.reject(new Error('stale'))
    await flush()
    await vi.advanceTimersByTimeAsync(5000)
    expect(onValue).not.toHaveBeenCalled()
    expect(onTerminal).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it.each(['resolve', 'reject'] as const)('old %s cannot stop or overwrite a restarted selection', async outcome => {
    vi.useFakeTimers()
    const old = deferred<{ status: string, id: string }>()
    const current = deferred<{ status: string, id: string }>()
    const poll = vi.fn().mockImplementationOnce(() => old.promise).mockImplementationOnce(() => current.promise)
      .mockResolvedValue({ status: 'canceled', id: 'new' })
    const onValue = vi.fn(), onError = vi.fn()
    const poller = createTerminalPoller<{status: string, id: string}>({ poll, onValue, onError, getStatus: v => v.status, intervalMs: 50 })
    poller.start()
    poller.stop()
    poller.start()
    expect(poll).toHaveBeenCalledTimes(2)
    if (outcome === 'resolve') old.resolve({status: 'completed', id: 'old'})
    else old.reject(new Error('old failure'))
    await flush()
    expect(poller.active()).toBe(true)
    expect(onValue).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
    current.resolve({status: 'running', id: 'new'})
    await flush()
    expect(onValue).toHaveBeenCalledWith({status: 'running', id: 'new'})
    await vi.advanceTimersByTimeAsync(50)
    expect(poll).toHaveBeenCalledTimes(3)
    expect(poller.active()).toBe(false)
  })

  it('does not overlap within a generation and keeps start idempotent', async () => {
    const request = deferred<{status: string}>()
    const poll = vi.fn(() => request.promise)
    const poller = createTerminalPoller({poll, getStatus: v => v.status})
    poller.start(); poller.start()
    await poller.pollNow()
    expect(poll).toHaveBeenCalledTimes(1)
    request.resolve({status: 'completed'})
    await flush()
    expect(poller.active()).toBe(false)
  })

  it('does not stop a new generation started while terminal follow-up is awaiting', async () => {
    vi.useFakeTimers()
    const terminalWait = deferred<void>()
    const newRequest = deferred<{status: string}>()
    const poll = vi.fn().mockResolvedValueOnce({status: 'completed'}).mockImplementation(() => newRequest.promise)
    const onTerminal = vi.fn(() => terminalWait.promise)
    const poller = createTerminalPoller<{status: string}>({poll, onTerminal, getStatus: v => v.status})
    poller.start()
    await flush()
    expect(onTerminal).toHaveBeenCalledTimes(1)
    poller.stop(); poller.start()
    terminalWait.resolve()
    await flush()
    expect(poller.active()).toBe(true)
    expect(poll).toHaveBeenCalledTimes(2)
    poller.stop()
    newRequest.resolve({status: 'running'})
    await flush()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each([-1, NaN, Infinity])('rejects invalid interval %s', intervalMs => {
    expect(() => createTerminalPoller({poll: async () => 'done', getStatus: x => x, intervalMs})).toThrow('interval')
  })
})
