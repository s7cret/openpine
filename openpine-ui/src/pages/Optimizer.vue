<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import api from '@/api/client'

const { t } = useI18n()
const strategyId = ref('')
const trials = ref(10)
const semanticProfile = ref('')
const allowLegacy = ref(false)
const error = ref('')
const result = ref('')

const canDryRun = computed(
  () =>
    Boolean(strategyId.value) &&
    Boolean(semanticProfile.value) &&
    (semanticProfile.value !== 'legacy_4x' || allowLegacy.value),
)

async function dryRun() {
  error.value = ''
  result.value = ''
  if (!strategyId.value) {
    error.value = t('optimizer.strategyRequired')
    return
  }
  if (!semanticProfile.value) {
    error.value = t('optimizer.semanticProfileRequired')
    return
  }
  if (semanticProfile.value === 'legacy_4x' && !allowLegacy.value) {
    error.value = t('optimizer.allowLegacyRequired')
    return
  }
  try {
    const { data } = await api.post('/optimizer/dry-run', {
      strategy_id: strategyId.value,
      trials: trials.value,
      semantic_profile: semanticProfile.value,
      allow_legacy: allowLegacy.value,
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
      <label class="text-sm text-gray-300" for="optimizer-semantic-profile">{{ t('optimizer.semanticProfile') }}</label>
      <select
        id="optimizer-semantic-profile"
        v-model="semanticProfile"
        data-testid="optimizer-semantic-profile"
        class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded"
      >
        <option value="">{{ t('optimizer.semanticProfileRequired') }}</option>
        <option value="strict_5x">strict_5x</option>
        <option value="legacy_4x">legacy_4x</option>
      </select>
      <label v-if="semanticProfile === 'legacy_4x'" class="text-sm text-gray-300" for="optimizer-allow-legacy">
        <input id="optimizer-allow-legacy" v-model="allowLegacy" type="checkbox" data-testid="optimizer-allow-legacy" />
        {{ t('optimizer.allowLegacy') }}
      </label>
      <button class="text-sm text-accent-light disabled:cursor-not-allowed disabled:opacity-50" type="button" :disabled="!canDryRun" @click="dryRun">{{ t('optimizer.dryRun') }}</button>
      <p v-if="error" class="text-danger text-sm" role="alert">{{ error }}</p>
      <pre v-if="result" class="text-xs text-gray-400">{{ result }}</pre>
    </section>
    <RouterLink to="/jobs" class="text-sm text-accent-light">{{ t('nav.jobs') }}</RouterLink>
  </div>
</template>
