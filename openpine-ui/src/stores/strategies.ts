import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/client'

export const useStrategiesStore = defineStore('strategies', () => {
  const items = ref<any[]>([])
  const current = ref<any>(null)
  const loading = ref(false)
  const error = ref('')

  function getId(s: any) { return s?.strategy_id ?? s?.id ?? '' }
  function errorMessage(e: any, fallback: string) { return e?.response?.data?.detail ?? e?.message ?? fallback }

  async function fetchAll() {
    loading.value = true
    error.value = ''
    try {
      const { data } = await api.getStrategies()
      items.value = Array.isArray(data) ? data : data?.strategies ?? []
    } catch (e: any) {
      error.value = errorMessage(e, 'Strategies fetch failed')
    } finally {
      loading.value = false
    }
  }

  async function fetchOne(id: string) {
    try {
      const { data } = await api.getStrategy(id)
      current.value = data
      return data
    } catch (e: any) {
      current.value = null
      error.value = errorMessage(e, 'Strategy detail load failed')
      throw e
    }
  }

  async function control(id: string, action: string) {
    try {
      await api.controlStrategy(id, action)
      // Optimistic: update local immediately
      const idx = items.value.findIndex(s => getId(s) === id)
      if (idx !== -1) {
        if (action === 'start') { items.value[idx].status = 'running'; items.value[idx].enabled = true }
        else if (action === 'pause' || action === 'stop') { items.value[idx].status = 'paused'; items.value[idx].enabled = false }
        else if (action === 'enable') { items.value[idx].enabled = true }
      }
      // Then sync with server
      await fetchAll()
    } catch (e: any) { error.value = errorMessage(e, `Strategy ${action} failed`) }
  }
  async function create(data: any) {
    error.value = ''
    const { data: result } = await api.createStrategy(data)
    await fetchAll()
    return result
  }

  async function remove(id: string) {
    try {
      const preview = await api.previewDeleteStrategy(id).then((r) => r.data).catch(() => null)
      if (preview) {
        const resources = Object.entries(preview.resources ?? {})
          .filter(([, value]) => Number(value) > 0)
          .map(([key, value]) => `${key}: ${value}`)
          .join('\n')
        const ok = confirm(`Delete strategy "${preview.name ?? id}"?\n\nWill delete:\n${resources || 'strategy row only'}\n\nMarket bars deleted: 0`)
        if (!ok) return
      } else if (!confirm(`Delete strategy ${id}? Market bars will not be deleted.`)) {
        return
      }
      await api.deleteStrategy(id)
      // Optimistic: remove from local list immediately
      items.value = items.value.filter(s => getId(s) !== id)
      // Then sync with server
      await fetchAll()
    } catch (e: any) { error.value = errorMessage(e, 'Strategy delete failed') }
  }

  return { items, current, loading, error, fetchAll, fetchOne, control, create, remove }
})
