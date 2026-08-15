<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import api from '@/api/client'

const { t } = useI18n()
const strategyId = ref('')
const trials = ref(10)
const error = ref('')
const result = ref('')

async function dryRun() {
  error.value = ''
  result.value = ''
  if (!strategyId.value) {
    error.value = t('optimizer.strategyRequired')
    return
  }
  try {
    const { data } = await api.post('/optimizer/dry-run', {
      strategy_id: strategyId.value,
      trials: trials.value,
    })
    result.value = JSON.stringify(data)
  } catch (exc: any) {
    error.value = exc?.message ?? t('optimizer.failed')
  }
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('optimizer.title') }}</h1>
    <p class="text-sm text-warning" role="status">{{ t('optimizer.notComplete') }}</p>
    <p class="text-sm text-gray-400">{{ t('optimizer.dryRunOnly') }}</p>
    <section class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-2" data-testid="optimizer-dry-run">
      <label class="text-sm text-gray-300" for="opt-strategy">{{ t('optimizer.strategyLabel') }}</label>
      <input id="opt-strategy" v-model="strategyId" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" />
      <label class="text-sm text-gray-300" for="opt-trials">{{ t('optimizer.trialsLabel') }}</label>
      <input id="opt-trials" v-model.number="trials" type="number" min="1" max="10000" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" />
      <button class="text-sm text-accent-light" type="button" @click="dryRun">{{ t('optimizer.dryRun') }}</button>
      <p v-if="error" class="text-danger text-sm" role="alert">{{ error }}</p>
      <pre v-if="result" class="text-xs text-gray-400">{{ result }}</pre>
    </section>
    <RouterLink to="/jobs" class="text-sm text-accent-light">{{ t('nav.jobs') }}</RouterLink>
  </div>
</template>
