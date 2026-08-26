<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api/client'
import MtfSeriesEditor from '@/components/MtfSeriesEditor.vue'
import {
  mtfSeriesValidationKey,
  toMtfSeriesRequests,
  type MtfSeriesRow,
} from '@/lib/mtfSeries'

const { t } = useI18n()
const admission = ref<Record<string, unknown> | null>(null)
const preview = ref<Record<string, unknown> | null>(null)
const strategyId = ref('')
const confirmation = ref('')
const error = ref('')
const startResult = ref('')
const mtfSeries = ref<MtfSeriesRow[]>([])
const mtfValidationMessage = computed(() => {
  const key = mtfSeriesValidationKey(mtfSeries.value)
  return key ? t(key) : ''
})

const canStart = computed(
  () =>
    Boolean(preview.value) &&
    confirmation.value === 'LIVE' &&
    Boolean(strategyId.value) &&
    !mtfValidationMessage.value,
)

onMounted(async () => {
  try {
    const { data } = await api.get('/live/admission')
    admission.value = data
  } catch (exc: any) {
    error.value = exc?.message ?? t('live.loadFailed')
  }
})

async function loadPreview() {
  error.value = ''
  startResult.value = ''
  preview.value = null
  if (!strategyId.value) {
    error.value = t('live.strategyRequired')
    return
  }
  const { data } = await api.get('/live/admission/preview', { params: { strategy_id: strategyId.value } })
  preview.value = data
}

async function startLive() {
  if (!canStart.value || !preview.value) return
  error.value = ''
  const { data } = await api.post('/live/start', {
    strategy_id: strategyId.value,
    preview_hash: preview.value.preview_hash,
    confirmation: confirmation.value,
    idempotency_key: `live-${strategyId.value}-${String(preview.value.preview_hash)}`,
    expires_at_utc_ms: preview.value.expires_at_utc_ms,
    mtf_series: toMtfSeriesRequests(mtfSeries.value),
  })
  startResult.value = JSON.stringify(data)
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('live.title') }}</h1>
    <p class="text-sm text-gray-400">{{ t('live.readonlyHint') }}</p>
    <p v-if="error" class="text-danger text-sm" role="alert">{{ error }}</p>
    <pre v-if="admission" class="bg-dark-800 rounded-xl border border-dark-500 p-4 text-xs text-gray-300">{{ JSON.stringify(admission, null, 2) }}</pre>
    <section class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-2" data-testid="live-confirm">
      <label class="text-sm text-gray-300" for="live-strategy">{{ t('live.strategyLabel') }}</label>
      <input id="live-strategy" v-model="strategyId" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" />
      <button class="text-sm text-accent-light" type="button" @click="loadPreview">{{ t('live.review') }}</button>
      <p v-if="preview" class="text-xs text-gray-400 font-mono">{{ preview.preview_hash }}</p>
      <MtfSeriesEditor v-model="mtfSeries" />
      <p v-if="mtfValidationMessage" class="text-sm text-warning" role="status">
        {{ mtfValidationMessage }}
      </p>
      <label class="text-sm text-gray-300" for="live-confirm">{{ t('live.typeLive') }}</label>
      <input id="live-confirm" v-model="confirmation" class="bg-dark-700 text-gray-100 text-sm px-2 py-1 rounded" data-testid="live-typed-confirm" />
      <button class="text-sm" type="button" :disabled="!canStart" @click="startLive">{{ t('live.start') }}</button>
      <p v-if="startResult" class="text-xs text-gray-400">{{ startResult }}</p>
    </section>
  </div>
</template>
