<script setup lang="ts">
import { ref, computed, watch } from 'vue'

const props = defineProps<{
  from: string
  to: string
  allFrom?: string
}>()

const emit = defineEmits<{
  (e: 'update:from', val: string): void
  (e: 'update:to', val: string): void
}>()

const isOpen = ref(false)
const localFrom = ref(props.from)
const localTo = ref(props.to)
const activePreset = ref('1M')

// Calendar state
const calLeft = ref(new Date())
const _calRightInit = new Date()
_calRightInit.setMonth(_calRightInit.getMonth() + 1)
const calRight = ref(_calRightInit)

// Initialize calRight
const initCalRight = () => {
  const d = new Date(calLeft.value)
  d.setMonth(d.getMonth() + 1)
  calRight.value = d
}
initCalRight()

const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December']
const monthShort = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const dayNames = ['Mo','Tu','We','Th','Fr','Sa','Su']

const presets = [
  { label: '1D', days: 1 },
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: 'All', days: 3650, all: true },
]

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function fmt(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

function parseDateOnly(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return new Date()
  return new Date(year, month - 1, day)
}

function fmtDisplay(d: string): string {
  if (!d) return ''
  const dt = parseDateOnly(d)
  return `${dt.getDate()} ${monthShort[dt.getMonth()]} ${dt.getFullYear()}`
}

function applyPreset(p: { label: string; days: number; all?: boolean }) {
  activePreset.value = p.label
  const now = new Date()
  const from = p.all && props.allFrom ? parseDateOnly(props.allFrom) : new Date(now.getTime() - p.days * 86400000)
  localFrom.value = fmt(from)
  localTo.value = fmt(now)
  emit('update:from', localFrom.value)
  emit('update:to', localTo.value)
  // Reset calendars
  calLeft.value = new Date(from)
  initCalRight()
}

function navigateMonth(cal: 'left' | 'right', delta: number) {
  const target = cal === 'left' ? calLeft : calRight
  const d = new Date(target.value)
  d.setMonth(d.getMonth() + delta)
  if (cal === 'left') {
    calLeft.value = d
    // Ensure right is after left
    if (calRight.value <= d) {
      const rd = new Date(d)
      rd.setMonth(rd.getMonth() + 1)
      calRight.value = rd
    }
  } else {
    calRight.value = d
    if (calLeft.value >= d) {
      const ld = new Date(d)
      ld.setMonth(ld.getMonth() - 1)
      calLeft.value = ld
    }
  }
}

function generateDays(monthDate: Date) {
  const year = monthDate.getFullYear()
  const month = monthDate.getMonth()
  const firstDay = new Date(year, month, 1)
  const lastDay = new Date(year, month + 1, 0)
  
  // Monday-based: 0=Mon, 6=Sun
  let startDay = firstDay.getDay() - 1
  if (startDay < 0) startDay = 6
  
  const days: Array<{ day: number; date: string; isCurrent: boolean; isStart: boolean; isEnd: boolean; inRange: boolean; isToday: boolean }> = []
  
  // Previous month padding
  const prevLast = new Date(year, month, 0)
  for (let i = startDay - 1; i >= 0; i--) {
    const d = prevLast.getDate() - i
    const dateStr = fmt(new Date(year, month - 1, d))
    days.push({ day: d, date: dateStr, isCurrent: false, isStart: false, isEnd: false, inRange: false, isToday: false })
  }
  
  // Current month
  const today = fmt(new Date())
  for (let d = 1; d <= lastDay.getDate(); d++) {
    const dateStr = fmt(new Date(year, month, d))
    days.push({
      day: d,
      date: dateStr,
      isCurrent: true,
      isStart: dateStr === localFrom.value,
      isEnd: dateStr === localTo.value,
      inRange: dateStr > localFrom.value && dateStr < localTo.value,
      isToday: dateStr === today,
    })
  }
  
  // Next month padding (fill to 42 = 6 rows)
  const remaining = 42 - days.length
  for (let d = 1; d <= remaining; d++) {
    const dateStr = fmt(new Date(year, month + 1, d))
    days.push({ day: d, date: dateStr, isCurrent: false, isStart: false, isEnd: false, inRange: false, isToday: false })
  }
  
  return days
}

const leftDays = computed(() => generateDays(calLeft.value))
const rightDays = computed(() => generateDays(calRight.value))

const leftMonthLabel = computed(() => `${monthNames[calLeft.value.getMonth()]} ${calLeft.value.getFullYear()}`)
const rightMonthLabel = computed(() => `${monthNames[calRight.value.getMonth()]} ${calRight.value.getFullYear()}`)

let clickState: 'from' | 'to' = 'from'

function onDayClick(dateStr: string) {
  activePreset.value = ''
  if (clickState === 'from') {
    localFrom.value = dateStr
    localTo.value = dateStr
    clickState = 'to'
  } else {
    if (dateStr < localFrom.value) {
      localTo.value = localFrom.value
      localFrom.value = dateStr
    } else {
      localTo.value = dateStr
    }
    clickState = 'from'
  }
  emit('update:from', localFrom.value)
  emit('update:to', localTo.value)
}

function toggle() {
  isOpen.value = !isOpen.value
  if (isOpen.value) clickState = 'from'
}

function onClickOutside(e: Event) {
  const target = e.target as HTMLElement
  if (!target.closest('.date-range-picker')) {
    isOpen.value = false
  }
}

watch(() => props.from, (v) => { localFrom.value = v })
watch(() => props.to, (v) => { localTo.value = v })

// Attach/detach click outside
import { onMounted, onUnmounted } from 'vue'
onMounted(() => document.addEventListener('click', onClickOutside))
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<template>
  <div class="date-range-picker relative">
    <!-- Trigger button -->
    <button
      @click.stop="toggle"
      class="flex items-center gap-2 px-3 py-1.5 bg-dark-700 border border-dark-500 rounded-lg text-sm text-gray-200 hover:border-accent transition-colors focus:outline-none"
    >
      <span class="text-accent">📅</span>
      <span>{{ fmtDisplay(localFrom) }}</span>
      <span class="text-gray-500">→</span>
      <span>{{ fmtDisplay(localTo) }}</span>
      <svg class="w-3.5 h-3.5 text-gray-500 ml-1 transition-transform" :class="{ 'rotate-180': isOpen }" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
      </svg>
    </button>

    <!-- Dropdown -->
    <transition name="dropdown">
      <div
        v-if="isOpen"
        @click.stop
        class="absolute top-full left-0 mt-2 bg-dark-800 border border-dark-500 rounded-xl shadow-2xl z-50 p-4 min-w-[640px]"
      >
        <!-- Presets row -->
        <div class="flex gap-1.5 mb-4">
          <button
	            v-for="p in presets"
	            :key="p.label"
	            @click="applyPreset(p)"
            :class="[
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
              activePreset === p.label
                ? 'bg-accent text-white shadow-lg shadow-accent/20'
                : 'bg-dark-700 text-gray-400 hover:bg-dark-600 hover:text-gray-200'
            ]"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- Dual calendar -->
        <div class="flex gap-6">
          <!-- Left calendar -->
          <div class="flex-1">
            <div class="flex items-center justify-between mb-3">
              <button @click="navigateMonth('left', -1)" class="p-1 rounded hover:bg-dark-600 text-gray-400 hover:text-gray-200 transition-colors">
                <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M12.79 5.23a.75.75 0 01-.02 1.06L8.832 10l3.938 3.71a.75.75 0 11-1.04 1.08l-4.5-4.25a.75.75 0 010-1.08l4.5-4.25a.75.75 0 011.06.02z" clip-rule="evenodd" /></svg>
              </button>
              <span class="text-sm font-medium text-gray-200">{{ leftMonthLabel }}</span>
              <button @click="navigateMonth('left', 1)" class="p-1 rounded hover:bg-dark-600 text-gray-400 hover:text-gray-200 transition-colors">
                <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clip-rule="evenodd" /></svg>
              </button>
            </div>
            <div class="grid grid-cols-7 gap-0.5">
              <div v-for="d in dayNames" :key="d" class="text-center text-[10px] text-gray-500 py-1 font-medium">{{ d }}</div>
              <button
                v-for="(day, i) in leftDays"
                :key="'l'+i"
                @click="onDayClick(day.date)"
                :disabled="!day.isCurrent"
                :class="[
                  'w-8 h-8 rounded-lg text-xs flex items-center justify-center transition-all relative',
                  !day.isCurrent ? 'text-gray-600 cursor-default' : 'text-gray-300 hover:bg-accent/20 cursor-pointer',
                  day.isStart ? 'bg-accent text-white rounded-r-none' : '',
                  day.isEnd ? 'bg-accent text-white rounded-l-none' : '',
                  day.inRange ? 'bg-accent/10 text-accent-light rounded-none' : '',
                  day.isToday && !day.isStart && !day.isEnd ? 'ring-1 ring-accent/50' : '',
                ]"
              >
                {{ day.day }}
              </button>
            </div>
          </div>

          <!-- Divider -->
          <div class="w-px bg-dark-600 self-stretch" />

          <!-- Right calendar -->
          <div class="flex-1">
            <div class="flex items-center justify-between mb-3">
              <button @click="navigateMonth('right', -1)" class="p-1 rounded hover:bg-dark-600 text-gray-400 hover:text-gray-200 transition-colors">
                <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M12.79 5.23a.75.75 0 01-.02 1.06L8.832 10l3.938 3.71a.75.75 0 11-1.04 1.08l-4.5-4.25a.75.75 0 010-1.08l4.5-4.25a.75.75 0 011.06.02z" clip-rule="evenodd" /></svg>
              </button>
              <span class="text-sm font-medium text-gray-200">{{ rightMonthLabel }}</span>
              <button @click="navigateMonth('right', 1)" class="p-1 rounded hover:bg-dark-600 text-gray-400 hover:text-gray-200 transition-colors">
                <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clip-rule="evenodd" /></svg>
              </button>
            </div>
            <div class="grid grid-cols-7 gap-0.5">
              <div v-for="d in dayNames" :key="d" class="text-center text-[10px] text-gray-500 py-1 font-medium">{{ d }}</div>
              <button
                v-for="(day, i) in rightDays"
                :key="'r'+i"
                @click="onDayClick(day.date)"
                :disabled="!day.isCurrent"
                :class="[
                  'w-8 h-8 rounded-lg text-xs flex items-center justify-center transition-all relative',
                  !day.isCurrent ? 'text-gray-600 cursor-default' : 'text-gray-300 hover:bg-accent/20 cursor-pointer',
                  day.isStart ? 'bg-accent text-white rounded-r-none' : '',
                  day.isEnd ? 'bg-accent text-white rounded-l-none' : '',
                  day.inRange ? 'bg-accent/10 text-accent-light rounded-none' : '',
                  day.isToday && !day.isStart && !day.isEnd ? 'ring-1 ring-accent/50' : '',
                ]"
              >
                {{ day.day }}
              </button>
            </div>
          </div>
        </div>

        <!-- Selected range display -->
        <div class="mt-4 pt-3 border-t border-dark-600 flex items-center justify-between">
          <div class="flex items-center gap-2 text-sm">
            <span class="text-gray-500">Selected:</span>
            <span class="text-accent font-medium">{{ fmtDisplay(localFrom) }}</span>
            <span class="text-gray-500">→</span>
            <span class="text-accent font-medium">{{ fmtDisplay(localTo) }}</span>
          </div>
          <button @click="isOpen = false" class="px-3 py-1.5 bg-accent hover:bg-accent-dark text-white text-xs rounded-lg transition-colors">
            Done
          </button>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.dropdown-enter-active, .dropdown-leave-active {
  transition: all 0.2s ease;
}
.dropdown-enter-from, .dropdown-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
