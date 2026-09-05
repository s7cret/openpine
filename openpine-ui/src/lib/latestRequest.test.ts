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
import { createRequestEpoch } from './latestRequest'

describe('request epoch (does not cancel server jobs)', () => {
  it('invalidates captures on selection and cannot revive after disposal', () => {
    const epoch = createRequestEpoch()
    const first = epoch.begin()
    const poll = epoch.capture()
    expect(first()).toBe(true)
    expect(poll()).toBe(true)
    const next = epoch.begin()
    expect(first()).toBe(false)
    expect(poll()).toBe(false)
    expect(next()).toBe(true)
    epoch.invalidate()
    expect(next()).toBe(false)
    const last = epoch.begin()
    epoch.dispose()
    expect(last()).toBe(false)
    expect(epoch.begin()()).toBe(false)
    expect(epoch.capture()()).toBe(false)
  })
})
