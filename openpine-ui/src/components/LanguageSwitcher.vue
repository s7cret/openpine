<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useLocaleStore } from '@/stores/locale'
import type { LocaleCode } from '@/i18n'

const { t } = useI18n()
const store = useLocaleStore()

function pick(code: LocaleCode) {
  store.change(code)
}
</script>

<template>
  <div class="flex items-center gap-1 rounded-lg border border-dark-500 bg-dark-700 p-0.5 text-xs">
    <button
      v-for="code in store.supported"
      :key="code"
      type="button"
      @click="pick(code)"
      :title="t(`language.${code}`)"
      :class="[
        'rounded-md px-2 py-1 font-semibold uppercase transition',
        store.current === code
          ? 'bg-accent text-white shadow-sm'
          : 'text-gray-400 hover:bg-dark-600 hover:text-gray-200',
      ]"
    >
      {{ code }}
    </button>
  </div>
</template>
