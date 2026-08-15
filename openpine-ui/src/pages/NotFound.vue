<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

const { t } = useI18n()
const router = useRouter()
</script>

<template>
  <section class="mx-auto max-w-xl rounded-xl border border-dark-500 bg-dark-800 p-6">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('app.notFoundTitle') }}</h1>
    <p class="mt-2 text-sm text-gray-400">{{ t('app.notFoundDescription') }}</p>
    <button class="mt-4 rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-100" @click="router.push('/dashboard')">
      {{ t('nav.dashboard') }}
    </button>
  </section>
</template>
