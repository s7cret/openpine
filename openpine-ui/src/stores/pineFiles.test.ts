import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePineFilesStore } from './pineFiles'
import * as api from '@/api/client'

vi.mock('@/api/client', () => ({
  createPineFile: vi.fn(),
  getPineFiles: vi.fn(),
  compilePineFile: vi.fn(),
  archivePineFile: vi.fn(),
  unarchivePineFile: vi.fn(),
}))

describe('pine files store compile lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.createPineFile).mockReset()
    vi.mocked(api.getPineFiles).mockReset()
    vi.mocked(api.compilePineFile).mockReset()
    vi.mocked(api.archivePineFile).mockReset()
    vi.mocked(api.unarchivePineFile).mockReset()
  })

  it('reports queued async compilation without marking it as completed', async () => {
    vi.mocked(api.createPineFile).mockResolvedValue({ data: { id: 'src-1' } } as any)
    vi.mocked(api.getPineFiles).mockResolvedValue({ data: [{ id: 'src-1', name: 'demo' }] } as any)
    vi.mocked(api.compilePineFile).mockResolvedValue({ data: { status: 'queued', operation_id: 'op-1', source_id: 'src-1' } } as any)

    const store = usePineFilesStore()
    const result = await store.create('demo', 'indicator("demo")')

    expect(result).toEqual({ sourceId: 'src-1', compileQueued: true, operationId: 'op-1' })
    expect(store.compiling.has('src-1')).toBe(true)
  })

  it('archives and restores a Pine file through the API action', async () => {
    const store = usePineFilesStore()
    store.items = [{ id: 'src-1', name: 'demo.pine', archived: false }]

    vi.mocked(api.archivePineFile).mockResolvedValueOnce({ data: { id: 'src-1', name: 'demo.pine', archived: true } } as any)
    vi.mocked(api.getPineFiles).mockResolvedValueOnce({ data: [{ id: 'src-1', name: 'demo.pine', archived: true }] } as any)

    await store.setArchived('src-1', true)

    expect(api.archivePineFile).toHaveBeenCalledWith('src-1')
    expect(store.items[0].archived).toBe(true)

    vi.mocked(api.unarchivePineFile).mockResolvedValueOnce({ data: { id: 'src-1', name: 'demo.pine', archived: false } } as any)
    vi.mocked(api.getPineFiles).mockResolvedValueOnce({ data: [{ id: 'src-1', name: 'demo.pine', archived: false }] } as any)

    await store.setArchived('src-1', false)

    expect(api.unarchivePineFile).toHaveBeenCalledWith('src-1')
    expect(store.items[0].archived).toBe(false)
  })
})
