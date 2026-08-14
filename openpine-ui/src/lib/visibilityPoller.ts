export type VisibilityPollerOptions = {
  poll: (signal: AbortSignal) => Promise<unknown>
  intervalMs: number
  maxBackoffMs?: number
  onError?: (error: unknown) => void
}

export type VisibilityPoller = {
  start: (immediate?: boolean) => void
  stop: () => void
  pollNow: () => Promise<void>
  active: () => boolean
}

export function createVisibilityPoller(options: VisibilityPollerOptions): VisibilityPoller {
  const maxBackoffMs = options.maxBackoffMs ?? Math.max(options.intervalMs, 60_000)
  let running = false
  let inFlight = false
  let failures = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null

  function visible() {
    return typeof document === 'undefined' || document.visibilityState !== 'hidden'
  }

  function clearTimer() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function schedule() {
    clearTimer()
    if (!running || !visible()) return
    const delay = failures
      ? Math.min(maxBackoffMs, options.intervalMs * (2 ** failures))
      : options.intervalMs
    timer = setTimeout(() => { void pollNow() }, delay)
  }

  async function pollNow() {
    if (!running || inFlight || !visible()) return
    clearTimer()
    inFlight = true
    controller = new AbortController()
    const activeController = controller
    try {
      await options.poll(activeController.signal)
      failures = 0
    } catch (error) {
      if (!activeController.signal.aborted) {
        failures += 1
        options.onError?.(error)
      }
    } finally {
      if (controller === activeController) controller = null
      inFlight = false
      schedule()
    }
  }

  function onVisibilityChange() {
    if (!visible()) {
      clearTimer()
      controller?.abort()
      return
    }
    if (running) void pollNow()
  }

  function start(immediate = true) {
    if (running) return
    running = true
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
    if (immediate) void pollNow()
    else schedule()
  }

  function stop() {
    if (!running) return
    running = false
    clearTimer()
    controller?.abort()
    controller = null
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }

  return { start, stop, pollNow, active: () => running }
}
