<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getVersionManifest } from '@/api/client'
import { setApiToken } from '@/api/auth'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; unlocked: [] }>()
const { t } = useI18n()
const token = ref('')
const error = ref('')
const submitting = ref(false)
const tokenInput = ref<HTMLInputElement | null>(null)

watch(() => props.open, async (open) => {
  if (!open) return
  error.value = ''
  await nextTick()
  tokenInput.value?.focus()
})

onMounted(() => {
  if (props.open) tokenInput.value?.focus()
})

function close() {
  if (!submitting.value) emit('close')
}

async function unlock() {
  const value = token.value.trim()
  if (!value) {
    error.value = t('app.tokenRequired')
    return
  }
  submitting.value = true
  error.value = ''
  setApiToken(value)
  try {
    await getVersionManifest()
    token.value = ''
    emit('unlocked')
  } catch (cause: any) {
    setApiToken('')
    error.value = cause?.response?.data?.detail ?? cause?.message ?? t('app.unlockFailed')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4"
      role="presentation"
      @click.self="close"
      @keydown.esc="close"
    >
      <form
        class="w-full max-w-md rounded-2xl border border-dark-500 bg-dark-800 p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-unlock-title"
        @submit.prevent="unlock"
      >
        <div class="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="auth-unlock-title" class="text-lg font-semibold text-gray-100">{{ t('app.unlockTitle') }}</h2>
            <p class="mt-1 text-sm text-gray-400">{{ t('app.unlockDescription') }}</p>
          </div>
          <button type="button" class="rounded p-1 text-gray-400 hover:bg-dark-600" :aria-label="t('common.close')" @click="close">✕</button>
        </div>

        <label class="block text-sm text-gray-300" for="openpine-api-token">{{ t('app.tokenLabel') }}</label>
        <input
          id="openpine-api-token"
          ref="tokenInput"
          v-model="token"
          type="password"
          autocomplete="current-password"
          class="mt-2 w-full rounded-lg border border-dark-500 bg-dark-900 px-3 py-2 text-gray-100 outline-none focus:border-accent"
          :disabled="submitting"
        />
        <p v-if="error" class="mt-3 text-sm text-danger" role="alert">{{ error }}</p>

        <div class="mt-5 flex justify-end gap-2">
          <button type="button" class="rounded-lg bg-dark-600 px-4 py-2 text-sm text-gray-300 hover:bg-dark-500" :disabled="submitting" @click="close">
            {{ t('app.dismiss') }}
          </button>
          <button type="submit" class="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/80 disabled:opacity-60" :disabled="submitting">
            {{ submitting ? t('common.loading') : t('app.unlock') }}
          </button>
        </div>
      </form>
    </div>
  </teleport>
</template>
