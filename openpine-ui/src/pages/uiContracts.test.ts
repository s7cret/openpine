import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..')
const source = (relative: string) => readFileSync(resolve(srcRoot, relative), 'utf8')

describe('global UI correctness contracts', () => {
  it('drives layout health/version from the API and exposes the accessible unlock prompt', () => {
    const layout = source('layouts/AppLayout.vue')
    const unlock = source('components/AuthUnlockDialog.vue')

    expect(layout).toContain('getGatewayHealth')
    expect(layout).not.toContain("{{ t('app.version') }} · {{ t('app.gatewayOnline') }}")
    expect(layout).toContain('AuthUnlockDialog')
    expect(unlock).toContain('role="dialog"')
    expect(unlock).toContain('aria-modal="true"')
    expect(unlock).toContain('@keydown.esc')
    expect(unlock).toContain('type="password"')
    expect(unlock).toContain('role="alert"')
  })

  it('opens the date picker on the selected range and follows external range updates', () => {
    const picker = source('components/DateRangePicker.vue')

    expect(picker).toContain("from '@/lib/dateRangeCalendar'")
    expect(picker).toContain('function syncCalendarsToRange()')
    expect(picker).toContain('if (isOpen.value) syncCalendarsToRange()')
    expect(picker).toContain("watch(() => [props.from, props.to]")
    expect(picker).not.toContain('initCalRight')
  })

  it('uses authenticated blob downloads for reports and exports', () => {
    const tvPage = source('pages/TvParity.vue')
    const tvViz = source('components/TvParityVisualization.vue')
    const backtests = source('pages/Backtests.vue')

    expect(tvPage).toContain('downloadApiResource')
    expect(tvViz).toContain('downloadApiResource')
    expect(backtests).toContain('downloadApiResource')
    expect(tvViz).not.toContain(':href="tvParityReportUrl')
    expect(backtests).not.toContain(':href="\'/api/backtest/runs/')
  })

  it('marks touched async status and dialogs for assistive technology', () => {
    const pineFiles = source('pages/PineFiles.vue')
    const tvParity = source('pages/TvParity.vue')
    const data = source('pages/Data.vue')

    expect(pineFiles).toContain('aria-live="polite"')
    expect(pineFiles).toContain('store.compileErrors')
    expect(tvParity).toContain('aria-live="polite"')
    expect(tvParity).toContain('role="alert"')
    expect(data).toContain('role="dialog"')
    expect(data).toContain('aria-modal="true"')
    expect(data).toContain('@keydown.esc')
  })

  it('uses non-overlapping visibility-aware polling on high-traffic views', () => {
    const hotViews = [
      'pages/Dashboard.vue',
      'pages/Data.vue',
      'pages/Strategies.vue',
      'pages/Backtests.vue',
      'components/TradeNotifications.vue',
    ]
    for (const view of hotViews) {
      const text = source(view)
      expect(text, view).not.toContain('setInterval(')
      expect(text, view).toContain('createVisibilityPoller')
    }
    const strategies = source('pages/Strategies.vue')
    expect(strategies).not.toContain("store.current?.symbol ?? 'BTCUSDT'")
    expect(strategies).toContain('v-if="detailStrategy"')
  })

  it('keeps the new RU and EN keys symmetric', () => {
    const en = JSON.parse(source('i18n/locales/en.json'))
    const ru = JSON.parse(source('i18n/locales/ru.json'))
    const required = [
      'gatewayChecking', 'gatewayDegraded', 'gatewayOffline',
      'unlockTitle', 'unlockDescription', 'tokenLabel', 'unlock', 'dismiss',
    ]
    for (const key of required) {
      expect(en.app[key]).toBeTruthy()
      expect(ru.app[key]).toBeTruthy()
    }
    expect(Object.keys(en.app).sort()).toEqual(Object.keys(ru.app).sort())
    expect(Object.keys(en.common).sort()).toEqual(Object.keys(ru.common).sort())
    expect(Object.keys(en.pineFiles).sort()).toEqual(Object.keys(ru.pineFiles).sort())
    expect(Object.keys(en.tvParity).sort()).toEqual(Object.keys(ru.tvParity).sort())
  })

  it('adds jobs optimizer live routes with EN and RU keys and no live start probe', () => {
    const router = source('router/index.ts')
    const en = JSON.parse(source('i18n/locales/en.json'))
    const ru = JSON.parse(source('i18n/locales/ru.json'))
    const live = source('pages/Live.vue')
    const optimizer = source('pages/Optimizer.vue')
    const inbox = source('stores/jobInbox.ts')
    expect(router).toContain("/jobs/:jobId")
    expect(router).toContain("/optimize/:jobId")
    expect(router).toContain("/live/:strategyId")
    expect(en.nav.jobs).toBeTruthy()
    expect(ru.nav.jobs).toBeTruthy()
    expect(en.nav.optimizer).toBeTruthy()
    expect(ru.nav.optimizer).toBeTruthy()
    expect(en.nav.live).toBeTruthy()
    expect(ru.nav.live).toBeTruthy()
    expect(Object.keys(en.optimizer).sort()).toEqual(Object.keys(ru.optimizer).sort())
    expect(Object.keys(en.live).sort()).toEqual(Object.keys(ru.live).sort())
    expect(live).toContain("api.get('/live/status')")
    expect(live).not.toContain("/live/start")
    expect(optimizer).not.toContain('}``')
    expect(inbox).not.toContain('randomUUID')
  })
})
