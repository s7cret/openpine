<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getSettings, updateSettings } from '@/api/client'
import {
  addStableQuoteAsset,
  COMMON_TIMEZONE_OPTIONS,
  IANA_TIMEZONE_COUNT,
  normalizeSettingsPayload,
  removeStableQuoteAsset,
  settingsPayloadToUpdate,
  STABLE_QUOTE_PRESETS,
  timezoneOptionLabel,
  UNIQUE_CURRENT_UTC_OFFSET_COUNT,
  type SettingsFormState,
  type TimezoneOption,
} from '@/lib/settings'

const loading = ref(false)
const saving = ref(false)
const status = ref('')
const error = ref('')
const stableQuoteInput = ref('')
const form = ref<SettingsFormState | null>(null)

const enabledTimeframes = computed(() => form.value?.timeframes ?? [])
const defaultTimeframeOptions = computed(() => enabledTimeframes.value.length ? enabledTimeframes.value : form.value?.supportedTimeframes ?? [])
const stableQuotes = computed(() => form.value?.stableQuoteAssets ?? [])
const timezoneOptions = computed<TimezoneOption[]>(() => {
  const current = form.value?.timezone
  if (!current || COMMON_TIMEZONE_OPTIONS.some((option) => option.value === current)) return COMMON_TIMEZONE_OPTIONS
  return [...COMMON_TIMEZONE_OPTIONS, { value: current, label: timezoneOptionLabel(current) }]
})
const resolvedTimezoneLabel = computed(() => form.value ? timezoneOptionLabel(form.value.timezone) : '')

async function load() {
  loading.value = true
  error.value = ''
  status.value = ''
  try {
    const { data } = await getSettings()
    form.value = normalizeSettingsPayload(data)
    stableQuoteInput.value = ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail ?? e?.message ?? 'Failed to load settings'
  } finally {
    loading.value = false
  }
}

function toggleTimeframe(tf: string) {
  if (!form.value) return
  const set = new Set(form.value.timeframes)
  if (set.has(tf)) set.delete(tf)
  else set.add(tf)
  form.value.timeframes = form.value.supportedTimeframes.filter((item) => set.has(item))
  if (!form.value.timeframes.includes(form.value.defaultTimeframe)) {
    form.value.defaultTimeframe = form.value.timeframes[0] ?? tf
  }
}

function addStableQuote(asset: string) {
  if (!form.value) return
  form.value.stableQuoteAssets = addStableQuoteAsset(form.value.stableQuoteAssets, asset)
}

function addStableQuoteFromInput() {
  const asset = stableQuoteInput.value.trim()
  if (!asset) return
  addStableQuote(asset)
  stableQuoteInput.value = ''
}

function removeStableQuote(asset: string) {
  if (!form.value) return
  form.value.stableQuoteAssets = removeStableQuoteAsset(form.value.stableQuoteAssets, asset)
}

async function save() {
  if (!form.value) return
  saving.value = true
  error.value = ''
  status.value = ''
  try {
    const payload = settingsPayloadToUpdate(form.value)
    const { data } = await updateSettings(payload)
    form.value = normalizeSettingsPayload(data)
    stableQuoteInput.value = ''
    status.value = 'Settings saved. New defaults will be used by symbol search, strategy creation, and data health views.'
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    error.value = typeof detail === 'string' ? detail : JSON.stringify(detail ?? e?.message ?? 'Failed to save settings')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-4">
    <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-lg font-semibold text-gray-200">⚙️ Settings</h1>
        <p class="text-sm text-gray-500">Runtime-safe OpenPine settings exposed without secrets.</p>
      </div>
      <button
        class="self-start rounded-lg bg-dark-700 px-3 py-2 text-sm text-gray-300 hover:bg-dark-600 disabled:opacity-50"
        :disabled="loading"
        @click="load"
      >
        Refresh
      </button>
    </div>

    <div v-if="error" class="rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
      {{ error }}
    </div>
    <div v-if="status" class="rounded-xl border border-success/40 bg-success/10 px-4 py-3 text-sm text-success">
      {{ status }}
    </div>
    <div v-if="loading && !form" class="rounded-xl border border-dark-500 bg-dark-800 px-4 py-8 text-center text-sm text-gray-500">
      Loading settings...
    </div>

    <form v-if="form" class="space-y-4" @submit.prevent="save">
      <section class="rounded-xl border border-dark-500 bg-dark-800 p-4">
        <div class="flex flex-col gap-1 border-b border-dark-600 pb-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 class="text-sm font-semibold text-gray-200">Time & chart defaults</h2>
            <p class="text-xs text-gray-500">Used by date rendering, strategy form defaults, and future scheduler views.</p>
          </div>
          <div class="text-xs text-gray-500">Resolved: {{ resolvedTimezoneLabel }}</div>
        </div>

        <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <label class="block">
            <span class="text-xs uppercase tracking-wide text-gray-500">Timezone</span>
            <select
              v-model="form.timezone"
              class="mt-1 w-full rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none"
            >
              <option v-for="timezone in timezoneOptions" :key="timezone.value" :value="timezone.value">
                {{ timezone.label }}
              </option>
            </select>
            <span class="mt-1 block text-xs text-gray-500">
              Popular UTC offsets only: {{ timezoneOptions.length }} shown / {{ UNIQUE_CURRENT_UTC_OFFSET_COUNT }} unique current offsets / {{ IANA_TIMEZONE_COUNT }} IANA zones.
            </span>
          </label>

          <label class="block">
            <span class="text-xs uppercase tracking-wide text-gray-500">Default timeframe</span>
            <select
              v-model="form.defaultTimeframe"
              class="mt-1 w-full rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none"
            >
              <option v-for="tf in defaultTimeframeOptions" :key="tf" :value="tf">{{ tf }}</option>
            </select>
            <span class="mt-1 block text-xs text-gray-500">Used as the default in new strategy forms.</span>
          </label>
        </div>
      </section>

      <section class="rounded-xl border border-dark-500 bg-dark-800 p-4">
        <div class="border-b border-dark-600 pb-3">
          <h2 class="text-sm font-semibold text-gray-200">Market data catalog</h2>
          <p class="text-xs text-gray-500">Controls which timeframes and pairs the UI surfaces first. This does not hide explicitly stored data.</p>
        </div>

        <div class="mt-4 space-y-4">
          <div>
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-xs uppercase tracking-wide text-gray-500">Enabled timeframes</span>
              <span class="text-xs text-gray-500">{{ enabledTimeframes.join(', ') }}</span>
            </div>
            <div class="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
              <button
                v-for="tf in form.supportedTimeframes"
                :key="tf"
                type="button"
                :class="[
                  form.timeframes.includes(tf)
                    ? 'border-accent bg-accent/20 text-accent-light'
                    : 'border-dark-500 bg-dark-700 text-gray-400 hover:bg-dark-600',
                  'rounded-lg border px-2 py-2 text-sm font-mono transition-colors'
                ]"
                @click="toggleTimeframe(tf)"
              >
                {{ tf }}
              </button>
            </div>
            <p class="mt-2 text-xs text-gray-500">Includes 3m. Backend validates the list before saving.</p>
          </div>

          <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <label class="flex items-start gap-3 rounded-xl border border-dark-600 bg-dark-700/60 p-3">
              <input
                v-model="form.stableQuotesOnly"
                type="checkbox"
                class="mt-1 h-4 w-4 rounded border-dark-500 bg-dark-700 text-accent focus:ring-accent"
              />
              <span>
                <span class="block text-sm font-medium text-gray-200">Show only stablecoin pairs by default</span>
                <span class="block text-xs text-gray-500">Enable to choose which stable quote assets the UI prioritizes.</span>
              </span>
            </label>

            <div v-if="form.stableQuotesOnly" class="rounded-xl border border-dark-600 bg-dark-700/60 p-3">
              <div class="flex items-center justify-between gap-3">
                <span class="text-xs uppercase tracking-wide text-gray-500">Stable quote assets</span>
                <span class="text-xs text-gray-500">{{ stableQuotes.length }} selected</span>
              </div>

              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  v-for="asset in stableQuotes"
                  :key="asset"
                  type="button"
                  class="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-sm font-medium text-accent-light hover:border-danger/50 hover:bg-danger/10 hover:text-danger"
                  :aria-label="`Remove ${asset}`"
                  @click="removeStableQuote(asset)"
                >
                  <span>{{ asset }}</span>
                  <span aria-hidden="true">×</span>
                </button>
                <span v-if="!stableQuotes.length" class="text-sm text-warning">No stable quote assets selected.</span>
              </div>

              <div class="mt-3 flex flex-col gap-2 sm:flex-row">
                <input
                  v-model.trim="stableQuoteInput"
                  class="min-w-0 flex-1 rounded-lg border border-dark-500 bg-dark-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-accent focus:outline-none"
                  placeholder="Add stablecoin, e.g. PYUSD"
                  @keydown.enter.prevent="addStableQuoteFromInput"
                />
                <button type="button" class="rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-200 hover:bg-dark-500" @click="addStableQuoteFromInput">
                  Add
                </button>
              </div>

              <div class="mt-2 flex flex-wrap gap-1.5">
                <button
                  v-for="asset in STABLE_QUOTE_PRESETS"
                  :key="asset"
                  type="button"
                  class="rounded bg-dark-600 px-2 py-1 text-xs text-gray-300 hover:bg-dark-500 disabled:cursor-not-allowed disabled:opacity-40"
                  :disabled="stableQuotes.includes(asset)"
                  @click="addStableQuote(asset)"
                >
                  + {{ asset }}
                </button>
              </div>
            </div>
          </div>

          <label class="block max-w-xs">
            <span class="text-xs uppercase tracking-wide text-gray-500">Symbol search limit</span>
            <input
              v-model.number="form.symbolSearchLimit"
              type="number"
              min="1"
              max="500"
              class="mt-1 w-full rounded-lg border border-dark-500 bg-dark-700 px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none"
            />
            <span class="mt-1 block text-xs text-gray-500">Backend max result count per `/api/data/symbols` query.</span>
          </label>
        </div>
      </section>

      <div class="sticky bottom-0 -mx-4 border-t border-dark-500 bg-dark-900/95 px-4 py-3 backdrop-blur sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:p-0">
        <div class="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <button type="button" class="rounded-lg bg-dark-700 px-4 py-2 text-sm text-gray-300 hover:bg-dark-600" @click="load">
            Reset
          </button>
          <button type="submit" :disabled="saving || !form.timeframes.length" class="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-light disabled:opacity-50">
            {{ saving ? 'Saving...' : 'Save Settings' }}
          </button>
        </div>
      </div>
    </form>
  </div>
</template>
