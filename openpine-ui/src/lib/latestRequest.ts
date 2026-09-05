export type LatestRequestHandle = {
  signal: AbortSignal
  isCurrent: () => boolean
}

export type LatestRequestController = {
  begin: () => LatestRequestHandle
  cancel: () => void
}

export function createLatestRequest(): LatestRequestController {
  let controller: AbortController | null = null
  let generation = 0

  function cancel() {
    generation += 1
    controller?.abort()
    controller = null
  }

  function begin(): LatestRequestHandle {
    cancel()
    controller = new AbortController()
    const requestController = controller
    const requestGeneration = generation

    return {
      signal: requestController.signal,
      isCurrent: () => (
        generation === requestGeneration
        && controller === requestController
        && !requestController.signal.aborted
      ),
    }
  }

  return { begin, cancel }
}


/** Generation tokens suppress stale async writes; they do not cancel server work. */
export function createRequestEpoch() {
  let generation = 0
  let disposed = false
  const capture = () => {
    const ticket = generation
    return () => !disposed && ticket === generation
  }
  return {
    capture,
    begin: () => { generation += 1; return capture() },
    invalidate: () => { generation += 1 },
    dispose: () => { disposed = true; generation += 1 },
  }
}
