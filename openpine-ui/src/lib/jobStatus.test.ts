import { describe, expect, it } from 'vitest'

import {
  canonicalJobStatus,
  isSuccessfulJobStatus,
  isTerminalJobStatus,
} from './jobStatus'

describe('canonical async job statuses', () => {
  it.each(['done', 'completed', 'success', 'succeeded'])('normalizes %s to completed', (status) => {
    expect(canonicalJobStatus(status)).toBe('completed')
    expect(isSuccessfulJobStatus(status)).toBe(true)
    expect(isTerminalJobStatus(status)).toBe(true)
  })

  it.each(['failed', 'cancelled', 'canceled', ' CANCELED '])('treats %s as terminal but unsuccessful', (status) => {
    expect(isTerminalJobStatus(status)).toBe(true)
    expect(isSuccessfulJobStatus(status)).toBe(false)
  })

  it('keeps running work non-terminal', () => {
    expect(canonicalJobStatus('RUNNING')).toBe('running')
    expect(isTerminalJobStatus('running')).toBe(false)
  })
})
