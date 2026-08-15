<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useDashboardStore } from '@/stores/dashboard'
import { useJobInboxStore, type JobRecord } from '@/stores/jobInbox'

const { t, te } = useI18n()
const router = useRouter()
const dash = useDashboardStore()
const inbox = useJobInboxStore()

const title = computed(() => (te('nav.jobs') ? t('nav.jobs') : 'Jobs'))

onMounted(async () => {
  await dash.fetchAll()
  const recent = dash.stats?.jobs?.recent ?? []
  for (const job of recent) {
    const id = String(job.id ?? job.job_id ?? job.run_id ?? crypto.randomUUID())
    const rec: JobRecord = {
      id,
      kind: job.type === 'backfill' ? 'backfill' : job.type === 'backtest' ? 'backtest' : 'unknown',
      status: job.status ?? 'unknown',
      title: job.type === 'backfill'
        ? `Backfill ${job.input?.symbol ?? job.progress?.detail?.symbol ?? ''}`.trim()
        : String(job.type ?? job.strategy_id ?? id),
      href: job.type === 'backtest' ? `/backtests/${id}` : `/jobs/${id}`,
      updatedAt: Number(job.updated_at ?? Date.now()),
    }
    inbox.upsert(rec)
  }
})
</script>

<template>
  <section class="space-y-3">
    <h1 class="text-lg font-semibold text-gray-100">{{ title }}</h1>
    <div v-if="!inbox.recent.length" class="rounded-xl border border-dark-500 bg-dark-800 p-4 text-sm text-gray-400">
      {{ te('dashboard.noJobs') ? t('dashboard.noJobs') : 'No jobs yet' }}
    </div>
    <button
      v-for="job in inbox.recent"
      :key="job.id"
      class="flex w-full items-center justify-between rounded-xl border border-dark-500 bg-dark-800 px-4 py-3 text-left"
      @click="router.push(job.href)"
    >
      <span class="text-sm text-gray-100">{{ job.title }}</span>
      <span class="text-xs text-gray-400">{{ job.status }}</span>
    </button>
  </section>
</template>
