<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getOrders } from '@/api/client'

interface Toast {
  id: string
  side: string
  symbol: string
  qty: number | string
  price: number | string
  status: string
  strategyId: string
  timestamp: number
}

const toasts = ref<Toast[]>([])
const notifiedOrderKeys = ref<Set<string>>(new Set())
let pollTimer: ReturnType<typeof setInterval> | null = null
let isFirstLoad = true
const mountedAt = Date.now()
const RECENT_ORDER_GRACE_MS = 60_000
const SEEN_STORAGE_KEY = 'openpine.notifiedTradeOrderKeys.v2'
const { t } = useI18n()

function loadNotifiedKeys() {
  try {
    const raw = localStorage.getItem(SEEN_STORAGE_KEY)
    const keys = raw ? JSON.parse(raw) : []
    if (Array.isArray(keys)) {
      notifiedOrderKeys.value = new Set(keys.filter((id): id is string => typeof id === 'string'))
    }
  } catch {
    notifiedOrderKeys.value = new Set()
  }
}

function saveNotifiedKeys() {
  try {
    const keys = Array.from(notifiedOrderKeys.value).slice(-500)
    localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(keys))
  } catch {
    // Best-effort only: notifications must keep working if storage is unavailable.
  }
}

function rememberNotification(key: string) {
  notifiedOrderKeys.value.add(key)
  saveNotifiedKeys()
}

function orderTimeMs(order: any): number {
  const raw = Number(order.updated_at ?? order.created_at ?? 0)
  return Number.isFinite(raw) ? raw : 0
}

function isNotifiableStatus(statusRaw: unknown) {
  const status = String(statusRaw ?? '').toLowerCase()
  return status === 'filled' || status === 'closed' || status === 'partially_filled'
}

function notificationKey(order: any) {
  const status = String(order.status ?? '').toLowerCase()
  return `${order.order_id}:${status}`
}

function sideKind(sideRaw: unknown) {
  const side = String(sideRaw ?? '').toLowerCase()
  return side === 'buy' || side === 'long' ? 'buy' : 'sell'
}

async function pollOrders() {
  try {
    const { data } = await getOrders(undefined, 50)
    const orders = Array.isArray(data) ? data : []

    if (isFirstLoad) {
      // Mark older existing orders as seen, but do not swallow a fresh order that
      // landed while the page or Vite client was reconnecting.
      orders.forEach((o: any) => {
        if (!o.order_id || !isNotifiableStatus(o.status)) return
        const key = notificationKey(o)
        if (notifiedOrderKeys.value.has(key)) return
        const isFresh = orderTimeMs(o) >= mountedAt - RECENT_ORDER_GRACE_MS
        if (!isFresh) rememberNotification(key)
      })
      isFirstLoad = false
    }

    for (const order of orders) {
      const oid = order.order_id
      if (!oid || !isNotifiableStatus(order.status)) continue
      const key = notificationKey(order)
      if (notifiedOrderKeys.value.has(key)) continue

      rememberNotification(key)
      const toast: Toast = {
        id: `${oid}_${Date.now()}`,
        side: order.side ?? 'unknown',
        symbol: order.symbol ?? '???',
        qty: order.filled_quantity ?? order.qty ?? '?',
        price: order.avg_fill_price ?? order.limit_price ?? '?',
        status: order.status ?? 'filled',
        strategyId: order.strategy_id ?? '',
        timestamp: Date.now(),
      }
      addToast(toast)
    }
  } catch (e) {
    // Silent fail — polling shouldn't spam errors
  }
}

function addToast(toast: Toast) {
  toasts.value.push(toast)
  // Keep trade notifications visible long enough to notice while watching charts.
  setTimeout(() => {
    removeToast(toast.id)
  }, 30000)
}

function removeToast(id: string) {
  const idx = toasts.value.findIndex(t => t.id === id)
  if (idx !== -1) toasts.value.splice(idx, 1)
}

function formatPrice(p: number | string) {
  const n = typeof p === 'string' ? parseFloat(p) : p
  if (isNaN(n)) return String(p)
  const abs = Math.abs(n)
  if (abs > 0 && abs < 1) return n.toPrecision(6)
  if (abs < 100) return n.toFixed(4)
  return n.toFixed(2)
}

onMounted(() => {
  loadNotifiedKeys()
  pollOrders()
  pollTimer = setInterval(pollOrders, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="fixed bottom-4 right-4 z-[1000] flex flex-col gap-2 pointer-events-none max-w-sm">
    <transition-group name="toast">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="pointer-events-auto bg-dark-800 border border-dark-500 rounded-xl shadow-2xl p-3 flex items-start gap-3 animate-slide-in"
      >
        <!-- Icon -->
        <div
          class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-sm font-bold"
          :class="sideKind(toast.side) === 'buy' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'"
        >
          {{ sideKind(toast.side) === 'buy' ? '↑' : '↓' }}
        </div>

        <!-- Content -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold text-gray-200">{{ toast.symbol }}</span>
            <span
              class="text-xs px-1.5 py-0.5 rounded font-medium"
              :class="sideKind(toast.side) === 'buy' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'"
            >
              {{ (toast.side ?? '').toUpperCase() }}
            </span>
          </div>
          <div class="text-[11px] uppercase tracking-wide text-accent-light mt-0.5">
            {{ t('tradeNotifications.newTrade') }}
          </div>
          <div class="text-xs text-gray-400 mt-0.5">
            {{ t('tradeNotifications.qty') }}: {{ toast.qty }} · {{ t('tradeNotifications.price') }}: {{ formatPrice(toast.price) }}
          </div>
          <div class="text-xs text-gray-500 mt-0.5 truncate">
            {{ toast.strategyId.slice(0, 20) }}...
          </div>
        </div>

        <!-- Close -->
        <button
          @click="removeToast(toast.id)"
          class="shrink-0 text-gray-500 hover:text-gray-300 p-0.5"
        >✕</button>

        <!-- Auto-dismiss progress bar -->
        <div class="absolute bottom-0 left-0 right-0 h-0.5 bg-dark-600 rounded-b-xl overflow-hidden">
          <div class="h-full bg-accent animate-progress" />
        </div>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-enter-active {
  transition: all 0.3s ease-out;
}
.toast-leave-active {
  transition: all 0.3s ease-in;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(100px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(100px);
}

@keyframes slide-in {
  from { transform: translateX(100px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

@keyframes progress {
  from { width: 100%; }
  to { width: 0%; }
}

.animate-slide-in {
  animation: slide-in 0.3s ease-out;
  position: relative;
}

.animate-progress {
  animation: progress 30s linear forwards;
}
</style>
