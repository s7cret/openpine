import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getDashboard, getDataHealth, getDataSummary, getPineFiles } from '@/api/client'

export const useDashboardStore = defineStore('dashboard', () => {
  const stats = ref<any>(null)
  const pineCount = ref(0)
  const strategiesCount = ref(0)
  const enabledCount = ref(0)
  const errorCount = ref(0)
  const cacheInfo = ref<any>(null)
  const dataInfo = ref<any>(null)
  const dataHealth = ref<any>(null)
  const loading = ref(false)
  const backendOk = ref(false)
  const error = ref('')

  function errorMessage(e: any, fallback: string) { return e?.response?.data?.detail ?? e?.message ?? fallback }

  async function fetchAll() {
    loading.value = true
    error.value = ''
    try {
      // Dashboard (strategies, jobs, uptime)
      const { data } = await getDashboard()
      stats.value = data
      strategiesCount.value = (data.strategies ?? []).length
      enabledCount.value = (data.strategies ?? []).filter((s: any) => s.enabled).length
      errorCount.value = (data.jobs?.failed ?? 0)
      backendOk.value = true
    } catch (e: any) {
      backendOk.value = false
      error.value = errorMessage(e, 'Dashboard fetch failed')
    }

    try {
      // Pine files count
      const { data: pineData } = await getPineFiles()
      const files = Array.isArray(pineData) ? pineData : pineData?.sources ?? []
      pineCount.value = files.length
    } catch (e) { /* ignore */ }

    try {
      const { data } = await getDataSummary()
      dataInfo.value = data
      cacheInfo.value = data
    } catch (e) { /* ignore */ }

    try {
      const { data } = await getDataHealth()
      dataHealth.value = data
    } catch (e) { /* ignore */ }

    loading.value = false
  }

  return { stats, pineCount, strategiesCount, enabledCount, errorCount, cacheInfo, dataInfo, dataHealth, loading, backendOk, error, fetchAll }
})
