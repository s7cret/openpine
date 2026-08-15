import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { useJobInboxStore } from './jobInbox'

describe('job inbox store', () => {
  it('upserts by id and keeps recent order', () => {
    setActivePinia(createPinia())
    const inbox = useJobInboxStore()
    inbox.upsert({ id: 'a', kind: 'backtest', status: 'running', title: 'A', href: '/backtests/a', updatedAt: 1 })
    inbox.upsert({ id: 'b', kind: 'compile', status: 'done', title: 'B', href: '/jobs/b', updatedAt: 2 })
    inbox.upsert({ id: 'a', kind: 'backtest', status: 'completed', title: 'A2', href: '/backtests/a', updatedAt: 3 })
    expect(inbox.items).toHaveLength(2)
    expect(inbox.recent[0].id).toBe('a')
    expect(inbox.recent[0].status).toBe('completed')
    expect(inbox.active).toHaveLength(0)
  })
})
