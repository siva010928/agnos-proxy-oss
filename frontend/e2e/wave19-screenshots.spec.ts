import { expect, test } from '@playwright/test'
import { gotoApp, snap } from './_helpers'

// WAVE 19 final-pass screenshots: capture the new operator screens for the
// quick visual review of key screens.

test('screenshot: Observability launchers + native KPIs', async ({ page }) => {
  await gotoApp(page, 'observability')
  await expect(page.getByRole('heading', { name: 'Observability' })).toBeVisible()
  await expect(page.getByTestId('launcher-grafana')).toBeVisible()
  await expect(page.getByTestId('launcher-jaeger')).toBeVisible()
  await expect(page.getByTestId('launcher-kafka')).toBeVisible()
  await expect(page.getByTestId('launcher-prometheus')).toBeVisible()
  await snap(page, 'wave19-observability-launchers')
})

test('screenshot: Request Logs page (search + filters + rows)', async ({ page }) => {
  await gotoApp(page, 'logs')
  await expect(page.getByRole('heading', { name: 'Request Logs' })).toBeVisible()
  await expect(page.getByTestId('logs-filters')).toBeVisible()
  // Wait for at least one row to render
  await expect(page.getByTestId('logs-rows')).toBeVisible({ timeout: 10_000 })
  await snap(page, 'wave19-request-logs')
})

test('screenshot: Sidebar shows Routing Map (view) + Admin Routing (edit) labels', async ({ page }) => {
  await gotoApp(page, '/')
  // Two clearly-distinguished entries: the read-only map and the admin editor.
  await expect(page.getByRole('link', { name: /Routing Map/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Routing \(edit\)/ })).toBeVisible()
  await snap(page, 'wave19-sidebar-routing-clarity')
})
