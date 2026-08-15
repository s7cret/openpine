<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useJobsStore } from '@/stores/jobs'
import { formatUtcMs } from '@/lib/utcMs'

const { t } = useI18n()
const route = useRoute()
const store = useJobsStore()
const loadError = ref('')

onMounted(async () => {
  try {
    await store.fetchOne(String(route.params.jobId || ''))
  } catch (exc: any) {
    loadError.value = exc?.response?.data?.detail ?? exc?.message ?? t('jobs.missing')
  }
})

function resultLink(job: NonNullable<typeof store.current>) {
  if (job.kind === 'backtest' && job.run_id) return `/backtests`
  if (job.kind === 'optimize') return `/strategies`
  if (job.kind === 'parity') return `/tv-parity`
  if (job.kind === 'backfill') return `/data`
  if (job.kind === 'compile') return `/pine-files`
  return '/jobs'
}
</script>

<template>
  <div class="space-y-4">
    <RouterLink to="/jobs" class="text-sm text-accent-light">{{ t('common.back') }}</RouterLink>
    <p v-if="loadError" class="text-danger text-sm" role="alert">{{ loadError }}</p>
    <div v-else-if="store.current" class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-2 text-sm">
      <h1 class="text-lg font-semibold text-gray-100">{{ store.current.job_id }}</h1>
      <p class="text-gray-300">{{ store.current.kind }} · {{ store.current.state }}</p>
      <p class="text-gray-500">{{ formatUtcMs(store.current.created_at_utc_ms) }}</p>
      <p v-if="store.current.error_code" class="text-danger">{{ store.current.error_code }}</p>
      <p class="text-gray-400">{{ t('jobs.progress') }}: {{ store.current.progress ?? 0 }}%</p>
      <RouterLink :to="resultLink(store.current)" class="text-accent-light">{{ t('jobs.openResult') }}</RouterLink>
    </div>
  </div>
</template>
