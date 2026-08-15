<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useJobInboxStore } from '@/stores/jobInbox'

const { t } = useI18n()
const router = useRouter()
const inbox = useJobInboxStore()
</script>

<template>
  <section class="space-y-3">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('nav.jobs') }}</h1>
    <div v-if="!inbox.recent.length" class="rounded-xl border border-dark-500 bg-dark-800 p-4 text-sm text-gray-400">
      {{ t('dashboard.noJobs') }}
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
