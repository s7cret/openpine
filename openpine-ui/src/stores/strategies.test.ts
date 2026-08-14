import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
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
    vi.mocked(api.controlStrategy).mockReset()
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

  it('clears stale detail before the next strategy request resolves', async () => {
    vi.mocked(api.getStrategy).mockResolvedValueOnce({ data: { strategy_id: 'old', symbol: 'ETHUSDT' } } as any)
    const store = useStrategiesStore()
    await store.fetchOne('old')

    let resolveNext!: (value: any) => void
    vi.mocked(api.getStrategy).mockImplementationOnce(() => new Promise(resolve => { resolveNext = resolve }) as any)

    const pending = store.fetchOne('new')

    expect(store.current).toBeNull()
    resolveNext({ data: { strategy_id: 'new', symbol: 'SOLUSDT' } })
    await pending
    expect(store.current?.strategy_id).toBe('new')
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

  it('surfaces action failures for the Strategies page', async () => {
    vi.mocked(api.controlStrategy).mockRejectedValueOnce(new Error('gateway refused start'))
    const store = useStrategiesStore()

    await store.control('strat-1', 'start')

    expect(store.error).toBe('gateway refused start')
  })

  it('renders store/action errors and an accessible dismissible detail dialog', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(resolve(here, '../pages/Strategies.vue'), 'utf8')

    expect(source).toContain('store.error')
    expect(source).toContain('role="alert"')
    expect(source).toContain('role="dialog"')
    expect(source).toContain('aria-modal="true"')
    expect(source).toContain('@keydown.esc')
    expect(source).not.toContain('autoFillPineSource')
    expect(source).toContain('v-if="detailStrategy"')
    expect(source).not.toContain("store.current?.symbol ?? 'BTCUSDT'")
  })
})
