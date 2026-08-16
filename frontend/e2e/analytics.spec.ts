import { test, expect } from '@playwright/test'
import { gotoApp, guardNo4xx, snap } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Smoke: the Cost Analytics screen mounts its core scaffolding (KPI strip, range
// picker, ranking tabs) and fires no 4xx during load. The richer filter/ranking
// interactions are exercised in the full-data local suite; this CI check guarantees
// the page renders against the echo stack.
test('analytics: page renders KPIs + range picker + ranking tabs (no 4xx)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'cost')

  await expect(page.getByTestId('analytics-page')).toBeVisible()
  await expect(page.getByTestId('range-picker')).toBeVisible()
  await expect(page.getByTestId('kpi-strip')).toBeVisible()
  await expect(page.getByTestId('rank-tabs')).toBeVisible()

  await snap(page, 'analytics-default')
  guard.assertClean()
})
