import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getDashboard, getPineFiles } from '@/api/client'
import api from '@/api/client'

export const useDashboardStore = defineStore('dashboard', () => {
  const stats = ref<any>(null)
  const pineCount = ref(0)
  const strategiesCount = ref(0)
  const enabledCount = ref(0)
  const errorCount = ref(0)
  const cacheInfo = ref<any>(null)
  const loading = ref(false)
  const backendOk = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      // Dashboard (strategies, jobs, uptime)
      const { data } = await getDashboard()
      stats.value = data
      strategiesCount.value = (data.strategies ?? []).length
      enabledCount.value = (data.strategies ?? []).filter((s: any) => s.enabled).length
      errorCount.value = (data.jobs?.failed ?? 0)
      backendOk.value = true
    } catch (e) {
      backendOk.value = false
      console.error('Dashboard fetch failed', e)
    }

    try {
      // Pine files count
      const { data: pineData } = await getPineFiles()
      const files = Array.isArray(pineData) ? pineData : pineData?.sources ?? []
      pineCount.value = files.length
    } catch (e) { /* ignore */ }

    try {
      // Cache status (instruments with data)
      const { data: cacheData } = await api.get('/data/cache')
      cacheInfo.value = cacheData
    } catch (e) { /* ignore */ }

    loading.value = false
  }

  return { stats, pineCount, strategiesCount, enabledCount, errorCount, cacheInfo, loading, backendOk, fetchAll }
})
