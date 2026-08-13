import { expect, test } from '@playwright/test'
import { clickRowMenuItem, gotoApp, guardNo4xx, openRowMenu, snap, typeRealUser, uniqId } from './_helpers'

const ALLOW = [/\/events($|\?)/]
const ADMIN = { 'X-Admin-Token': 'platform-admin-secret', 'Content-Type': 'application/json' }

test('keys: portal RowMenu visible + clickable on a one-row list (the prior clipping bug)', async ({ page }) => {
  // Provision a DEDICATED throwaway workspace so this test is fully isolated
  // from other parallel workers (which would otherwise mutate keys in the
  // shared ws-novatech-payments and disable the row mid-test). Tear it down at end.
  const wsId = uniqId('ws-keys').toLowerCase()
  const guard = guardNo4xx(page, ALLOW)
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

  const create = await page.request.post('/admin/workspaces', {
    headers: ADMIN,
    data: {
      workspace_id: wsId, client_id: 'novatech', name: 'E2E Keys WS',
      chat_models: { 'claude-sonnet-4-5': [{ provider: 'bedrock', model_id: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0', weight: 1 }] },
      default_chat_alias: 'claude-sonnet-4-5',
    },
  })
  expect(create.ok()).toBeTruthy()

  try {
    await gotoApp(page, 'admin/keys')
    await expect(page.getByText('Admin · API Keys')).toBeVisible()
    // Select our dedicated workspace (it appears after the workspaces poll).
    await expect(async () => {
      await page.getByTestId('ws-picker').selectOption(wsId)
    }).toPass({ timeout: 10_000 })

    // Issue a key (label + roles + future date)
    await page.getByTestId('key-issue').click()
    await expect(page.getByTestId('key-issue-modal')).toBeVisible()
    await typeRealUser(page.getByTestId('key-label'), 'e2e-rowmenu-test')
    await page.getByTestId('key-no-expiry').click()
    await page.getByTestId('key-date').fill('2027-01-01')
    await page.getByTestId('key-issue-confirm').click()
    await expect(page.getByTestId('plaintext-modal')).toBeVisible({ timeout: 8_000 })
    const plaintext = await page.getByTestId('plaintext-value').innerText()
    expect(plaintext).toMatch(/^gw-/)
    await page.getByTestId('plaintext-copy').click()
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip).toBe(plaintext)
    await page.getByTestId('plaintext-close').click()

    // This workspace has exactly one key \u2014 grab its id from the single row.
    const row = page.locator('[data-testid^="key-row-"]').first()
    await expect(row).toBeVisible({ timeout: 8_000 })
    const keyId = (await row.getAttribute('data-testid'))!.replace('key-row-', '')

    // 📸 - keys list with the ⋯ trigger visible
    await snap(page, 'admin-keys-list')

    // The portal menu lands fully in the viewport even on a one-row list
    await openRowMenu(page, `key-menu-${keyId}`)

    // Rotate via the menu item (portal-positioned, clickable)
    await clickRowMenuItem(page, `key-menu-${keyId}`, `key-rotate-${keyId}`)
    await expect(page.getByTestId('confirm-rotate')).toBeVisible()
    await expect(page.getByTestId('confirm-rotate-go')).toBeInViewport()
    await page.getByTestId('confirm-rotate-go').click()
    // Fresh plaintext modal
    await expect(page.getByTestId('plaintext-modal')).toBeVisible({ timeout: 8_000 })
    const rotated = await page.getByTestId('plaintext-value').innerText()
    expect(rotated).toMatch(/^gw-/)
    expect(rotated).not.toBe(plaintext)
    await page.getByTestId('plaintext-close').click()

    // Disable via the menu item
    await clickRowMenuItem(page, `key-menu-${keyId}`, `key-disable-${keyId}`)
    await expect(page.getByTestId('confirm-disable')).toBeVisible()
    await page.getByTestId('confirm-disable-go').click()
    await expect(page.locator(`[data-testid="key-row-${keyId}"]`).getByText('disabled')).toBeVisible({ timeout: 8_000 })

    guard.assertClean()
  } finally {
    await page.request.delete(`/admin/workspaces/${wsId}`, { headers: ADMIN }).catch(() => {})
  }
})

test('keys: issue is rejected when label is empty or all roles unchecked (no half-cooked save)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  // Keys now defaults to an all-workspaces overview; pick a workspace so "Issue key"
  // is enabled (deep-link to a provisioned one).
  await gotoApp(page, `admin/keys?workspace=${process.env.E2E_WORKSPACE || 'san-bedrock-static-direct'}`)
  await expect(page.getByTestId('key-issue')).toBeEnabled({ timeout: 20_000 })
  await page.getByTestId('key-issue').click()
  // Empty label keeps Issue disabled
  await expect(page.getByTestId('key-issue-confirm')).toBeDisabled()
  // Fill label, then turn off member role → still disabled (no roles)
  await typeRealUser(page.getByTestId('key-label'), 'invalid-no-roles')
  await page.getByTestId('key-no-expiry').click()  // no expiry simplest
  await page.getByTestId('key-role-member').click()  // toggles off the default
  await expect(page.getByTestId('key-issue-confirm')).toBeDisabled()
  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})
