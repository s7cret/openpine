import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import * as api from '@/api/client'

export const useBacktestsStore = defineStore('backtests', () => {
  const items = ref<any[]>([])
  const current = ref<any>(null)
  const progress = ref<any>(null)
  const progressMap = reactive<Record<string, any>>({})
  const loading = ref(false)
  const error = ref('')
  let pendingRunFingerprint = ''
  let pendingRunKey = ''

  function errorMessage(cause: any, fallback: string) {
    return cause?.response?.data?.detail ?? cause?.message ?? fallback
  }

  async function fetchAll() {
    loading.value = true
    error.value = ''
    try {
      const { data } = await api.getBacktests()
      items.value = Array.isArray(data) ? data : data?.runs ?? []
    } catch (cause) {
      error.value = errorMessage(cause, 'Backtests fetch failed')
    } finally {
      loading.value = false
    }
  }

  async function fetchOne(id: string) {
    error.value = ''
    try {
      const { data } = await api.getBacktest(id)
      current.value = data
    } catch (cause) { error.value = errorMessage(cause, 'Backtest detail load failed') }
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
    error.value = ''
    const fingerprint = JSON.stringify(data, Object.keys(data).sort())
    if (!pendingRunKey || fingerprint !== pendingRunFingerprint) {
      pendingRunFingerprint = fingerprint
      pendingRunKey = crypto.randomUUID()
    }
    const requestKey = pendingRunKey
    try {
      const res = await api.runBacktest(data, requestKey)
      if (pendingRunKey === requestKey) {
        pendingRunFingerprint = ''
        pendingRunKey = ''
      }
      return res.data
    } catch (cause) { error.value = errorMessage(cause, 'Backtest start failed'); return null }
  }

  async function estimate(data: { strategy_id: string; from_time: string; to_time: string }) {
    try {
      const res = await api.estimateBacktest(data)
      return res.data
    } catch (e) { console.error(e); return null }
  }

  async function deleteRun(id: string) {
    error.value = ''
    try {
      await api.deleteBacktest(id)
      items.value = items.value.filter(r => (r.run_id ?? r.id) !== id)
      delete progressMap[id]
    } catch (cause) { error.value = errorMessage(cause, 'Backtest delete failed') }
  }

  async function controlRun(id: string, action: string) {
    error.value = ''
    try {
      const res = await api.controlBacktest(id, action)
      await fetchProgress(id)
      await fetchAll()
      return res.data
    } catch (cause) { error.value = errorMessage(cause, `Backtest ${action} failed`); return null }
  }

  return { items, current, progress, progressMap, loading, error, fetchAll, fetchOne, fetchProgress, getProgress, run, estimate, deleteRun, controlRun }
})
