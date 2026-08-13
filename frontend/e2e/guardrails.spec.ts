import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gotoApp, guardNo4xx, snap, typeRealUser, uniqId } from './_helpers'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ALLOW = [/\/events($|\?)/]
const ADMIN = { 'X-Admin-Token': 'platform-admin-secret', 'Content-Type': 'application/json' }

function envFromDotenv(key: string): string {
  const dotenv = readFileSync(resolve(__dirname, '../../.env'), 'utf8')
  const m = dotenv.match(new RegExp(`^${key}=\\"?([^"\\n]+)\\"?$`, 'm'))
  if (!m) throw new Error(`missing ${key} in .env`)
  return m[1]
}

// ─────────────────────────────────────────────────────────────────────
// Configuration page - visual rule builder + live CEL preview + test panel
// ─────────────────────────────────────────────────────────────────────

test('rules: visual builder, live CEL, real Test, save, edit-mode focus, delete', async ({ page, request }) => {
  const guard = guardNo4xx(page, ALLOW)
  const ruleName = uniqId('e2e-rule')
  const profileName = uniqId('e2e-prof')

  // Pre-create a profile via API so the rule editor's multi-select can link it
  const r = await request.post('/admin/guardrails/profiles', {
    headers: ADMIN,
    data: {
      name: profileName, detector_type: 'regex', enabled: true,
      // server now validates regex profiles must carry a pattern (no dead config)
      config: { pattern: '\\d{3}-\\d{2}-\\d{4}' },
    },
  })
  expect(r.ok()).toBeTruthy()
  const { id: profileId } = await r.json()

  await gotoApp(page, 'guardrails/rules')
  await expect(page.getByText('Guardrails · Configuration')).toBeVisible()

  // open new-rule editor
  await page.getByTestId('rule-new').click()
  await expect(page.getByTestId('rule-editor')).toBeVisible()

  // identity (typed char-by-char with focus + value asserts)
  await typeRealUser(page.getByTestId('rule-name'), ruleName)
  await typeRealUser(page.getByTestId('rule-description'), 'E2E rule - model+workspace condition')

  // ── visual builder: condition #1 - model contains "claude" ──
  await page.getByTestId('add-condition').click()
  await expect(page.getByTestId('cond-0')).toBeVisible()
  await typeRealUser(page.getByTestId('cond-0-value'), 'claude')

  // condition #2 - workspace equals "ws-novatech-payments"
  await page.getByTestId('add-condition').click()
  await page.getByTestId('cond-1-attr').selectOption('workspace')
  await page.getByTestId('cond-1-op').selectOption('eq')
  await typeRealUser(page.getByTestId('cond-1-value'), 'ws-novatech-payments')

  // CEL preview reflects builder state with AND combinator
  const cel = page.getByTestId('cel-preview')
  await expect(cel).toContainText('request.model.contains("claude")')
  await expect(cel).toContainText('request.workspace == "ws-novatech-payments"')
  await expect(cel).toContainText('&&')

  // toggle to OR
  await page.getByTestId('combinator-or').click()
  await expect(cel).toContainText('||')
  // server-side validate → ✓ valid badge appears
  await expect(page.getByTestId('cel-valid')).toBeVisible({ timeout: 5_000 })

  // 📸 editor with builder + CEL preview
  await snap(page, 'guardrails-rule-editor-builder')

  // link the pre-created profile
  await page.getByTestId('rule-profiles').click()
  await page.getByTestId(`profile-option-${profileId}`).click()
  await expect(page.getByTestId(`profile-chip-${profileId}`)).toBeVisible()

  // ── Test panel: real evaluation with sample SSN content ──
  await typeRealUser(page.getByTestId('rule-test-content'), 'My SSN is 123-45-6789 and email is bob@test.com')
  await page.getByTestId('rule-test-run').click()
  await expect(page.getByTestId('rule-test-result')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByTestId('rule-test-result')).toContainText(/violation/)
  await expect(page.getByTestId('rule-test-result')).toContainText(/regex_pii|ssn/)

  // 📸 Test panel result
  await page.getByTestId('rule-test-result').scrollIntoViewIfNeeded()
  await snap(page, 'guardrails-rule-test-panel')

  // Scope: new rules default to workspace-scoped - pick a workspace so Save enables.
  await page.getByTestId('rule-scope-workspace-select').selectOption('ws-novatech-payments')

  // Save
  await page.getByTestId('rule-save').click()
  await expect(page.getByTestId('toast-success').first()).toBeVisible({ timeout: 5_000 })
  await expect(page.getByTestId('rule-list').getByText(ruleName)).toBeVisible({ timeout: 10_000 })

  // 📸 Rules list with the new rule
  await snap(page, 'guardrails-rules-list')

  // ── Edit-mode focus retention: reopen the saved rule, retype name char-by-char ──
  const row = page.locator('[data-testid^="rule-row-"]', { hasText: ruleName }).first()
  await row.click()
  await expect(page.getByTestId('rule-editor')).toBeVisible()
  await typeRealUser(page.getByTestId('rule-name'), `${ruleName}-edited`)
  // close without saving
  await page.locator('button:has-text("Cancel")').click()
  await expect(page.getByTestId('rule-editor')).toHaveCount(0)

  // Delete via the persistent, always-visible row action (no hidden menu)
  const rowAfter = page.locator('[data-testid^="rule-row-"]', { hasText: ruleName }).first()
  const rid = (await rowAfter.getAttribute('data-testid'))!.replace('rule-row-', '')
  await page.getByTestId(`rule-delete-${rid}`).click()
  await expect(page.getByTestId('rule-list').getByText(ruleName)).toHaveCount(0, { timeout: 10_000 })

  // cleanup the pre-created profile
  await request.delete(`/admin/guardrails/profiles/${profileId}`, { headers: ADMIN })

  guard.assertClean()
})

// ─────────────────────────────────────────────────────────────────────
// Providers page - catalog + Custom Regex with PII template
// ─────────────────────────────────────────────────────────────────────

test('providers: catalog renders + Custom Regex with PII template (edit-mode focus)', async ({ page, request }) => {
  const guard = guardNo4xx(page, ALLOW)
  const profileName = uniqId('e2e-pii')

  await gotoApp(page, 'guardrails/providers')
  await expect(page.getByText('Guardrails · Providers')).toBeVisible()

  // catalog renders the full set
  for (const t of ['regex', 'secrets', 'keyword', 'presidio', 'bedrock', 'azure']) {
    await expect(page.getByTestId(`provider-card-${t}`)).toBeVisible()
  }
  // The 4 "COMING SOON" stubs (model-armor / patronus / crowdstrike / grayswan)
  // were removed in WAVE 19 \u2014 the catalog now shows only currently-supported
  // detector providers.

  // 📸 catalog
  await snap(page, 'guardrails-providers-catalog')

  // Click Custom Regex card → detail page
  await page.getByTestId('provider-card-regex').click()
  await expect(page.getByRole('heading', { name: 'Custom Regex' }).first()).toBeVisible()

  // Add configuration
  await page.getByTestId('profile-new').click()
  await expect(page.getByTestId('profile-editor')).toBeVisible()
  await typeRealUser(page.getByTestId('profile-name'), profileName)

  // Apply PII template - pre-fills 5 patterns
  await page.getByTestId('regex-pii-template').click()
  await expect(page.getByTestId('regex-row-0')).toBeVisible()
  await expect(page.getByTestId('regex-row-4')).toBeVisible()

  // 📸 Custom Regex config with PII template applied
  await snap(page, 'guardrails-provider-config-regex')

  // Save
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('toast-success').first()).toBeVisible({ timeout: 5_000 })
  await expect(page.getByTestId('profile-list').getByText(profileName)).toBeVisible({ timeout: 8_000 })

  // ── Edit-mode focus retention: open existing profile, retype a pattern key ──
  const row = page.locator('[data-testid^="profile-row-"]', { hasText: profileName }).first()
  await row.click()
  await expect(page.getByTestId('profile-editor')).toBeVisible()
  await typeRealUser(page.getByTestId('regex-key-0'), 'email_v2')
  await page.locator('button:has-text("Cancel")').click()
  await expect(page.getByTestId('profile-editor')).toHaveCount(0)

  // delete via the row's trash button
  const rowAfter = page.locator('[data-testid^="profile-row-"]', { hasText: profileName }).first()
  const pid = (await rowAfter.getAttribute('data-testid'))!.replace('profile-row-', '')
  await page.getByTestId(`profile-delete-${pid}`).click()
  await expect(page.getByTestId('profile-list').getByText(profileName)).toHaveCount(0, { timeout: 8_000 })

  guard.assertClean()
})

// ─────────────────────────────────────────────────────────────────────
// Providers page - AWS Bedrock Guardrails: gated Test button + real probe
// ─────────────────────────────────────────────────────────────────────

test('providers: Bedrock config - gated Test, bogus → red, valid → green', async ({ page, request }) => {
  const guard = guardNo4xx(page, ALLOW)
  const AK = envFromDotenv('AWS_ACCESS_KEY_ID')
  const SK = envFromDotenv('AWS_SECRET_ACCESS_KEY')
  const profileName = uniqId('e2e-bedrock')

  await gotoApp(page, 'guardrails/providers')
  await page.getByTestId('provider-card-bedrock').click()
  await page.getByTestId('profile-new').click()
  await expect(page.getByTestId('profile-editor')).toBeVisible()

  await typeRealUser(page.getByTestId('profile-name'), profileName)

  // Test disabled with empty creds (server enforces "no env fallback" too)
  await expect(page.getByTestId('bedrock-test')).toBeDisabled()

  // Type only access_key - still disabled (secret + guardrail_id missing)
  await typeRealUser(page.getByTestId('bedrock-access-key'), AK)
  await expect(page.getByTestId('bedrock-test')).toBeDisabled()

  // Fill remaining required fields
  await typeRealUser(page.getByTestId('bedrock-secret-key'), SK)
  await typeRealUser(page.getByTestId('bedrock-guardrail-id'), 'eqc0uwdzzf6m')
  await expect(page.getByTestId('bedrock-test')).toBeEnabled()

  // 📸 Bedrock config form fully filled
  await snap(page, 'guardrails-provider-config-bedrock')

  // Click Test → green with real latency (now calls the real ApplyGuardrail API)
  await page.getByTestId('bedrock-test').click()
  await expect(page.getByText(/reachable · /)).toBeVisible({ timeout: 30_000 })

  // Save
  await page.getByTestId('profile-save').click()
  await expect(page.getByTestId('toast-success').first()).toBeVisible({ timeout: 5_000 })
  await expect(page.getByTestId('profile-list').getByText(profileName)).toBeVisible({ timeout: 8_000 })

  // cleanup
  const rowAfter = page.locator('[data-testid^="profile-row-"]', { hasText: profileName }).first()
  const pid = (await rowAfter.getAttribute('data-testid'))!.replace('profile-row-', '')
  await page.getByTestId(`profile-delete-${pid}`).click()

  guard.assertClean()
})
