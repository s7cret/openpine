import { describe, expect, it } from 'vitest'

import { createLatestRequest } from './latestRequest'

describe('latest request cancellation', () => {
  it('aborts the previous request and invalidates it before a replacement starts', () => {
    const requests = createLatestRequest()
    const first = requests.begin()

    expect(first.signal.aborted).toBe(false)
    expect(first.isCurrent()).toBe(true)

    const second = requests.begin()

    expect(first.signal.aborted).toBe(true)
    expect(first.isCurrent()).toBe(false)
    expect(second.signal.aborted).toBe(false)
    expect(second.isCurrent()).toBe(true)

    requests.cancel()
    expect(second.signal.aborted).toBe(true)
    expect(second.isCurrent()).toBe(false)
  })
})