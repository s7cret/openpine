import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAchievements, type AchievementItem, type AchievementSummary } from '@/api/client'

export const useAchievementsStore = defineStore('achievements', () => {
  const items = ref<AchievementItem[]>([])
  const summary = ref<AchievementSummary | null>(null)
  const loading = ref(false)
  const error = ref('')
  const backendOk = ref(false)

  async function fetchAll() {
    loading.value = true
    error.value = ''
    try {
      const { data } = await getAchievements(false)
      items.value = data.items
      summary.value = data.summary
      backendOk.value = true
    } catch (e: any) {
      backendOk.value = false
      error.value = e?.response?.data?.detail ?? e?.message ?? 'Achievements fetch failed'
    } finally {
      loading.value = false
    }
  }

  return { items, summary, loading, error, backendOk, fetchAll }
})
