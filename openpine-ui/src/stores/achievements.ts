import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { getAchievements, type AchievementItem, type AchievementSummary } from '@/api/client'
import { useLocaleStore } from './locale'

/**
 * Achievements store. The backend is the source of truth for both
 * progress *and* localized copy: the GET endpoint accepts a
 * `locale` query param and returns the per-locale title /
 * description / reward from the achievement_i18n table. We
 * re-fetch automatically when the user switches language so
 * the page reactively renders in the new locale.
 */
export const useAchievementsStore = defineStore('achievements', () => {
  const items = ref<AchievementItem[]>([])
  const summary = ref<AchievementSummary | null>(null)
  const loading = ref(false)
  const error = ref('')
  const backendOk = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null
  let inflight = false
  let started = false

  async function fetchAll() {
    // Reentrancy guard: avoid stacking requests if a previous one
    // is still in flight (e.g. after a locale switch on a slow link).
    if (inflight) return
    inflight = true
    loading.value = true
    error.value = ''
    try {
      const locale = useLocaleStore().current
      const { data } = await getAchievements(locale)
      items.value = data.items
      summary.value = data.summary
      backendOk.value = true
    } catch (e: any) {
      backendOk.value = false
      error.value = e?.response?.data?.detail ?? e?.message ?? 'Achievements fetch failed'
    } finally {
      loading.value = false
      inflight = false
    }
  }

  function startPolling(intervalMs = 5000) {
    if (started) return
    started = true
    fetchAll()
    timer = setInterval(() => fetchAll(), intervalMs)
    // Re-fetch on locale change so titles/descriptions switch instantly.
    // The watch is owned by the store and torn down with stopPolling.
    const localeStore = useLocaleStore()
    watch(
      () => localeStore.current,
      () => fetchAll(),
    )
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    started = false
  }

  return { items, summary, loading, error, backendOk, fetchAll, startPolling, stopPolling }
})
