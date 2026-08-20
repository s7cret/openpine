<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { newMtfSeriesRow, type MtfSeriesRow } from '@/lib/mtfSeries'

const props = defineProps<{ modelValue: MtfSeriesRow[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: MtfSeriesRow[]] }>()
const { t } = useI18n()

function addRow() {
  emit('update:modelValue', [...props.modelValue, newMtfSeriesRow()])
}

function updateRow(id: number, field: 'symbol' | 'timeframe', value: string) {
  emit(
    'update:modelValue',
    props.modelValue.map((row) => (row.id === id ? { ...row, [field]: value } : row)),
  )
}

function removeRow(id: number) {
  emit('update:modelValue', props.modelValue.filter((row) => row.id !== id))
}
</script>

<template>
  <section class="space-y-2" data-testid="mtf-series-editor">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-gray-200">{{ t('mtf.title') }}</h3>
        <p class="text-xs text-gray-500">{{ t('mtf.hint') }}</p>
      </div>
      <button type="button" class="shrink-0 text-sm text-accent-light" @click="addRow">
        {{ t('mtf.add') }}
      </button>
    </div>
    <p v-if="modelValue.length === 0" class="text-xs text-gray-500">{{ t('mtf.empty') }}</p>
    <div
      v-for="row in modelValue"
      :key="row.id"
      class="grid gap-2 rounded-lg border border-dark-500 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
      data-testid="mtf-series-row"
    >
      <label class="space-y-1 text-xs text-gray-400">
        <span>{{ t('mtf.symbol') }}</span>
        <input
          :value="row.symbol"
          :placeholder="t('mtf.symbolPlaceholder')"
          class="w-full rounded bg-dark-700 px-2 py-1.5 text-sm text-gray-100"
          data-testid="mtf-series-symbol"
          @input="updateRow(row.id, 'symbol', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <label class="space-y-1 text-xs text-gray-400">
        <span>{{ t('mtf.timeframe') }}</span>
        <input
          :value="row.timeframe"
          :placeholder="t('mtf.timeframePlaceholder')"
          class="w-full rounded bg-dark-700 px-2 py-1.5 text-sm text-gray-100"
          data-testid="mtf-series-timeframe"
          @input="updateRow(row.id, 'timeframe', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <button type="button" class="self-end text-sm text-danger" @click="removeRow(row.id)">
        {{ t('mtf.remove') }}
      </button>
    </div>
  </section>
</template>
