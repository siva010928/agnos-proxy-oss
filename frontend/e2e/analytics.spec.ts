import { test, expect } from '@playwright/test'
import { gotoApp, guardNo4xx, snap } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Smoke: the Cost Analytics screen mounts its synchronous scaffolding (page shell,
// range picker, KPI strip) and fires no 4xx during load. The rankings table and the
// richer filter interactions depend on populated cost data, so they are exercised in
// the full-data local suite; this CI check guarantees the page renders against the
// echo stack.
test('analytics: page renders shell + range picker + KPI strip (no 4xx)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'cost')

  await expect(page.getByTestId('analytics-page')).toBeVisible()
  await expect(page.getByTestId('range-picker')).toBeVisible()
  await expect(page.getByTestId('kpi-strip')).toBeVisible()

  await snap(page, 'analytics-default')
  guard.assertClean()
})
