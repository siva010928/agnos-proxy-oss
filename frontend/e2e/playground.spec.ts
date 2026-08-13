import { expect, test } from '@playwright/test'
import { gotoApp, guardNo4xx, snap } from './_helpers'

const ALLOW = [/\/events($|\?)/]

// Suppress the first-visit onboarding tour so it doesn't intercept clicks.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('agnos_playground_tour_done', '1')
  })
})

test('playground: onboarding tour appears on first visit', async ({ page, context }) => {
  // Override the beforeEach by clearing the flag before this single test
  await context.clearCookies()
  await page.addInitScript(() => { localStorage.removeItem('agnos_playground_tour_done') })

  await gotoApp(page, 'playground')
  await page.waitForTimeout(800)

  // Tour overlay should be visible
  const tourOverlay = page.getByTestId('playground-tour-overlay')
  await expect(tourOverlay).toBeVisible()
  await expect(page.getByText('Welcome to Agnos - the LLM governance layer')).toBeVisible()

  // Step through the tour
  for (let i = 0; i < 4; i++) {
    await page.getByTestId('tour-next').click()
    await page.waitForTimeout(150)
  }
  // Last step says "Let's go" - clicking it dismisses the tour
  await page.getByTestId('tour-next').click()
  await expect(tourOverlay).not.toBeVisible()
})

test('playground: dropdowns populate + can run a real request', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)

  await gotoApp(page, 'playground')

  // Header is visible
  await expect(page.getByText('See exactly what Agnos does to an LLM request')).toBeVisible()

  // Step 1 - scenario picker is visible with grouped sections
  await expect(page.getByText('STEP 1', { exact: true })).toBeVisible()
  await expect(page.getByText('Success scenarios')).toBeVisible()
  await expect(page.getByText(/Guardrail scenarios/)).toBeVisible()
  await expect(page.getByText(/Failure scenarios/)).toBeVisible()

  // CONTEXT: Client + Workspace dropdowns must have at least one option each.
  // Both are <select> elements inside the CONTEXT (impersonation) card.
  const contextCard = page.locator('div').filter({ hasText: 'Who is making this request?' }).first()
  const clientSelect = contextCard.locator('select').first()
  const workspaceSelect = contextCard.locator('select').nth(1)

  // Wait for the workspaces fetch to complete and populate
  await expect.poll(async () => (await clientSelect.locator('option').count()), {
    message: 'Client dropdown should have at least 1 option',
    timeout: 10_000,
  }).toBeGreaterThan(0)
  await expect.poll(async () => (await workspaceSelect.locator('option').count()), {
    message: 'Workspace dropdown should have at least 1 option',
    timeout: 10_000,
  }).toBeGreaterThan(0)

  // Step 2 - framework chips (5 frameworks)
  await expect(page.getByText('STEP 2', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'OpenAI SDK' })).toBeVisible()
  await expect(page.getByRole('button', { name: /LangChain/ })).toBeVisible()

  // Step 3 - prompt textarea + Run button
  await expect(page.getByText('STEP 3', { exact: true })).toBeVisible()
  const promptTextarea = page.locator('textarea').first()
  await expect(promptTextarea).toBeVisible()

  // Pick the Normal scenario (default) and run
  const normalBtn = page.locator('button').filter({ hasText: 'Normal Request' }).first()
  await normalBtn.click()
  // The prompt should pre-fill
  await expect(promptTextarea).not.toBeEmpty()

  // Click Run
  const runBtn = page.locator('button').filter({ hasText: /^Run/ }).first()
  await runBtn.click()

  // The Response panel should populate (success or error - both are acceptable
  // for this test; we just need to confirm the UI fully wired up).
  await expect(page.locator('text=Response').first()).toBeVisible()

  // Wait for completion (up to 30s for Claude)
  await expect.poll(async () => {
    const text = await page.textContent('body')
    return /Completed in|Failed|HTTP/.test(text || '')
  }, { timeout: 30_000 }).toBe(true)

  await snap(page, 'playground-after-run')
  guard.assertClean()
})


test('playground: scenario picker pre-fills prompt with example', async ({ page }) => {
  await gotoApp(page, 'playground')
  await page.waitForTimeout(800)

  const promptTextarea = page.locator('textarea').first()

  // Pick AWS Secret scenario
  const awsBtn = page.locator('button').filter({ hasText: /AWS Secret/ }).first()
  await awsBtn.click()
  await expect(promptTextarea).toContainText('AKIAIOSFODNN7EXAMPLE')

  // Pick Phone scenario
  const phoneBtn = page.locator('button').filter({ hasText: /Phone Number/ }).first()
  await phoneBtn.click()
  await expect(promptTextarea).toContainText(/\(415\) 555-2671/)
})


test('playground: collapsible "Why this exists" panel', async ({ page }) => {
  await gotoApp(page, 'playground')
  await page.waitForTimeout(500)

  const whyButton = page.getByRole('button', { name: /Why this exists/ })
  await expect(whyButton).toBeVisible()

  // Hidden initially
  await expect(page.getByText('The problem we set out to solve')).not.toBeVisible()

  // Open it
  await whyButton.click()
  await expect(page.getByText('The problem we set out to solve')).toBeVisible()
  await expect(page.getByText('Our solution')).toBeVisible()
  await expect(page.getByText('What it solves, concretely')).toBeVisible()
  await expect(page.getByText('What this page proves')).toBeVisible()
})
