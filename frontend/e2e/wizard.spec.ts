import { expect, test } from '@playwright/test'
import { gotoApp, guardNo4xx, snap, typeRealUser, uniqId } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Real Anthropic key for the wizard's live Test-Connection step. Sourced from
// the environment (loaded from the gitignored root .env by playwright.config)
// so we never commit a live secret. If absent, the full-onboarding test is
// skipped rather than failing with a confusing 401.
const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY || ''

test('wizard: full 6-step guided onboarding using ONLY shared editors', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  test.skip(!ANTHROPIC_KEY, 'ANTHROPIC_API_KEY not set - skipping live onboarding test (set it in root .env)')
  const wsId = uniqId('e2e-ws').toLowerCase()
  const aliasName = 'claude-sonnet-4-5'

  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await gotoApp(page, 'admin')
  await expect(page.getByText('Admin · Onboarding')).toBeVisible()
  await page.getByTestId('open-wizard').click()
  await expect(page.getByTestId('wizard')).toBeVisible()

  // Step 1 - identity (CLIENT + slug + display name)
  await expect(page.getByTestId('wizard-next')).toBeDisabled()  // empty wsId + no client blocks Next
  await page.getByTestId('wizard-client-id').selectOption('novatech')
  await typeRealUser(page.getByTestId('wizard-ws-id'), wsId)
  await typeRealUser(page.getByTestId('wizard-ws-name'), 'E2E Workspace')
  await expect(page.getByTestId('wizard-next')).toBeEnabled()
  await page.getByTestId('wizard-next').click()

  // Step 2 - first provider (anthropic, real Test must pass)
  await page.getByTestId('wizard-provider-option-anthropic').click()
  await typeRealUser(page.getByTestId('wizard-provider-field-api_key'), ANTHROPIC_KEY)
  await typeRealUser(page.getByTestId('wizard-provider-alias'), aliasName)
  // Default model id pre-filled by ProviderEditor
  // Run Test Connection - must pass before Next is enabled
  await expect(page.getByTestId('wizard-next')).toBeDisabled()
  await page.getByTestId('wizard-provider-test').click()
  await expect(page.getByTestId('wizard-provider-test-pass')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('wizard-next')).toBeEnabled()
  await snap(page, 'admin-wizard-step2-provider-green')
  await page.getByTestId('wizard-next').click()

  // Step 3 - routing (alias from Step 2 was pre-populated)
  await expect(page.getByTestId(`wizard-aliases-alias-${aliasName}`)).toBeVisible()
  await snap(page, 'admin-wizard-step3-routing')
  await page.getByTestId('wizard-next').click()

  // Step 4 - guardrails (mode 'block' default; PII pre-checked)
  await expect(page.getByTestId('wizard-guardrails-mode-block')).toBeVisible()
  await page.getByTestId('wizard-next').click()

  // Step 5 - limits & budget (defaults are fine)
  await page.getByTestId('wizard-next').click()

  // Step 6 - issue first key
  await typeRealUser(page.getByTestId('wizard-key-label'), 'first-key')
  // No expiry checkbox: leave UNCHECKED → must pick a date
  // Default state has no_expiry === false (the form initializes expires_at=null,
  // setNoExpiry(true) - so we toggle off to require a date)
  // Simpler: toggle on (no expiry) which is valid for the form
  await page.getByTestId('wizard-key-no-expiry').click()  // toggles off → date required
  await page.getByTestId('wizard-key-date').fill('2027-01-01')
  await expect(page.getByTestId('wizard-finish')).toBeEnabled()
  await page.getByTestId('wizard-finish').click()

  // Done - plaintext key shown once. finish() does real upstream work
  // (create workspace → register the provider key in Bifrost → issue key), and
  // the Bifrost sync can take a while under load, so allow a generous window.
  await expect(page.getByTestId('wizard-issued-key')).toBeVisible({ timeout: 60_000 })
  const plaintext = await page.getByTestId('wizard-issued-key').innerText()
  expect(plaintext).toMatch(/^gw-/)
  await page.getByTestId('wizard-copy-key').click()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toBe(plaintext)
  await snap(page, 'admin-wizard-done')

  await page.getByTestId('wizard-done').click()

  // The new workspace is in the list (look in the table specifically)
  await expect(page.locator('table').getByText(wsId)).toBeVisible({ timeout: 8_000 })

  guard.assertClean()
})

test('wizard: invalid workspace_id slug blocks Next (no network call)', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)
  await gotoApp(page, 'admin')
  await page.getByTestId('open-wizard').click()
  // Select a client (required) but enter an invalid slug with spaces
  await page.getByTestId('wizard-client-id').selectOption('novatech')
  await typeRealUser(page.getByTestId('wizard-ws-id'), 'has spaces')
  await expect(page.getByTestId('wizard-next')).toBeDisabled()
  await page.locator('button:has-text("Cancel")').first().click()
  guard.assertClean()
})
