import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import * as api from '@/api/client'

export const useBacktestsStore = defineStore('backtests', () => {
  const items = ref<any[]>([])
  const current = ref<any>(null)
  const progress = ref<any>(null)
  const progressMap = reactive<Record<string, any>>({})
  const loading = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      const { data } = await api.getBacktests()
      items.value = Array.isArray(data) ? data : data?.runs ?? []
    } catch (e) {
      console.error('Backtests fetch failed', e)
    } finally {
      loading.value = false
    }
  }

  async function fetchOne(id: string) {
    try {
      const { data } = await api.getBacktest(id)
      current.value = data
    } catch (e) { console.error(e) }
  }

  async function fetchProgress(id: string) {
    try {
      const { data } = await api.getBacktestProgress(id)
      progress.value = data
      if (data) {
        progressMap[id] = data
      }
    } catch (e) { progress.value = null }
  }

  function getProgress(id: string) {
    return progressMap[id] ?? null
  }

  async function run(data: any) {
    try {
      const res = await api.runBacktest(data)
      return res.data
    } catch (e) { console.error(e); return null }
  }

  async function estimate(data: { strategy_id: string; from_time: string; to_time: string }) {
    try {
      const res = await api.estimateBacktest(data)
      return res.data
    } catch (e) { console.error(e); return null }
  }

  async function deleteRun(id: string) {
    try {
      await api.deleteBacktest(id)
      items.value = items.value.filter(r => (r.run_id ?? r.id) !== id)
      delete progressMap[id]
    } catch (e) { console.error(e) }
  }

  return { items, current, progress, progressMap, loading, fetchAll, fetchOne, fetchProgress, getProgress, run, estimate, deleteRun }
})
