import { describe, expect, it } from 'vitest'

import { loadDataSummaryState } from './dataSummaryState'

describe('data summary loading state', () => {
  it('does not throw on failed refresh and keeps previous summary', async () => {
    const previous = { total_size_bytes: 123 }

    const state = await loadDataSummaryState(previous, async () => {
      throw new Error('backend offline')
    })

    expect(state.summary).toBe(previous)
    expect(state.error).toBe('backend offline')
  })

  it('clears old errors after successful refresh', async () => {
    const state = await loadDataSummaryState(null, async () => ({ total_size_bytes: 456 }))

    expect(state.summary).toEqual({ total_size_bytes: 456 })
    expect(state.error).toBe('')
  })
})
