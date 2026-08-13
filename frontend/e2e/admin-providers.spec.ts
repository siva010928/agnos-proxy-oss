import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gotoApp, guardNo4xx, snap, typeRealUser } from './_helpers'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ALLOW = [/\/events($|\?)/]

function envFromDotenv(key: string): string {
  const dotenv = readFileSync(resolve(__dirname, '../../.env'), 'utf8')
  const m = dotenv.match(new RegExp(`^${key}=\\"?([^"\\n]+)\\"?$`, 'm'))
  if (!m) throw new Error(`missing ${key} in .env`)
  return m[1]
}

const ANTHROPIC_KEY = envFromDotenv('ANTHROPIC_API_KEY')

// Providers now defaults to an ALL-WORKSPACES overview (no workspace forced), so
// "Add provider" is enabled only once a specific workspace is chosen. Deep-link to
// a known provisioned workspace so the editor opens deterministically.
const E2E_WS = process.env.E2E_WORKSPACE || 'san-bedrock-static-direct'

async function openProviderEditor(page: any, provider: string) {
  await gotoApp(page, `admin/providers?workspace=${E2E_WS}`)
  await expect(page.getByText('Admin · Providers')).toBeVisible()
  await expect(page.getByTestId('provider-new')).toBeEnabled({ timeout: 20_000 })
  await page.getByTestId('provider-new').click()
  await expect(page.getByTestId('provider-editor')).toBeVisible()
  await page.getByTestId(`provider-option-${provider}`).click()
}

test('providers: list renders + portal-RowMenu lands in viewport', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'admin/providers')
  await expect(page.getByText('Admin · Providers')).toBeVisible()
  await snap(page, 'admin-providers-list')
  guard.assertClean()
})

test('providers: Save is HARD-BLOCKED until Test Connection passes', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await openProviderEditor(page, 'anthropic')

  // Save disabled with no creds at all
  await expect(page.getByTestId('provider-save')).toBeDisabled()
  await expect(page.getByTestId('provider-test')).toBeDisabled()

  // Fill a bogus key - Test enabled, Save still disabled
  await typeRealUser(page.getByTestId('provider-field-api_key'), 'sk-ant-INVALID-NOT-REAL')
  await expect(page.getByTestId('provider-test')).toBeEnabled()
  await expect(page.getByTestId('provider-save')).toBeDisabled()

  // Bogus → red, Save STILL disabled (the lock-in: red Test must not allow Save)
  await page.getByTestId('provider-test').click()
  await expect(page.getByTestId('provider-test-fail')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('provider-save')).toBeDisabled()

  // Valid → green, NOW Save is enabled
  await typeRealUser(page.getByTestId('provider-field-api_key'), ANTHROPIC_KEY)
  // typing changed creds → testStatus reset to untested → Save disabled again
  await expect(page.getByTestId('provider-save')).toBeDisabled()
  await page.getByTestId('provider-test').click()
  await expect(page.getByTestId('provider-test-pass')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('provider-save')).toBeEnabled()

  await snap(page, 'admin-providers-anthropic-green')
  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})

test('providers: bedrock requires both keys + region (UI gating)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await openProviderEditor(page, 'bedrock')
  // Bedrock pre-fills region 'us-east-1' (single source of truth in PROVIDER_SPEC).
  // Test should still be DISABLED because access_key + secret_key are blank.
  await expect(page.getByTestId('provider-test')).toBeDisabled()
  await typeRealUser(page.getByTestId('provider-field-access_key'), 'AKIATEST')
  await expect(page.getByTestId('provider-test')).toBeDisabled()
  await typeRealUser(page.getByTestId('provider-field-secret_key'), 'secret')
  await expect(page.getByTestId('provider-test')).toBeEnabled()
  // Clear region - Test should disable again
  await page.getByTestId('provider-field-region').selectOption('')
  await expect(page.getByTestId('provider-test')).toBeDisabled()
  await page.getByTestId('provider-field-region').selectOption('us-east-1')
  await expect(page.getByTestId('provider-test')).toBeEnabled()
  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})

test('providers: azure requires endpoint URL (UI validation)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await openProviderEditor(page, 'azure')
  await expect(page.getByTestId('provider-test')).toBeDisabled()
  await typeRealUser(page.getByTestId('provider-field-api_key'), 'azure-key')
  await expect(page.getByTestId('provider-test')).toBeDisabled()
  await typeRealUser(page.getByTestId('provider-field-endpoint'), 'https://my-resource.openai.azure.com')
  await expect(page.getByTestId('provider-test')).toBeEnabled()
  // Bogus key + endpoint → red real upstream error
  await page.getByTestId('provider-test').click()
  await expect(page.getByTestId('provider-test-fail')).toBeVisible({ timeout: 30_000 })
  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})
