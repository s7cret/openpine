import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createVisibilityPoller } from './visibilityPoller'

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('createVisibilityPoller', () => {
  beforeEach(() => {
    const fakeDocument = new EventTarget()
    Object.defineProperty(fakeDocument, 'visibilityState', { configurable: true, value: 'visible' })
    vi.stubGlobal('document', fakeDocument)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('never overlaps requests and schedules from completion', async () => {
    vi.useFakeTimers()
    const first = deferred<void>()
    const poll = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue(undefined)
    const poller = createVisibilityPoller({ poll, intervalMs: 1000 })

    poller.start()
    await flush()
    await vi.advanceTimersByTimeAsync(5000)
    expect(poll).toHaveBeenCalledTimes(1)

    first.resolve()
    await flush()
    await vi.advanceTimersByTimeAsync(999)
    expect(poll).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(poll).toHaveBeenCalledTimes(2)
    poller.stop()
  })

  it('aborts and pauses while hidden, then refreshes on visibility', async () => {
    vi.useFakeTimers()
    const first = deferred<void>()
    const signals: AbortSignal[] = []
    const poll = vi.fn((signal: AbortSignal) => {
      signals.push(signal)
      return signals.length === 1 ? first.promise : Promise.resolve()
    })
    const poller = createVisibilityPoller({ poll, intervalMs: 1000 })
    poller.start()
    await flush()
    expect(poll).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(signals[0].aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(poll).toHaveBeenCalledTimes(1)
    first.resolve()
    await flush()

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(poll).toHaveBeenCalledTimes(2)
    poller.stop()
  })

  it('can defer the first request until the interval', async () => {
    vi.useFakeTimers()
    const poll = vi.fn().mockResolvedValue(undefined)
    const poller = createVisibilityPoller({ poll, intervalMs: 1000 })
    poller.start(false)
    await flush()
    expect(poll).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(poll).toHaveBeenCalledTimes(1)
    poller.stop()
  })

  it('backs off after errors', async () => {
    vi.useFakeTimers()
    const poll = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(undefined)
    const poller = createVisibilityPoller({ poll, intervalMs: 1000, maxBackoffMs: 8000 })
    poller.start()
    await flush()
    expect(poll).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1999)
    expect(poll).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(poll).toHaveBeenCalledTimes(2)
    poller.stop()
  })
})
