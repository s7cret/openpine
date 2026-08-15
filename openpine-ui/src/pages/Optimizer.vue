<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useStrategiesStore } from '@/stores/strategies'
import api from '@/api/client'

const { t, te } = useI18n()
const st = useStrategiesStore()
const strategyId = ref('')
const trials = ref(20)
const status = ref('')
const loading = ref(false)

const title = computed(() => (te('nav.optimizer') ? t('nav.optimizer') : 'Optimizer'))
const strategies = computed(() => st.items.filter((item: any) => !item.archived))

onMounted(() => { void st.fetchAll() })

async function validate() {
  if (!strategyId.value) {
    status.value = te('optimizer.selectStrategy') ? t('optimizer.selectStrategy') : 'Select a strategy first'
    return
  }
  loading.value = true
  status.value = ''
  try {
    const { data } = await api.post('/optimizer/dry-run', {
      strategy_id: strategyId.value,
      trials: Number(trials.value),
    })
    const ok = (data.status ?? 'ok') === 'valid'
    status.value = ok
      ? `Dry-run only. Config valid for ${data.trials_requested ?? trials.value} trials. Search/champion is not implemented on this API.`
      : `${data.status}: ${data.reason ?? 'invalid'}``
  } catch (e: any) {
    status.value = e?.response?.data?.detail ?? e?.message ?? 'Optimizer validation failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="space-y-4">
    <div>
      <h1 class="text-lg font-semibold text-gray-100">{{ title }}</h1>
      <p class="mt-1 text-sm text-gray-400">
        {{ te('optimizer.placeholder') ? t('optimizer.placeholder') : 'Validate a parameter search before launching trials. Champion promotion is a separate action.' }}
      </p>
    </div>
    <div class="grid gap-3 rounded-xl border border-dark-500 bg-dark-800 p-4 md:grid-cols-3">
      <label class="text-sm text-gray-300">
        Strategy
        <select v-model="strategyId" class="mt-1 w-full rounded-lg bg-dark-700 px-3 py-2 text-gray-100">
          <option value="">—</option>
          <option v-for="item in strategies" :key="item.strategy_id ?? item.id" :value="item.strategy_id ?? item.id">
            {{ item.name ?? item.strategy_id ?? item.id }}
          </option>
        </select>
      </label>
      <label class="text-sm text-gray-300">
        Trials
        <input v-model.number="trials" type="number" min="1" class="mt-1 w-full rounded-lg bg-dark-700 px-3 py-2 text-gray-100" />
      </label>
      <div class="flex items-end">
        <button class="rounded-lg bg-accent px-4 py-2 text-sm text-white disabled:opacity-50" :disabled="loading" @click="validate">
          Validate config
        </button>
      </div>
    </div>
    <p v-if="status" class="text-sm text-gray-300" role="status">{{ status }}</p>
  </section>
</template>
