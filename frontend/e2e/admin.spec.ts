import { test, expect } from '@playwright/test'

// Smoke + key admin surfaces: pages load authenticated, pricing search finds the
// full litellm ids, hierarchical pickers default to all-workspaces on Keys/Providers.

test('authenticated dashboard loads (overview)', async ({ page }) => {
  await page.goto('/app/')
  // not bounced to the story/login (we're authenticated via storageState)
  await expect(page).toHaveURL(/\/app\//)
})

test('Custom Pricing: full litellm id (us.anthropic.*) is findable via search', async ({ page }) => {
  await page.goto('/app/admin/pricing')
  await expect(page.getByRole('heading', { name: /Pricing/i })).toBeVisible()
  // provider filter → bedrock, search the full inference-profile id
  const provider = page.getByTestId('pricing-provider')
  if (await provider.isVisible().catch(() => false)) await provider.selectOption('bedrock')
  const search = page.getByTestId('pricing-search').or(page.getByPlaceholder(/Search model/i))
  await search.fill('us.anthropic.claude-sonnet-4-5')
  // a matching row appears (the id was previously cut off by the 300-cap)
  await expect(page.getByText(/us\.anthropic\.claude-sonnet-4-5/).first()).toBeVisible()
})

test('Custom Pricing: per-1M / per-1K unit toggle works', async ({ page }) => {
  await page.goto('/app/admin/pricing')
  await expect(page.getByText('per 1M').first()).toBeVisible()
  await page.getByText('per 1K', { exact: false }).first().click()
  await expect(page.getByText(/\/1K/).first()).toBeVisible()
})

test('Admin · Keys defaults to All-workspaces overview (browse), not a forced workspace', async ({ page }) => {
  await page.goto('/app/admin/keys')
  await expect(page.getByRole('heading', { name: /API Keys/i })).toBeVisible()
  // the ws-picker "All workspaces" is the neutral default → overview list renders
  const overview = page.getByTestId('key-overview')
  await expect.soft(overview).toBeVisible()
})

test('Admin · Providers defaults to All-workspaces overview', async ({ page }) => {
  await page.goto('/app/admin/providers')
  await expect(page.getByRole('heading', { name: /Providers/i })).toBeVisible()
  await expect.soft(page.getByTestId('provider-overview')).toBeVisible()
})
