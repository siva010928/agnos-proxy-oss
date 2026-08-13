import { expect, test } from '@playwright/test'
import { clickRowMenuItem, gotoApp, guardNo4xx, openRowMenu, snap, typeRealUser, uniqId } from './_helpers'

const ALLOW = [/\/events($|\?)/]

test('custom pricing: PricingOverrideForm + portal RowMenu edit/delete', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  const substr = uniqId('e2e-pricing').toLowerCase()

  await gotoApp(page, 'admin/pricing')
  await expect(page.getByText('Admin · Custom Pricing')).toBeVisible()
  await expect(page.getByTestId('pricing-list')).toBeVisible({ timeout: 8_000 })

  // 📸 - catalog
  await snap(page, 'admin-pricing-catalog')

  // Create a new override
  await page.getByTestId('pricing-new').click()
  await expect(page.getByTestId('pricing-editor')).toBeVisible()
  // The override form now scopes models by provider: pick one first (this enables
  // the model/substring input and lists only that provider's models). Scope to the
  // editor - the page also has a 'pricing-provider' filter with the same testid.
  await page.getByTestId('pricing-editor').getByTestId('pricing-provider').selectOption('bedrock')
  await typeRealUser(page.getByTestId('pricing-substr'), substr)
  // Fill prices
  await page.getByTestId('pricing-input').fill('0.001234')
  await page.getByTestId('pricing-output').fill('0.005678')
  await typeRealUser(page.getByTestId('pricing-note'), 'e2e demo override')
  await page.getByTestId('pricing-save').click()
  await expect(page.getByTestId('toast-success').first()).toBeVisible({ timeout: 5_000 })

  // Override appears
  const row = page.getByTestId(`override-row-${substr}`)
  await expect(row).toBeVisible({ timeout: 5_000 })

  // Open the portal RowMenu and click Edit
  await clickRowMenuItem(page, `override-menu-${substr}`, `override-edit-${substr}`)
  await expect(page.getByTestId('pricing-editor')).toBeVisible()
  // Edit the note (substr is disabled in edit mode)
  await typeRealUser(page.getByTestId('pricing-note'), 'edited note')
  await page.locator('button:has-text("Cancel")').first().click()

  // Delete via RowMenu → ConfirmModal
  await clickRowMenuItem(page, `override-menu-${substr}`, `override-delete-${substr}`)
  await expect(page.getByTestId('pricing-confirm-delete')).toBeVisible()
  await page.getByTestId('pricing-confirm-delete-go').click()
  await expect(page.getByTestId(`override-row-${substr}`)).toHaveCount(0, { timeout: 8_000 })

  guard.assertClean()
})

test('custom pricing: empty substr + length<3 blocks Save (server validation safety net)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'admin/pricing')
  await page.getByTestId('pricing-new').click()
  await expect(page.getByTestId('pricing-editor')).toBeVisible()

  // Empty substring → save disabled
  await expect(page.getByTestId('pricing-save')).toBeDisabled()

  // pick a provider to enable the model/substring input (scope to the editor - the
  // page filter shares the 'pricing-provider' testid)
  await page.getByTestId('pricing-editor').getByTestId('pricing-provider').selectOption('bedrock')
  // 2-char substring → still disabled
  await typeRealUser(page.getByTestId('pricing-substr'), 'ab')
  await page.getByTestId('pricing-input').fill('1')
  await expect(page.getByTestId('pricing-save')).toBeDisabled()

  // Both prices 0 → still disabled (no-op override rejected)
  await typeRealUser(page.getByTestId('pricing-substr'), 'claude-sonnet-zero-test')
  await page.getByTestId('pricing-input').fill('0')
  await page.getByTestId('pricing-output').fill('0')
  await expect(page.getByTestId('pricing-save')).toBeDisabled()

  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})
