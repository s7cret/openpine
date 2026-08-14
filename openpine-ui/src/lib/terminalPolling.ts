import { isTerminalJobStatus } from './jobStatus'

type TerminalPollerOptions<T> = {
  poll: () => Promise<T>
  getStatus: (value: T) => string | null | undefined
  onValue?: (value: T) => void | Promise<void>
  onTerminal?: (value: T) => void | Promise<void>
  onError?: (error: unknown) => void
  intervalMs?: number
  isTerminal?: (status: string | null | undefined) => boolean
}

export type TerminalPoller = {
  start: () => void
  stop: () => void
  pollNow: () => Promise<void>
  active: () => boolean
}

export function createTerminalPoller<T>(options: TerminalPollerOptions<T>): TerminalPoller {
  const intervalMs = options.intervalMs ?? 1500
  const terminal = options.isTerminal ?? isTerminalJobStatus
  let running = false
  let inFlight = false
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function stop() {
    running = false
    clearTimer()
  }

  function schedule() {
    clearTimer()
    if (!running) return
    timer = setTimeout(() => { void pollNow() }, intervalMs)
  }

  async function pollNow() {
    if (!running || inFlight) return
    inFlight = true
    try {
      const value = await options.poll()
      await options.onValue?.(value)
      if (terminal(options.getStatus(value))) {
        await options.onTerminal?.(value)
        stop()
        return
      }
    } catch (error) {
      options.onError?.(error)
    } finally {
      inFlight = false
    }
    schedule()
  }

  function start() {
    if (running) return
    running = true
    void pollNow()
  }

  return {
    start,
    stop,
    pollNow,
    active: () => running,
  }
}
