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
  if (!Number.isFinite(intervalMs) || intervalMs < 0) throw new Error('Invalid polling interval')
  const terminal = options.isTerminal ?? isTerminalJobStatus
  let running = false
  let generation = 0
  let inFlight: number | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function stop() {
    running = false
    generation += 1
    clearTimer()
  }

  function schedule(ticket: number) {
    if (!running || ticket !== generation) return
    clearTimer()
    timer = setTimeout(() => { void pollNow() }, intervalMs)
  }

  async function pollNow() {
    const ticket = generation
    if (!running || inFlight === ticket) return
    clearTimer()
    inFlight = ticket
    const current = () => running && generation === ticket
    try {
      const value = await options.poll()
      if (!current()) return
      await options.onValue?.(value)
      if (!current()) return
      if (terminal(options.getStatus(value))) {
        await options.onTerminal?.(value)
        // An asynchronous terminal callback may have started a new selection.
        if (current()) stop()
        return
      }
    } catch (error) {
      if (current()) options.onError?.(error)
    } finally {
      if (inFlight === ticket) inFlight = null
      schedule(ticket)
    }
  }

  function start() {
    if (running) return
    running = true
    generation += 1
    void pollNow()
  }

  return { start, stop, pollNow, active: () => running }
}
