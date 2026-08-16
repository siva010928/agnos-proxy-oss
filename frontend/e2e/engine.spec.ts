import { test, expect } from '@playwright/test'
import { gotoApp, guardNo4xx } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Smoke: the Engine Health screen mounts, reports the running engine, and offers the
// swap controls, with no 4xx during load. The actual bifrost<->direct swap round-trip
// is exercised in the full-infra local/staging suite (it needs a live Bifrost
// sidecar); this CI check guarantees the operator screen renders on the echo stack
// without mutating global engine state.
test('engine: health screen renders current engine + swap controls (no 4xx)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'engine')

  await expect(page.getByTestId('engine-name')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId('engine-swap-row')).toBeVisible()

  guard.assertClean()
})
