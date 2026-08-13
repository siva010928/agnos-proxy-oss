import { test, expect } from '@playwright/test'
import { gotoApp, guardNo4xx } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Engine swap mutates GLOBAL server state - must run serially (not in parallel
// with other workers that also read/write engine state via the Routing screen's
// EngineOverrideCard or the BVT integration suite).
test.describe.configure({ mode: 'serial' })

test('engine swap: bifrost → direct → bifrost (no 4xx)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)

  // Force bifrost as the starting state (other tests may leave overrides active)
  await page.request.post('/admin/engine', {
    headers: { 'X-Admin-Token': 'platform-admin-secret', 'Content-Type': 'application/json' },
    data: { engine: 'bifrost' },
  })

  await gotoApp(page, 'engine')

  // initial state - bifrost
  await expect(page.getByTestId('engine-name')).toHaveText('bifrost', { timeout: 10_000 })

  // swap to direct
  await page.getByTestId('engine-toggle').locator('button').click()
  await expect(page.getByTestId('toast-success')).toBeVisible({ timeout: 5_000 })
  await expect(page.getByTestId('engine-name')).toHaveText('direct', { timeout: 10_000 })

  // swap back to bifrost (cleanup so we don't leave the runtime engine in 'direct')
  await page.getByTestId('engine-toggle').locator('button').click()
  await expect(page.getByTestId('engine-name')).toHaveText('bifrost', { timeout: 10_000 })

  guard.assertClean()
})
