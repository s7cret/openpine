<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useJobsStore } from '@/stores/jobs'
import { formatUtcMs } from '@/lib/utcMs'

const { t } = useI18n()
const store = useJobsStore()

onMounted(() => {
  void store.fetchList()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-gray-100">{{ t('jobs.title') }}</h1>
      <button class="text-sm text-accent-light" type="button" @click="store.fetchList()">
        {{ t('common.refresh') }}
      </button>
    </div>
    <p v-if="store.error" class="text-danger text-sm" role="alert">{{ store.error }}</p>
    <p v-else-if="store.loading" class="text-gray-500 text-sm" aria-live="polite">{{ t('common.loading') }}</p>
    <p v-else-if="!store.items.length" class="text-gray-500 text-sm">{{ t('jobs.empty') }}</p>
    <div v-else class="bg-dark-800 rounded-xl border border-dark-500 divide-y divide-dark-600">
      <RouterLink
        v-for="job in store.items"
        :key="job.job_id"
        :to="{ name: 'job-detail', params: { jobId: job.job_id } }"
        class="flex items-center justify-between px-4 py-3 text-sm hover:bg-dark-700"
      >
        <span class="font-mono text-gray-300">{{ job.job_id }}</span>
        <span class="text-gray-400">{{ job.kind }}</span>
        <span class="text-gray-200">{{ job.state }}</span>
        <span class="text-gray-500">{{ formatUtcMs(job.created_at_utc_ms) }}</span>
      </RouterLink>
    </div>
  </div>
</template>
