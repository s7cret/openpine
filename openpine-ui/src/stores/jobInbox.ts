import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export type JobRecord = {
  job_id: string
  kind: string
  state: string
  href: string
  updated_at_utc_ms: number | null
}

export const useJobInboxStore = defineStore('jobInbox', () => {
  const items = ref<JobRecord[]>([])
  const error = ref('')

  async function fetchAll() {
    error.value = ''
    const { data } = await api.get('/jobs')
    const jobs = Array.isArray(data?.jobs) ? data.jobs : []
    items.value = jobs.map((job: Record<string, unknown>) => {
      const jobId = String(job.job_id ?? '')
      if (!jobId) {
        throw new Error('persisted job is missing id')
      }
      return {
        job_id: jobId,
        kind: String(job.kind ?? 'unknown'),
        state: String(job.state ?? 'unknown'),
        href: String(job.href ?? `/jobs/${jobId}`),
        updated_at_utc_ms: typeof job.updated_at_utc_ms === 'number' ? job.updated_at_utc_ms : null,
      }
    })
  }

  return { items, error, fetchAll }
})
