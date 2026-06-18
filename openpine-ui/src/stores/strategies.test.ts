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
  archiveStrategy: vi.fn(),
  unarchiveStrategy: vi.fn(),
}))

describe('strategies store detail lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.getStrategy).mockReset()
    vi.mocked(api.getStrategies).mockReset()
    vi.mocked(api.archiveStrategy).mockReset()
    vi.mocked(api.unarchiveStrategy).mockReset()
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

  it('archives and restores a strategy through the API action', async () => {
    const store = useStrategiesStore()
    store.items = [{ strategy_id: 'strat-1', name: 'Demo', archived: false, enabled: true }]

    vi.mocked(api.archiveStrategy).mockResolvedValueOnce({ data: { strategy_id: 'strat-1', name: 'Demo', archived: true, enabled: false } } as any)
    vi.mocked(api.getStrategies).mockResolvedValueOnce({ data: [{ strategy_id: 'strat-1', name: 'Demo', archived: true, enabled: false }] } as any)

    await store.setArchived('strat-1', true)

    expect(api.archiveStrategy).toHaveBeenCalledWith('strat-1')
    expect(store.items[0].archived).toBe(true)
    expect(store.items[0].enabled).toBe(false)

    vi.mocked(api.unarchiveStrategy).mockResolvedValueOnce({ data: { strategy_id: 'strat-1', name: 'Demo', archived: false, enabled: false } } as any)
    vi.mocked(api.getStrategies).mockResolvedValueOnce({ data: [{ strategy_id: 'strat-1', name: 'Demo', archived: false, enabled: false }] } as any)

    await store.setArchived('strat-1', false)

    expect(api.unarchiveStrategy).toHaveBeenCalledWith('strat-1')
    expect(store.items[0].archived).toBe(false)
    expect(store.items[0].enabled).toBe(false)
  })
})
