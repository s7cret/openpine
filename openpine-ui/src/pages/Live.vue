<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useStrategiesStore } from '@/stores/strategies'
import api from '@/api/client'

const { t, te } = useI18n()
const route = useRoute()
const st = useStrategiesStore()
const strategyId = ref(String(route.params.strategyId ?? ''))
const status = ref('')
const loading = ref(false)

const title = computed(() => (te('nav.live') ? t('nav.live') : 'Live'))
const strategies = computed(() => st.items.filter((item: { archived?: boolean }) => !item.archived))

onMounted(async () => {
  await st.fetchAll()
  try {
    const { data } = await api.get('/live/status')
    status.value = `Live status: ${data.mode}. Enabled=${data.live_enabled}.`
  } catch (e: any) {
    status.value = e?.response?.data?.detail ?? e?.message ?? 'Live status failed'
  }
})

async function stop() {
  if (!strategyId.value) return
  loading.value = true
  try {
    await api.post('/live/stop', { strategy_id: strategyId.value })
    status.value = te('live.stopped') ? t('live.stopped') : 'Strategy stop requested. Live remains deny-by-default.'
  } catch (e: any) {
    status.value = e?.response?.data?.detail ?? e?.message ?? 'Stop failed'
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
        {{ te('live.placeholder') ? t('live.placeholder') : 'Paper/live controls. This page never starts live on mount.' }}
      </p>
    </div>
    <div class="grid gap-3 rounded-xl border border-dark-500 bg-dark-800 p-4 md:grid-cols-2">
      <label class="text-sm text-gray-300">
        {{ te('optimizer.selectStrategy') ? t('optimizer.selectStrategy') : 'Strategy' }}
        <select v-model="strategyId" class="mt-1 w-full rounded-lg bg-dark-700 px-3 py-2 text-gray-100">
          <option value="">-</option>
          <option v-for="item in strategies" :key="item.strategy_id ?? item.id" :value="item.strategy_id ?? item.id">
            {{ item.name ?? item.strategy_id ?? item.id }}
          </option>
        </select>
      </label>
      <div class="flex items-end">
        <button class="rounded-lg bg-dark-600 px-4 py-2 text-sm text-gray-100" :disabled="loading || !strategyId" @click="stop">
          Stop
        </button>
      </div>
    </div>
    <p class="text-sm text-amber-300" role="status">{{ status }}</p>
  </section>
</template>
