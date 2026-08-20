<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import api from '@/api/client'

interface ParameterRow {
  id: number
  name: string
  type: 'int' | 'float'
  default: number
  min: number
  max: number
  step: number
}

interface OptimizerSearchResult {
  optimization_id: string
  status: string
  trials_requested: number
  trials_completed: number
  champion: { params: Record<string, unknown>; metrics: Record<string, number> } | null
  trial_status_counts: Record<string, number>
}

const { t } = useI18n()
const strategyId = ref('')
const fromTime = ref('')
const toTime = ref('')
const trials = ref(10)
const objective = ref('net_profit')
const semanticProfile = ref('')
const allowLegacy = ref(false)
const error = ref('')
const result = ref<OptimizerSearchResult | null>(null)
const running = ref(false)
let nextParameterId = 2
const parameterRows = ref<ParameterRow[]>([
  { id: 1, name: '', type: 'int', default: 1, min: 1, max: 10, step: 1 },
])

function optimizerValidationMessage(): string {
  if (!strategyId.value.trim()) return t('optimizer.strategyRequired')
  if (!fromTime.value || !toTime.value) return t('optimizer.rangeRequired')
  const from = Date.parse(fromTime.value)
  const to = Date.parse(toTime.value)
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
    return t('optimizer.rangeInvalid')
  }
  if (!Number.isInteger(trials.value) || trials.value < 1 || trials.value > 100) {
    return t('optimizer.trialsInvalid')
  }
  if (!parameterRows.value.length) return t('optimizer.parameterRequired')
  const names = parameterRows.value.map((parameter) => parameter.name)
  if (new Set(names).size !== names.length) return t('optimizer.parameterDuplicate')
  for (const parameter of parameterRows.value) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(parameter.name)) {
      return t('optimizer.parameterNameInvalid')
    }
    const values = [parameter.default, parameter.min, parameter.max, parameter.step]
    if (
      !values.every(Number.isFinite) ||
      (parameter.type === 'int' && !values.every(Number.isInteger)) ||
      parameter.step <= 0 ||
      parameter.max < parameter.min
    ) {
      return t('optimizer.parameterRangeInvalid')
    }
  }
  if (!semanticProfile.value) return t('optimizer.semanticProfileRequired')
  if (semanticProfile.value === 'legacy_4x' && !allowLegacy.value) {
    return t('optimizer.allowLegacyRequired')
  }
  return ''
}

const validationMessage = computed(optimizerValidationMessage)
const isSearchDisabled = computed(() => running.value || Boolean(validationMessage.value))

function addParameter() {
  parameterRows.value.push({
    id: nextParameterId++,
    name: '',
    type: 'int',
    default: 1,
    min: 1,
    max: 10,
    step: 1,
  })
}

function removeParameter(id: number) {
  parameterRows.value = parameterRows.value.filter((parameter) => parameter.id !== id)
}

async function runSearch() {
  error.value = ''
  result.value = null
  const validation = optimizerValidationMessage()
  if (validation) {
    error.value = validation
    return
  }
  running.value = true
  try {
    const parameters = parameterRows.value.map((parameter) => ({
      name: parameter.name,
      type: parameter.type,
      default: parameter.type === 'int' ? Math.trunc(parameter.default) : parameter.default,
      min: parameter.type === 'int' ? Math.trunc(parameter.min) : parameter.min,
      max: parameter.type === 'int' ? Math.trunc(parameter.max) : parameter.max,
      step: parameter.type === 'int' ? Math.trunc(parameter.step) : parameter.step,
    }))
    const { data } = await api.post<OptimizerSearchResult>('/optimizer/search', {
      strategy_id: strategyId.value.trim(),
      from_time: new Date(fromTime.value).toISOString(),
      to_time: new Date(toTime.value).toISOString(),
      trials: trials.value,
      objective: objective.value,
      parameters,
      semantic_profile: semanticProfile.value,
      allow_legacy: allowLegacy.value,
    })
    result.value = data
  } catch (exc: unknown) {
    error.value = (exc as { message?: string })?.message ?? t('optimizer.failed')
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold text-gray-100">{{ t('optimizer.title') }}</h1>
    <p class="text-sm text-gray-400">{{ t('optimizer.description') }}</p>

    <section
      class="space-y-4 rounded-xl border border-dark-500 bg-dark-800 p-4"
      data-testid="optimizer-search"
    >
      <div class="grid gap-3 md:grid-cols-2">
        <label class="space-y-1 text-sm text-gray-300" for="opt-strategy">
          <span>{{ t('optimizer.strategyLabel') }}</span>
          <input id="opt-strategy" v-model="strategyId" class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100" />
        </label>
        <label class="space-y-1 text-sm text-gray-300" for="opt-trials">
          <span>{{ t('optimizer.trialsLabel') }}</span>
          <input id="opt-trials" v-model.number="trials" type="number" min="1" max="100" class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100" />
        </label>
        <label class="space-y-1 text-sm text-gray-300" for="opt-from">
          <span>{{ t('optimizer.fromLabel') }}</span>
          <input id="opt-from" v-model="fromTime" type="datetime-local" class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100" />
        </label>
        <label class="space-y-1 text-sm text-gray-300" for="opt-to">
          <span>{{ t('optimizer.toLabel') }}</span>
          <input id="opt-to" v-model="toTime" type="datetime-local" class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100" />
        </label>
        <label class="space-y-1 text-sm text-gray-300" for="opt-objective">
          <span>{{ t('optimizer.objectiveLabel') }}</span>
          <select id="opt-objective" v-model="objective" class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100">
            <option value="net_profit">net_profit</option>
            <option value="profit_factor">profit_factor</option>
            <option value="sharpe_ratio">sharpe_ratio</option>
            <option value="max_drawdown_percent">max_drawdown_percent</option>
          </select>
        </label>
        <label class="space-y-1 text-sm text-gray-300" for="optimizer-semantic-profile">
          <span>{{ t('optimizer.semanticProfile') }}</span>
          <select
            id="optimizer-semantic-profile"
            v-model="semanticProfile"
            data-testid="optimizer-semantic-profile"
            class="w-full rounded bg-dark-700 px-2 py-1 text-gray-100"
          >
            <option value="">{{ t('optimizer.semanticProfileRequired') }}</option>
            <option value="strict_5x">strict_5x</option>
            <option value="legacy_4x">legacy_4x</option>
          </select>
        </label>
      </div>

      <label v-if="semanticProfile === 'legacy_4x'" class="text-sm text-gray-300" for="optimizer-allow-legacy">
        <input id="optimizer-allow-legacy" v-model="allowLegacy" type="checkbox" data-testid="optimizer-allow-legacy" />
        {{ t('optimizer.allowLegacy') }}
      </label>

      <div class="space-y-2">
        <div class="flex items-center justify-between gap-3">
          <h2 class="text-sm font-semibold text-gray-200">{{ t('optimizer.parameterSpace') }}</h2>
          <button type="button" class="text-sm text-accent-light" @click="addParameter">
            {{ t('optimizer.addParameter') }}
          </button>
        </div>
        <div
          v-for="parameter in parameterRows"
          :key="parameter.id"
          class="grid gap-2 rounded border border-dark-500 p-3 sm:grid-cols-2 lg:grid-cols-7"
        >
          <input v-model="parameter.name" data-testid="optimizer-parameter-name" :aria-label="t('optimizer.nameLabel')" :placeholder="t('optimizer.nameLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100" />
          <select v-model="parameter.type" :aria-label="t('optimizer.typeLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100">
            <option value="int">int</option>
            <option value="float">float</option>
          </select>
          <input v-model.number="parameter.default" type="number" :aria-label="t('optimizer.defaultLabel')" :placeholder="t('optimizer.defaultLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100" />
          <input v-model.number="parameter.min" type="number" :aria-label="t('optimizer.minLabel')" :placeholder="t('optimizer.minLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100" />
          <input v-model.number="parameter.max" type="number" :aria-label="t('optimizer.maxLabel')" :placeholder="t('optimizer.maxLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100" />
          <input v-model.number="parameter.step" type="number" :aria-label="t('optimizer.stepLabel')" :placeholder="t('optimizer.stepLabel')" class="rounded bg-dark-700 px-2 py-1 text-sm text-gray-100" />
          <button type="button" class="text-sm text-danger" @click="removeParameter(parameter.id)">
            {{ t('optimizer.removeParameter') }}
          </button>
        </div>
      </div>

      <p v-if="validationMessage" class="text-sm text-warning" role="status">{{ validationMessage }}</p>
      <button
        class="text-sm text-accent-light disabled:cursor-not-allowed disabled:opacity-50"
        type="button"
        data-testid="optimizer-run-search"
        :disabled="isSearchDisabled"
        @click="runSearch"
      >
        {{ running ? t('optimizer.running') : t('optimizer.runSearch') }}
      </button>
      <p v-if="error" class="text-sm text-danger" role="alert">{{ error }}</p>
    </section>

    <section v-if="result" class="space-y-2 rounded-xl border border-dark-500 bg-dark-800 p-4" data-testid="optimizer-result">
      <h2 class="font-semibold text-gray-100">{{ t('optimizer.resultTitle') }}</h2>
      <p class="text-sm text-gray-300">{{ t('optimizer.status') }}: {{ result.status }}</p>
      <p class="text-sm text-gray-300">
        {{ t('optimizer.trialsCompleted') }}: {{ result.trials_completed }}/{{ result.trials_requested }}
      </p>
      <div v-if="result.champion" class="space-y-1" data-testid="optimizer-champion">
        <h3 class="text-sm font-semibold text-success">{{ t('optimizer.champion') }}</h3>
        <pre class="overflow-x-auto text-xs text-gray-400">{{ JSON.stringify(result.champion, null, 2) }}</pre>
      </div>
      <p v-else class="text-sm text-warning">{{ t('optimizer.noChampion') }}</p>
    </section>

    <RouterLink to="/jobs" class="text-sm text-accent-light">{{ t('nav.jobs') }}</RouterLink>
  </div>
</template>
