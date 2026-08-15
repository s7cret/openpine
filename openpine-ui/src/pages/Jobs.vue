<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { compareJobs, type JobCompareResult } from '@/api/client'
import { useJobsStore } from '@/stores/jobs'
import { formatUtcMs } from '@/lib/utcMs'

const { t } = useI18n()
const store = useJobsStore()
const leftId = ref('')
const rightId = ref('')
const compareResult = ref<JobCompareResult | null>(null)
const compareError = ref('')

onMounted(() => {
  void store.fetchList()
})

async function runCompare() {
  compareError.value = ''
  compareResult.value = null
  if (!leftId.value || !rightId.value) {
    compareError.value = t('jobs.compareMissing')
    return
  }
  try {
    const { data } = await compareJobs(leftId.value, rightId.value)
    compareResult.value = data
  } catch (exc: any) {
    compareError.value = exc?.response?.data?.detail ?? exc?.message ?? t('jobs.missing')
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-gray-100">{{ t('jobs.title') }}</h1>
      <button class="text-sm text-accent-light" type="button" @click="store.fetchList()">
        {{ t('common.refresh') }}
      </button>
    </div>
    <section class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3" data-testid="jobs-compare">
      <h2 class="text-sm font-medium text-gray-200">{{ t('jobs.compareTitle') }}</h2>
      <div class="flex flex-wrap gap-2">
        <input v-model="leftId" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" :placeholder="t('jobs.compareLeft')" />
        <input v-model="rightId" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" :placeholder="t('jobs.compareRight')" />
        <button class="text-sm text-accent-light" type="button" @click="runCompare">{{ t('jobs.compareAction') }}</button>
      </div>
      <p v-if="compareError" class="text-danger text-sm" role="alert">{{ compareError }}</p>
      <p
        v-else-if="compareResult?.warning"
        class="text-warning text-sm"
        role="alert"
        data-testid="jobs-compare-warning"
      >
        {{ compareResult.code === 'SEMANTIC_PROFILE_MISSING' ? t('jobs.compareMissing') : t('jobs.compareMismatch') }}
      </p>
    </section>
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
