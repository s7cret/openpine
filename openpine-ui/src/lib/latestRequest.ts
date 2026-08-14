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
