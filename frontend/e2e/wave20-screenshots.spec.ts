import { expect, test } from '@playwright/test'
import { gotoApp, snap } from './_helpers'

test('screenshot: Overview with real live KPIs', async ({ page }) => {
  await gotoApp(page, '/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await page.waitForTimeout(2000)
  await snap(page, 'wave20-overview')
})

test('screenshot: Live Requests feed', async ({ page }) => {
  await gotoApp(page, 'live')
  await expect(page.getByRole('heading', { name: 'Live Requests' })).toBeVisible()
  await page.waitForTimeout(2000)
  await snap(page, 'wave20-live-requests')
})

test('screenshot: Clients admin page', async ({ page }) => {
  await gotoApp(page, 'admin/clients')
  await expect(page.getByRole('heading', { name: /Clients/ })).toBeVisible()
  await expect(page.getByTestId('client-list')).toBeVisible({ timeout: 5_000 })
  await snap(page, 'wave20-admin-clients')
})
