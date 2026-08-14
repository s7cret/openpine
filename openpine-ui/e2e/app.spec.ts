import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN = 'e2e-token'

function payloadFor(pathname: string): unknown {
  if (pathname === '/health') return { status: 'ok', version: 'e2e' }
  if (pathname === '/api/version') return {
    version: 'e2e',
    stack_conforms: true,
    modules: [],
    runtime: { python: '3.13', platform: 'linux', machine: 'arm64', node: 'e2e' },
  }
  if (pathname === '/api/data/metadata') return { source: 'e2e', exchanges: [], market_types: [] }
  if (pathname === '/api/data/health') return {
    status: 'ok',
    exchanges: [],
    totals: { enabled_exchanges: 0, market_types: 0, symbol_search_exchanges: 0 },
    settings: { timeframes: [] },
  }
  if (pathname === '/api/settings') return { timezone: 'UTC' }
  if (pathname === '/api/tv-parity/runs') return { items: [], total: 0, limit: 10 }
  if (pathname === '/api/achievements') return {
    items: [],
    summary: { total: 0, unlocked: 0, by_tier: {} },
  }
  if (pathname.endsWith('/summary-cards')) return { overall_status: 'match', failures: [] }
  if (pathname.endsWith('/diagnostics/callouts')) return { callouts: [] }
  if (pathname.endsWith('/mismatches/top')) return { items: [], total: 0, limit: 20 }
  if (pathname.endsWith('/chart-data')) return { series: [], failures: [], plots: {}, trades: {} }
  if (
    pathname === '/api/strategies'
    || pathname === '/api/pine-sources'
    || pathname === '/api/backtest/runs'
    || pathname === '/api/orders'
  ) return []
  return {}
}

async function mockApi(route: Route, requireAuth = true): Promise<void> {
  const request = route.request()
  const url = new URL(request.url())
  if (url.pathname === '/health') {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payloadFor(url.pathname)) })
    return
  }
  if (url.pathname.startsWith('/api/') && requireAuth && request.headers().authorization !== `Bearer ${TOKEN}`) {
    await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Unauthorized' }) })
    return
  }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payloadFor(url.pathname)) })
}

async function installApiMock(page: Page): Promise<void> {
  await page.route('**/health', route => mockApi(route))
  await page.route('**/api/**', route => mockApi(route))
}

test('401 opens LAN unlock flow and all subsequent API calls carry bearer auth', async ({ page }) => {
  const pageErrors: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await installApiMock(page)
  const navigation = await page.goto('/dashboard')
  await page.waitForTimeout(250)
  expect(pageErrors).toEqual([])
  expect(consoleErrors.filter(message => !message.includes('401 (Unauthorized)'))).toEqual([])

  const dialog = page.getByRole('dialog', { name: /unlock|разблок/i })
  expect(navigation?.status()).toBe(200)
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/token|токен/i).fill(TOKEN)
  await dialog.getByRole('button', { name: /unlock|разблок/i }).click()

  await expect(dialog).toBeHidden()
  expect(await page.evaluate(() => sessionStorage.getItem('openpine.api.bearer-token'))).toBe(TOKEN)
  await expect.poll(async () => {
    const requests = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name))
    return requests.some(url => url.includes('/api/'))
  }).toBe(true)
})

for (const path of ['/dashboard', '/pine-files', '/strategies', '/backtests', '/tv-parity', '/data', '/achievements', '/settings']) {
  test(`${path} has no WCAG A/AA violations or uncaught page errors`, async ({ page }) => {
    const pageErrors: string[] = []
    const consoleErrors: string[] = []
    page.on('pageerror', error => pageErrors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    await page.addInitScript(token => sessionStorage.setItem('openpine.api.bearer-token', token), TOKEN)
    await installApiMock(page)
    await page.goto(path)
    await page.waitForLoadState('networkidle')

    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
    const violationSummary = results.violations.flatMap(violation =>
      violation.nodes.map(node => ({ id: violation.id, target: node.target, html: node.html })),
    )
    expect(violationSummary).toEqual([])
    expect(pageErrors).toEqual([])
    expect(consoleErrors).toEqual([])
  })
}
