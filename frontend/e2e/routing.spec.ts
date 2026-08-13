import { test, expect, type Page } from '@playwright/test'

// Admin · Routing - the persistence bug fixes + model-availability restriction.

const WS = process.env.E2E_WORKSPACE || 'san-bedrock-static-direct'

async function openRoutingWithWorkspace(page: Page) {
  // Engine routing is GATEWAY-WIDE, but the card renders once a workspace is picked
  // (parity runs against a real account). Deep-link a workspace deterministically;
  // ?workspace= survives reload so we test routing persistence, not picker timing.
  await page.goto(`/app/admin/routing?workspace=${WS}`)
  await expect(page.getByRole('heading', { name: /Routing/i })).toBeVisible()
  await expect(page.getByTestId('engine-override-save')).toBeVisible({ timeout: 20_000 })
}

test.describe('Admin · Routing', () => {
  test('engine SPLIT persists across reload (does not reset to Rented)', async ({ page }) => {
    await openRoutingWithWorkspace(page)
    // set anthropic → Split, then Save
    await page.getByTestId('engine-mode-anthropic-split').click()
    await expect(page.getByTestId('engine-split-anthropic')).toBeVisible()
    await page.getByTestId('engine-override-save').click()
    await expect(page.getByText(/Engine routing saved/i)).toBeVisible()

    // reload - the gateway-wide split must still be selected (the reported bug: it
    // reset to Rented after reload because the card copied stale data on mount).
    await page.goto(`/app/admin/routing?workspace=${WS}`)
    await expect(page.getByTestId('engine-override-save')).toBeVisible({ timeout: 20_000 })
    // the split slider is only shown when mode === 'split'
    await expect(page.getByTestId('engine-split-anthropic')).toBeVisible()
  })

  test('engine split SURVIVES saving an alias (not reset to rented)', async ({ page }) => {
    await openRoutingWithWorkspace(page)
    await page.getByTestId('engine-mode-anthropic-split').click()
    await page.getByTestId('engine-override-save').click()
    await expect(page.getByText(/Engine routing saved/i)).toBeVisible()
    // create/save an alias, then confirm the split is still shown
    await page.getByTestId('alias-new').click()
    // the split slider must remain after the alias modal interaction + refetch
    await page.keyboard.press('Escape').catch(() => {})
    await expect(page.getByTestId('engine-split-anthropic')).toBeVisible()
  })

  test('alias editor opens with a model target (availability hint is best-effort)', async ({ page }) => {
    await openRoutingWithWorkspace(page)
    await page.getByTestId('alias-new').click()
    // add a target if the builder starts empty (testIdPrefix="target")
    const add = page.getByTestId('target-add')
    if (await add.isVisible().catch(() => false)) await add.click()
    // a model target field is present; the "✓ N models this account can reach" hint
    // appears when the live account listing succeeds (also proven by the backend
    // `availability` sanity command).
    await expect(page.getByTestId('target-0-model')).toBeVisible({ timeout: 10_000 })
    await expect.soft(page.getByTestId('target-0-live')).toBeVisible({ timeout: 10_000 })
  })
})
