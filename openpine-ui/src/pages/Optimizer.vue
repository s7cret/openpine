<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

const { t } = useI18n()
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('optimizer.title') }}</h1>
    <p class="text-sm text-warning" role="status">{{ t('optimizer.notComplete') }}</p>
    <p class="text-sm text-gray-400">{{ t('optimizer.dryRunOnly') }}</p>
    <RouterLink to="/jobs" class="text-sm text-accent-light">{{ t('nav.jobs') }}</RouterLink>
  </div>
</template>
