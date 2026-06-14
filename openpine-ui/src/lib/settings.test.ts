import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import {
  addStableQuoteAsset,
  COMMON_TIMEZONE_OPTIONS,
  normalizeSettingsPayload,
  removeStableQuoteAsset,
  settingsPayloadToUpdate,
  timezoneOptionLabel,
  type SettingsPayload,
} from './settings'

const payload: SettingsPayload = {
  timezone: 'Europe/Moscow',
  timezone_label: 'MSK',
  marketdata: {
    stable_quotes_only: true,
    stable_quote_assets: ['USDT', 'USDC'],
    symbol_search_limit: 50,
    timeframes: ['1m', '3m', '1h'],
    default_timeframe: '3m',
    supported_timeframes: ['1m', '3m', '5m', '15m', '1h'],
  },
}

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..')

describe('settings helpers', () => {
  it('normalizes backend settings into form-safe values', () => {
    expect(normalizeSettingsPayload(payload)).toEqual({
      timezone: 'Europe/Moscow',
      stableQuotesOnly: true,
      stableQuoteAssets: ['USDT', 'USDC'],
      symbolSearchLimit: 50,
      timeframes: ['1m', '3m', '1h'],
      defaultTimeframe: '3m',
      supportedTimeframes: ['1m', '3m', '5m', '15m', '1h'],
    })
  })

  it('dedupes and uppercases stable assets and keeps default timeframe selectable', () => {
    const update = settingsPayloadToUpdate({
      timezone: 'Europe/Moscow',
      stableQuotesOnly: false,
      stableQuoteAssets: ['usdt', 'usdc', 'USDT', ''],
      symbolSearchLimit: 75,
      timeframes: ['3m', '1m', '3m'],
      defaultTimeframe: '1h',
      supportedTimeframes: ['1m', '3m', '1h'],
    })

    expect(update).toEqual({
      timezone: 'Europe/Moscow',
      marketdata: {
        stable_quotes_only: false,
        stable_quote_assets: ['USDT', 'USDC'],
        symbol_search_limit: 75,
        timeframes: ['3m', '1m', '1h'],
        default_timeframe: '1h',
      },
    })
  })

  it('offers offset-first timezone buckets with concise region examples', () => {
    expect(COMMON_TIMEZONE_OPTIONS).toContainEqual({
      value: 'UTC+03:00',
      label: 'UTC+03:00 — Moscow / Middle East / East Africa',
    })
    expect(COMMON_TIMEZONE_OPTIONS.length).toBeLessThan(20)
    expect(timezoneOptionLabel('UTC+03:00')).toBe('UTC+03:00 — Moscow / Middle East / East Africa')
    expect(timezoneOptionLabel('UTC+09:00')).toBe('UTC+09:00 — Japan / Korea')
    expect(timezoneOptionLabel('Europe/Moscow')).toBe('UTC+03:00 — Moscow / Middle East / East Africa')
  })

  it('manages stable quote assets as removable chips', () => {
    const added = addStableQuoteAsset(['USDT'], 'usdc')
    expect(added).toEqual(['USDT', 'USDC'])
    expect(addStableQuoteAsset(added, 'USDT')).toEqual(['USDT', 'USDC'])
    expect(removeStableQuoteAsset(added, 'usdt')).toEqual(['USDC'])
  })

  it('renders stable quote controls only behind the stable-only toggle', () => {
    const settingsVue = readFileSync(resolve(srcRoot, 'pages/Settings.vue'), 'utf8')
    const enMessages = JSON.parse(readFileSync(resolve(srcRoot, 'i18n/locales/en.json'), 'utf8'))
    expect(settingsVue).toContain('v-if="form.stableQuotesOnly"')
    expect(settingsVue).toContain("t('settings.stableQuotes')")
    expect(enMessages.settings.stableQuotes).toBe('Stable quote assets')
    expect(settingsVue).not.toContain('stableQuoteAssetsText')
  })

  it('adds an achievements page before settings in route and nav order', () => {
    const routerSource = readFileSync(resolve(srcRoot, 'router/index.ts'), 'utf8')
    const layoutSource = readFileSync(resolve(srcRoot, 'layouts/AppLayout.vue'), 'utf8')
    const achievementsRouteIndex = routerSource.indexOf("path: '/achievements'")
    const settingsRouteIndex = routerSource.indexOf("path: '/settings'")
    const achievementsNavIndex = layoutSource.indexOf("path: '/achievements'")
    const settingsNavIndex = layoutSource.indexOf("path: '/settings'")

    expect(achievementsRouteIndex).toBeGreaterThan(-1)
    expect(settingsRouteIndex).toBeGreaterThan(achievementsRouteIndex)
    expect(achievementsNavIndex).toBeGreaterThan(-1)
    expect(settingsNavIndex).toBeGreaterThan(achievementsNavIndex)
  })
})
