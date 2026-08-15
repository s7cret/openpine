import { defineStore } from 'pinia'
import { ref } from 'vue'
import { cancelJob, getJob, getJobEvents, getJobs, retryJob, type JobV1 } from '@/api/client'
import { parseUtcMs } from '@/lib/utcMs'

export const useJobsStore = defineStore('jobs', () => {
  const items = ref<JobV1[]>([])
  const current = ref<JobV1 | null>(null)
  const loading = ref(false)
  const error = ref('')
  const cursor = ref<string | null>(null)
  const eventCursor = ref('')

  function requireJobId(jobId: string | undefined): string {
    if (!jobId) throw new Error('job_id required')
    return jobId
  }

  async function fetchList(kind?: string) {
    loading.value = true
    error.value = ''
    try {
      const { data } = await getJobs({ kind, limit: 100 })
      items.value = data.items.map((job) => {
        requireJobId(job.job_id)
        parseUtcMs(job.created_at_utc_ms)
        return job
      })
      cursor.value = data.cursor
    } catch (exc: any) {
      error.value = exc?.response?.data?.detail ?? exc?.message ?? 'jobs fetch failed'
    } finally {
      loading.value = false
    }
  }

  async function fetchOne(jobId: string) {
    const id = requireJobId(jobId)
    const { data } = await getJob(id)
    requireJobId(data.job_id)
    parseUtcMs(data.created_at_utc_ms)
    current.value = data
    return data
  }

  async function reconcile(after = eventCursor.value) {
    const { data } = await getJobEvents(after)
    if (data.resync) {
      eventCursor.value = ''
      await fetchList()
      return
    }
    for (const event of data.items) {
      requireJobId(event.job_id)
      eventCursor.value = event.event_id
    }
  }

  async function cancel(jobId: string, key: string) {
    const { data } = await cancelJob(requireJobId(jobId), key)
    current.value = data
    return data
  }

  async function retry(jobId: string) {
    const { data } = await retryJob(requireJobId(jobId))
    current.value = data
    return data
  }

  return { items, current, loading, error, cursor, fetchList, fetchOne, reconcile, cancel, retry }
})
