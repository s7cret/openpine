import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { canonicalJobStatus, isTerminalJobStatus } from '@/lib/jobStatus'

export type JobKind = 'compile' | 'backtest' | 'optimize' | 'parity' | 'backfill' | 'unknown'

export type JobRecord = {
  id: string
  kind: JobKind
  status: string
  title: string
  href: string
  updatedAt: number
}

export const useJobInboxStore = defineStore('jobInbox', () => {
  const items = ref<JobRecord[]>([])

  const active = computed(() => items.value.filter((job) => !isTerminalJobStatus(job.status)))
  const recent = computed(() => [...items.value].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 20))

  function upsert(job: JobRecord) {
    const next = {
      ...job,
      status: canonicalJobStatus(job.status),
    }
    const index = items.value.findIndex((item) => item.id === next.id)
    if (index >= 0) items.value[index] = next
    else items.value.unshift(next)
  }

  return { items, active, recent, upsert }
})
