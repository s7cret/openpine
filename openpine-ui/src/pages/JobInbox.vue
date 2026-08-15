<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useJobInboxStore } from '@/stores/jobInbox'
import api from '@/api/client'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()
const inbox = useJobInboxStore()
const detailError = ref('')
const detail = ref<Record<string, unknown> | null>(null)

const title = computed(() => (te('nav.jobs') ? t('nav.jobs') : 'Jobs'))
const jobId = computed(() => String(route.params.jobId ?? ''))

onMounted(async () => {
  try {
    await inbox.fetchAll()
    if (jobId.value) {
      const { data } = await api.get(`/jobs/${jobId.value}`)
      detail.value = data
    }
  } catch (e: any) {
    detailError.value = e?.response?.data?.detail ?? e?.message ?? 'Job load failed'
  }
})
</script>

<template>
  <section class="space-y-3">
    <h1 class="text-lg font-semibold text-gray-100">{{ title }}</h1>
    <p v-if="detailError" class="text-sm text-amber-300" role="alert">{{ detailError }}</p>
    <div v-if="jobId && detail" class="rounded-xl border border-dark-500 bg-dark-800 p-4 text-sm text-gray-300">
      <p>{{ detail.job_id }} · {{ detail.kind }} · {{ detail.state }}</p>
    </div>
    <div v-if="!inbox.items.length" class="rounded-xl border border-dark-500 bg-dark-800 p-4 text-sm text-gray-400">
      {{ te('dashboard.noJobs') ? t('dashboard.noJobs') : 'No jobs yet' }}
    </div>
    <button
      v-for="job in inbox.items"
      :key="job.job_id"
      class="block w-full rounded-xl border border-dark-500 bg-dark-800 px-4 py-3 text-left text-sm text-gray-200"
      @click="router.push(job.href)"
    >
      {{ job.kind }} · {{ job.job_id }} · {{ job.state }}
    </button>
  </section>
</template>
