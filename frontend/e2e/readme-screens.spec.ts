import { test, expect, Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gotoApp } from './_helpers'

// Curated, README-facing gallery. Captures the highest-impact product screens
// from the running gateway (same app as the live deployment) into repo /docs/screens
// so the README can show the product with real data. Run with:
//   npx playwright test e2e/readme-screens.spec.ts
const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '..', '..', 'docs', 'screens')
mkdirSync(OUT, { recursive: true })

async function shot(page: Page, route: string, name: string, waitMs = 2200) {
  await gotoApp(page, route)
  await page.waitForLoadState('domcontentloaded')
  await page.waitForTimeout(900)
  // dismiss any first-visit onboarding tour / modal so the screen is clean
  for (const label of ['Skip', 'Got it', 'Close']) {
    const btn = page.getByRole('button', { name: label, exact: false }).first()
    if (await btn.isVisible().catch(() => false)) { await btn.click().catch(() => {}); await page.waitForTimeout(300) }
  }
  await page.waitForTimeout(waitMs)            // let count-ups + charts settle
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
}

test('README gallery', async ({ page }) => {
  await shot(page, '/', 'overview')
  await shot(page, 'cost', 'analytics', 3200)
  await shot(page, 'playground', 'playground')
  await shot(page, 'admin/providers', 'providers')
  await shot(page, 'admin/keys', 'keys')
  await shot(page, 'admin/routing', 'routing', 3000)
  await shot(page, 'guardrails/providers', 'guardrails')
  await shot(page, 'observability', 'observability')
  await shot(page, 'logs', 'logs')
  await shot(page, 'live', 'live-requests')
  expect(true).toBe(true)
})
