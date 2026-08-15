<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t, te } = useI18n()
const title = computed(() => (te('app.notFound') ? t('app.notFound') : 'Not found'))
</script>

<template>
  <section class="space-y-3">
    <h1 class="text-lg font-semibold text-gray-100">{{ title }}</h1>
  </section>
</template>
