<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAchievementsStore } from '@/stores/achievements'
import type { AchievementItem } from '@/api/client'

// 4-tier filter — 'all' by default
type TierId = 'all' | 'pro' | 'ultra' | 'hyper' | 'apex'

const { t } = useI18n()
const activeTier = ref<TierId>('all')

const store = useAchievementsStore()
onMounted(() => {
  // startPolling handles the initial fetch, the 5s interval, AND
  // re-fetches automatically when the user switches language.
  store.startPolling(5000)
})
onUnmounted(() => {
  store.stopPolling()
})

const TIER_META = computed<Record<Exclude<TierId, 'all'>, { label: string; color: string; ring: string; pill: string; hint: string }>>(() => ({
  pro: {
    label: 'Pro',
    color: 'text-emerald-300',
    ring: 'ring-emerald-500/30',
    pill: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
    hint: t('achievements.tierProHint'),
  },
  ultra: {
    label: 'Ultra',
    color: 'text-violet-300',
    ring: 'ring-violet-500/30',
    pill: 'bg-violet-500/10 text-violet-300 border-violet-500/30',
    hint: t('achievements.tierUltraHint'),
  },
  hyper: {
    label: 'Hyper',
    color: 'text-rose-300',
    ring: 'ring-rose-500/30',
    pill: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
    hint: t('achievements.tierHyperHint'),
  },
  apex: {
    label: 'Apex',
    color: 'text-amber-300',
    ring: 'ring-amber-500/40',
    pill: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
    hint: t('achievements.tierApexHint'),
  },
}))

const visible = computed<AchievementItem[]>(() => {
  if (!store.items) return []
  return activeTier.value === 'all'
    ? store.items
    : store.items.filter((a) => a.tier === activeTier.value)
})

const totals = computed(() => {
  const s = store.summary
  if (!s) {
    return {
      total: 0,
      unlocked: 0,
      byTier: [
        { tier: 'pro' as const, done: 0, of: 0 },
        { tier: 'ultra' as const, done: 0, of: 0 },
        { tier: 'hyper' as const, done: 0, of: 0 },
        { tier: 'apex' as const, done: 0, of: 0 },
      ],
    }
  }
  return {
    total: s.total,
    unlocked: s.unlocked,
    byTier: (['pro', 'ultra', 'hyper', 'apex'] as const).map((tier) => ({
      tier,
      done: s.by_tier[tier]?.done ?? 0,
      of: s.by_tier[tier]?.of ?? 0,
    })),
  }
})

function pct(a: AchievementItem): number {
  return a.progress_pct
}

function isDone(a: AchievementItem): boolean {
  return a.unlocked
}

function fmt(n: number): string {
  if (n >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(1)}T`
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString('en-US')
}

const tierButtons = computed<{ id: TierId; label: string; icon: string; hint: string }[]>(() => [
  { id: 'all',   label: 'All',   icon: '🌐', hint: t('achievements.tierAll') },
  { id: 'pro',   label: 'Pro',   icon: '🟢', hint: TIER_META.value.pro.hint },
  { id: 'ultra', label: 'Ultra', icon: '🟣', hint: TIER_META.value.ultra.hint },
  { id: 'hyper', label: 'Hyper', icon: '🔴', hint: TIER_META.value.hyper.hint },
  { id: 'apex',  label: 'Apex',  icon: '🟠', hint: TIER_META.value.apex.hint },
])
</script>

<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="rounded-2xl border border-dark-500 bg-dark-800 p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="flex items-start gap-3">
          <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/15 text-xl">🏆</div>
          <div>
            <h1 class="text-lg font-semibold text-gray-200">{{ t('achievements.title') }}</h1>
            <p class="mt-1 text-sm text-gray-500">
              {{ totals.unlocked }} / {{ totals.total }} unlocked ·
              Pro {{ totals.byTier[0].done }}/{{ totals.byTier[0].of }},
              Ultra {{ totals.byTier[1].done }}/{{ totals.byTier[1].of }},
              Hyper {{ totals.byTier[2].done }}/{{ totals.byTier[2].of }},
              Apex {{ totals.byTier[3].done }}/{{ totals.byTier[3].of }}
            </p>
          </div>
        </div>
        <div
          :class="[
            'rounded-lg border px-3 py-1.5 text-xs',
            store.backendOk
              ? 'border-dark-500 bg-dark-700 text-gray-400'
              : 'border-danger/40 bg-danger/10 text-danger',
          ]"
        >
          <span v-if="store.loading">{{ t('common.loading') }}</span>
          <span v-else-if="store.backendOk">{{ t('achievements.backendOk', { count: store.items.length }) }}</span>
          <span v-else>{{ t('achievements.offline') }} · {{ store.error || 'fetch failed' }}</span>
        </div>
      </div>
    </div>

    <!-- Tier filter -->
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-5">
      <button
        v-for="b in tierButtons"
        :key="b.id"
        type="button"
        @click="activeTier = b.id"
        :class="[
          'flex flex-col items-start gap-0.5 rounded-xl border px-3 py-2.5 text-left transition',
          activeTier === b.id
            ? 'border-accent/60 bg-dark-700 shadow-[0_0_0_1px_rgba(99,102,241,0.4)]'
            : 'border-dark-500 bg-dark-800 hover:border-dark-400 hover:bg-dark-700',
        ]"
      >
        <div class="flex items-center gap-1.5">
          <span>{{ b.icon }}</span>
          <span
            :class="[
              'text-sm font-semibold',
              b.id === 'all' ? 'text-gray-200' : TIER_META[b.id as Exclude<TierId, 'all'>].color,
            ]"
          >{{ b.label }}</span>
        </div>
        <span class="text-[10px] uppercase tracking-wide text-gray-500">{{ b.hint }}</span>
      </button>
    </div>

    <!-- Achievement grid -->
    <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      <div
        v-for="a in visible"
        :key="a.id"
        :class="[
          'relative rounded-2xl border bg-dark-800 p-4 ring-1 transition',
          isDone(a)
            ? 'border-amber-500/40 ' + TIER_META[a.tier].ring + ' shadow-[0_0_24px_rgba(245,158,11,0.12)]'
            : 'border-dark-500 ' + TIER_META[a.tier].ring,
        ]"
      >
        <!-- Tier pill + done ribbon -->
        <div class="mb-3 flex items-center justify-between">
          <span
            :class="['inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide', TIER_META[a.tier].pill]"
          >
            {{ TIER_META[a.tier].label }}
          </span>
          <span
            v-if="isDone(a)"
            class="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-300"
          >
            {{ t('achievements.unlocked') }}
          </span>
        </div>

        <!-- Title row -->
        <div class="mb-1.5 flex items-center gap-2">
          <span class="text-2xl leading-none">{{ a.icon }}</span>
          <h3 class="text-sm font-semibold text-gray-100">{{ a.title }}</h3>
        </div>
        <p class="mb-3 text-xs leading-relaxed text-gray-400">{{ a.description }}</p>

        <!-- Progress -->
        <div class="space-y-1.5">
          <div class="flex items-center justify-between text-[11px] tabular-nums text-gray-500">
            <span>{{ fmt(a.current) }} / {{ fmt(a.target) }}</span>
            <span :class="[isDone(a) ? 'text-amber-300' : 'text-gray-500']">
              {{ pct(a).toFixed(0) }}%
            </span>
          </div>
          <div class="h-1.5 overflow-hidden rounded-full bg-dark-600">
            <div
              :class="[
                'h-full rounded-full transition-all',
                isDone(a)
                  ? 'bg-gradient-to-r from-amber-400 to-amber-300'
                  : a.tier === 'pro' ? 'bg-emerald-400/70'
                  : a.tier === 'ultra' ? 'bg-violet-400/70'
                  : a.tier === 'hyper' ? 'bg-rose-400/70'
                  : 'bg-amber-400/70',
              ]"
              :style="{ width: pct(a) + '%' }"
            />
          </div>
        </div>

        <!-- Reward -->
        <div class="mt-3 flex items-center gap-1.5 border-t border-dark-600 pt-2.5 text-[11px] text-gray-500">
          <span>🎁</span>
          <span>{{ a.reward }}</span>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div
      v-if="visible.length === 0"
      class="rounded-2xl border border-dashed border-dark-500 bg-dark-800/50 p-8 text-center text-sm text-gray-500"
    >
      <span v-if="store.loading">{{ t('achievements.loadingEmpty') }}</span>
      <span v-else-if="!store.backendOk">{{ t('achievements.backendDown') }}</span>
      <span v-else>{{ t('achievements.noTier') }}</span>
    </div>
  </div>
</template>
