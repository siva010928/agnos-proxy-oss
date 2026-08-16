import { test, expect, Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Curated, README-facing gallery. Captures from the LIVE DEPLOYED gateway so the
// README shows the production-grade dashboard (not local). Credentials are read from
// the environment - never hardcode them. Run with:
//   DEPLOY_BASE=https://<your-gateway> DEPLOY_USER=admin DEPLOY_PASS=<admin-password> \
//     npx playwright test e2e/readme-deployed-screens.spec.ts --timeout=240000
const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '..', '..', 'docs', 'screens')
mkdirSync(OUT, { recursive: true })

const BASE = process.env.DEPLOY_BASE || 'http://localhost:8090'
const USER = process.env.DEPLOY_USER || 'admin'
const PASS = process.env.DEPLOY_PASS || ''

// Capture tooling, not CI validation. Skip at runtime (never throw at import) when
// DEPLOY_PASS is absent so merely collecting this file cannot fail a run. CI excludes
// it entirely via playwright.config testIgnore.
test.beforeEach(() => {
  test.skip(!PASS, 'Set DEPLOY_PASS (dashboard admin password) to run the deployed capture.')
})

async function deployedLogin(page: Page) {
  // Hit the API directly so the session cookie is set on this browser context;
  // every subsequent page.goto on the same context inherits the cookie.
  const r = await page.request.post(`${BASE}/auth/login`, {
    data: { username: USER, password: PASS },
    headers: { 'Content-Type': 'application/json' },
  })
  if (!r.ok()) throw new Error(`/auth/login failed: ${r.status()} ${await r.text()}`)
}

async function shot(page: Page, route: string, name: string, waitMs = 2500) {
  await page.goto(`${BASE}/app/${route.replace(/^\//, '')}`, { waitUntil: 'domcontentloaded' })
  await page.waitForLoadState('domcontentloaded')
  await page.waitForTimeout(900)
  // dismiss any first-visit onboarding tour / modal so the screen is clean
  for (const label of ['Skip', 'Got it', 'Close']) {
    const btn = page.getByRole('button', { name: label, exact: false }).first()
    if (await btn.isVisible().catch(() => false)) { await btn.click().catch(() => {}); await page.waitForTimeout(300) }
  }
  await page.waitForTimeout(waitMs)
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
}

test('README gallery (deployed)', async ({ page }) => {
  await deployedLogin(page)

  // ── Top-level operator screens ──────────────────────────────────────────────
  await shot(page, '/', 'overview')
  await shot(page, 'cost', 'analytics', 3500)
  await shot(page, 'live', 'live-requests')
  await shot(page, 'logs', 'logs')
  await shot(page, 'observability', 'observability')
  await shot(page, 'engine', 'engine-health')
  // Routing Map (the visualization page) - lives at /app/routing (admin/routing is
  // the alias-editor CRUD page; we capture that separately below).
  await shot(page, 'routing', 'routing-map')

  // Routing Map → Show visualization (graph view)
  await page.goto(`${BASE}/app/routing`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2200)
  const showViz = page.getByRole('button', { name: /show visualization/i }).first()
  if (await showViz.isVisible().catch(() => false)) {
    await showViz.click().catch(() => {})
    await page.waitForTimeout(2200)
  }
  await page.screenshot({ path: `${OUT}/routing-map-visualization.png`, fullPage: true })

  // ── Playground (a real OpenAI-compatible request playground) ────────────────
  await shot(page, 'playground', 'playground')

  // ── Admin / configuration screens ───────────────────────────────────────────
  await shot(page, 'admin/providers', 'providers')
  await shot(page, 'admin/keys', 'keys')
  await shot(page, 'admin/pricing', 'admin-pricing')
  await shot(page, 'admin/routing', 'routing', 3000)

  // ── Admin · Routing → Edit alias · claude-sonnet-4.5 (deep state) ──────────
  // Navigate to admin/routing, find the alias row for claude-sonnet-4.5, click Edit.
  await page.goto(`${BASE}/app/admin/routing`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2000)
  // try common patterns to open the alias editor
  const editTriggers = [
    page.getByRole('button', { name: /edit/i }).first(),
    page.getByText(/claude-sonnet-4-5|claude-sonnet-4\.5/i).first(),
  ]
  for (const t of editTriggers) {
    if (await t.isVisible().catch(() => false)) {
      try { await t.click(); break } catch { /* fallthrough */ }
    }
  }
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${OUT}/admin-routing-edit-alias.png`, fullPage: true })

  // ── ⚙️ Engine Override panel (lives further down on /admin/routing) ────────
  await page.goto(`${BASE}/app/admin/routing`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2000)
  const engineHeader = page.getByText(/Engine Override/i).first()
  if (await engineHeader.isVisible().catch(() => false)) {
    await engineHeader.scrollIntoViewIfNeeded().catch(() => {})
    await page.waitForTimeout(800)
  }
  await page.screenshot({ path: `${OUT}/admin-engine-override.png`, fullPage: true })

  // ── Guardrails · Detector Profiles (catalog) ────────────────────────────────
  await shot(page, 'guardrails/providers', 'guardrails-detectors')

  // ── Detector Profiles → AWS Bedrock Guardrails (configure detail) ──────────
  await page.goto(`${BASE}/app/guardrails/providers`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2200)
  // Scope the click to the AWS Bedrock card specifically (otherwise the first
  // "configure →" link wins and we land on Custom Regex).
  const bedrockCard = page.locator('div').filter({ hasText: /^AWS Bedrock Guardrails/ }).filter({ hasText: /configure/i }).first()
  if (await bedrockCard.count() > 0) {
    await bedrockCard.locator('text=/configure/i').first().click().catch(() => {})
    await page.waitForTimeout(2200)
  } else {
    // Fallback: scope by ancestor that contains both the card name and a configure link
    const cfg = page.locator('a, button').filter({ hasText: /configure/i }).nth(4)   // 5th card = AWS Bedrock
    await cfg.click().catch(() => {})
    await page.waitForTimeout(2200)
  }
  await page.screenshot({ path: `${OUT}/guardrails-bedrock-config.png`, fullPage: true })

  // ── Guardrails · Configuration → editing CEL (rule editor) ─────────────────
  await page.goto(`${BASE}/app/guardrails/rules`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2200)
  // Prefer EDITING an existing rule (it lands directly on the CEL state); fall
  // back to creating a new one and scrolling down to the CEL editor section.
  const editExisting = page.getByRole('button', { name: /^edit$/i }).first()
  if (await editExisting.isVisible().catch(() => false)) {
    await editExisting.click().catch(() => {})
  } else {
    const newBtn = page.getByRole('button', { name: /new rule|create your first rule|create rule/i }).first()
    if (await newBtn.isVisible().catch(() => false)) await newBtn.click().catch(() => {})
  }
  await page.waitForTimeout(2200)
  // try to reveal the CEL editor (it lives in step 2 / "When (CEL)")
  for (const label of [/cel/i, /when.*CEL/i, /condition/i, /next/i]) {
    const t = page.getByText(label).first()
    if (await t.isVisible().catch(() => false)) {
      await t.scrollIntoViewIfNeeded().catch(() => {})
      await page.waitForTimeout(300)
    }
  }
  await page.screenshot({ path: `${OUT}/guardrails-rule-editor.png`, fullPage: true })

  expect(true).toBe(true)
})
