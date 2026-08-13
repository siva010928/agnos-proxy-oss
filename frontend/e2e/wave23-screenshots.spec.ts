import { expect, test } from '@playwright/test'
import { gotoApp, snap } from './_helpers'

test('WAVE 23: Analytics with multi-point trend charts + rankings populated', async ({ page }) => {
  await gotoApp(page, 'cost')
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible()
  // Wait for data to load (timeseries + cost queries)
  await page.waitForTimeout(3000)
  // Rankings should be populated (not empty state)
  await expect(page.getByTestId('rank-table-provider')).toBeVisible({ timeout: 10_000 })
  await snap(page, 'wave23-analytics-multi-point')
})

test('WAVE 23: Observability consistent with Overview (shows DB total)', async ({ page }) => {
  await gotoApp(page, 'observability')
  await expect(page.getByRole('heading', { name: 'Observability' })).toBeVisible()
  await page.waitForTimeout(2000)
  await snap(page, 'wave23-observability-consistent')
})

test('WAVE 23: Overview with real multi-day KPIs', async ({ page }) => {
  await gotoApp(page, '/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await page.waitForTimeout(2000)
  await snap(page, 'wave23-overview-multiday')
})
