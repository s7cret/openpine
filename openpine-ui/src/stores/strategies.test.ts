import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useStrategiesStore } from './strategies'
import * as api from '@/api/client'

vi.mock('@/api/client', () => ({
  getStrategy: vi.fn(),
  getStrategies: vi.fn(),
  controlStrategy: vi.fn(),
  createStrategy: vi.fn(),
  previewDeleteStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
}))

describe('strategies store detail lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.getStrategy).mockReset()
  })

  it('clears stale current strategy and surfaces detail load failures', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.mocked(api.getStrategy).mockResolvedValueOnce({ data: { strategy_id: 'old', name: 'Old' } } as any)
    const store = useStrategiesStore()

    await store.fetchOne('old')
    expect(store.current?.strategy_id).toBe('old')

    vi.mocked(api.getStrategy).mockRejectedValueOnce(new Error('not found'))
    await expect(store.fetchOne('missing')).rejects.toThrow('not found')
    expect(store.current).toBeNull()
    consoleSpy.mockRestore()
  })
})
