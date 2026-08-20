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
    expect(Object.keys(en.nav).sort()).toEqual(Object.keys(ru.nav).sort())
    expect(Object.keys(en.jobs).sort()).toEqual(Object.keys(ru.jobs).sort())
    expect(Object.keys(en.live).sort()).toEqual(Object.keys(ru.live).sort())
    expect(Object.keys(en.optimizer).sort()).toEqual(Object.keys(ru.optimizer).sort())
  })

  it('loads persisted jobs by stable id and never synthesizes identity', () => {
    const jobsPage = source('pages/Jobs.vue')
    const detail = source('pages/JobDetail.vue')
    const store = source('stores/jobs.ts')
    expect(jobsPage).toContain('job.job_id')
    expect(jobsPage).toContain('compareJobs')
    expect(jobsPage).toContain('jobs-compare-warning')
    const generated = source('api/generated/openapi.ts')
    expect(generated).toContain("/api/jobs/compare")
    expect(jobsPage).not.toContain('crypto.randomUUID')
    expect(detail).toContain('route.params.jobId')
    expect(detail).not.toContain('crypto.randomUUID')
    expect(store).toContain('job_id required')
    expect(store).not.toContain('crypto.randomUUID')
  })

  it('live page never posts start on mount and requires typed LIVE', () => {
    const live = source('pages/Live.vue')
    const mount = live.split('onMounted')[1] ?? ''
    expect(live).toContain('/live/admission')
    expect(live).toContain('live-typed-confirm')
    expect(live).toContain('live-semantic-profile')
    expect(live).toContain('semantic_profile: semanticProfile.value')
    expect(live).toContain("confirmation.value === 'LIVE'")
    expect(mount).not.toContain('/live/start')
    expect(live).toContain('/live/start')
    expect(live).not.toContain('__probe__')
  })

  it('optimizer page runs real search and renders a champion', () => {
    const page = source('pages/Optimizer.vue')
    expect(page).toContain('/optimizer/search')
    expect(page).toContain('optimizer-search')
    expect(page).toContain('champion')
    expect(page).toContain('parameterRows')
    expect(page).not.toContain('/optimizer/dry-run')
  })

  it('optimizer search sends an explicit semantic profile and mirrors validation', () => {
    const page = source('pages/Optimizer.vue')
    expect(page).toContain('optimizer-semantic-profile')
    expect(page).toContain('semantic_profile: semanticProfile.value')
    expect(page).toContain('allow_legacy: allowLegacy.value')
    expect(page).toContain("t('optimizer.semanticProfileRequired')")
    expect(page).toContain('optimizerValidationMessage')
    expect(page).toContain('isSearchDisabled')
    expect(page).toContain(':disabled="isSearchDisabled"')
  })

  it('backtest start sends an explicit semantic profile', () => {
    const page = source('pages/Backtests.vue')
    expect(page).toContain('backtest-semantic-profile')
    expect(page).toContain('semantic_profile')
    expect(page).toContain('allow_legacy')
    expect(page).toContain("t('backtests.semanticProfileRequired')")
  })

  it('tv parity start sends an explicit semantic profile', () => {
    const page = source('pages/TvParity.vue')
    expect(page).toContain('tv-parity-semantic-profile')
    expect(page).toContain('semanticProfile: semanticProfile.value')
    expect(page).toContain('allowLegacy: allowLegacy.value')
    expect(page).toContain("t('tvParity.semanticProfileRequired')")
  })

  it('strategy create requires an explicit semantic profile', () => {
    const page = source('pages/Strategies.vue')
    expect(page).toContain('strategy-semantic-profile')
    expect(page).toContain('v-model="form.semantic_profile"')
    expect(page).toContain("t('strategies.semanticProfileRequired')")
  })

  it('strategy detail can PATCH semantic profile', () => {
    const page = source('pages/Strategies.vue')
    expect(page).toContain('strategy-detail-semantic-profile')
    expect(page).toContain('updateSemanticProfile')
    expect(page).toContain('semantic_profile:')
  })
})
