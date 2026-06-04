import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/client'

export const useStrategiesStore = defineStore('strategies', () => {
  const items = ref<any[]>([])
  const current = ref<any>(null)
  const loading = ref(false)

  function getId(s: any) { return s?.strategy_id ?? s?.id ?? '' }

  async function fetchAll() {
    loading.value = true
    try {
      const { data } = await api.getStrategies()
      items.value = Array.isArray(data) ? data : data?.strategies ?? []
    } catch (e) {
      console.error('Strategies fetch failed', e)
    } finally {
      loading.value = false
    }
  }

  async function fetchOne(id: string) {
    try {
      const { data } = await api.getStrategy(id)
      current.value = data
    } catch (e) { console.error(e) }
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
    } catch (e) { console.error(e) }
  }

  async function create(data: any) {
    const { data: result } = await api.createStrategy(data)
    await fetchAll()
    return result
  }

  async function remove(id: string) {
    try {
      await api.deleteStrategy(id)
      // Optimistic: remove from local list immediately
      items.value = items.value.filter(s => getId(s) !== id)
      // Then sync with server
      await fetchAll()
    } catch (e) { console.error(e) }
  }

  return { items, current, loading, fetchAll, fetchOne, control, create, remove }
})
