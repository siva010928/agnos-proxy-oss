import { test, expect } from '@playwright/test'
import { gotoApp, guardNo4xx, snap } from './_helpers'

const ALLOW = [/\/events($|\?)/]

test('analytics: KPIs + filter bar + range picker + rankings (no 4xx)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'cost')

  // page mounted
  await expect(page.getByTestId('analytics-page')).toBeVisible()
  await expect(page.getByTestId('range-picker')).toBeVisible()
  await expect(page.getByTestId('filter-bar')).toBeVisible()

  // KPI strip renders 6 tiles
  await expect(page.getByTestId('kpi-strip').locator('> *')).toHaveCount(6, { timeout: 10_000 })

  // give the page a moment to paint charts populated, then snap the centerpiece
  await snap(page, 'analytics-default-45d')

  // range picker triggers a re-query (45d → 7d)
  const tsCalls: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/admin/usage/timeseries')) tsCalls.push(req.url())
  })
  await page.getByTestId('range-7d').click()
  await expect.poll(() => tsCalls.some((u) => /from=/.test(u) && /granularity=day/.test(u)), { timeout: 5_000 }).toBe(true)

  // filter bar - pick a provider, expect cost endpoint to receive provider=
  const costCalls: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/admin/cost')) costCalls.push(req.url())
  })
  await page.getByTestId('filter-provider').selectOption('bedrock')
  await expect.poll(() => costCalls.some((u) => /provider=bedrock/.test(u)), { timeout: 5_000 }).toBe(true)
  await snap(page, 'analytics-filtered-7d-bedrock')

  // rankings - switch dim to 'model'
  await page.getByTestId('rank-model').click()
  await expect(page.getByTestId('rank-table-model')).toBeVisible({ timeout: 5_000 })

  // scroll the rankings table into view, then snap (covers the 3rd, 4th, 5th rows visually)
  await page.getByTestId('rank-table-model').scrollIntoViewIfNeeded()
  await snap(page, 'analytics-rankings-by-model')

  // filter chip clear works
  await page.getByTestId('filter-clear').click()
  await expect(page.getByTestId('filter-clear')).toHaveCount(0)

  guard.assertClean()
})
