<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'

const { t } = useI18n()
const admission = ref<Record<string, unknown> | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    const { data } = await api.get('/live/admission')
    admission.value = data
  } catch (exc: any) {
    error.value = exc?.message ?? t('live.loadFailed')
  }
})
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('live.title') }}</h1>
    <p class="text-sm text-gray-400">{{ t('live.readonlyHint') }}</p>
    <p v-if="error" class="text-danger text-sm" role="alert">{{ error }}</p>
    <pre v-else-if="admission" class="bg-dark-800 rounded-xl border border-dark-500 p-4 text-xs text-gray-300">{{ JSON.stringify(admission, null, 2) }}</pre>
  </div>
</template>
