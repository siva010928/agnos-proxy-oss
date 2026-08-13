import { expect, test } from '@playwright/test'
import { gotoApp, uniqId } from './_helpers'

// Guided onboarding redirect flow: Workspaces -> (create) -> Providers
// (?onboarding=1) -> Routing (?onboarding=1). Verifies the cross-screen trail
// and that "Next" is gated until each step's prerequisite exists.
test('onboarding: create workspace redirects to Providers with the step trail', async ({ page }) => {
  const wsId = uniqId('e2e-onb')

  await gotoApp(page, 'workspaces')

  // Open the create modal and fill it.
  await page.getByTestId('ws-new').click()
  await expect(page.getByTestId('ws-create-modal')).toBeVisible()
  await page.getByTestId('ws-create-id').fill(wsId)
  await page.getByTestId('ws-create-name').fill('E2E Onboarding')
  // Pick the first real client option (wait for the async clients fetch).
  const clientSel = page.getByTestId('ws-create-client')
  await expect.poll(async () =>
    clientSel.locator('option').count()).toBeGreaterThan(1)
  const optionValues = await clientSel.locator('option').evaluateAll(
    (els) => els.map((e) => (e as HTMLOptionElement).value).filter(Boolean))
  expect(optionValues.length, 'need at least one client to onboard').toBeGreaterThan(0)
  await clientSel.selectOption(optionValues[0])

  await page.getByTestId('ws-create-submit').click()

  // Redirected to Providers, in onboarding mode, for the new workspace.
  await expect(page).toHaveURL(new RegExp(`/admin/providers\\?workspace=${wsId}&onboarding=1`), { timeout: 10_000 })
  await expect(page.getByTestId('onboarding-trail')).toBeVisible()
  // No providers yet -> the Next CTA is disabled.
  await expect(page.getByTestId('onboarding-next-disabled')).toBeVisible()

  // The Routing step also shows the trail and gates Finish until an alias exists.
  await gotoApp(page, `admin/routing?workspace=${wsId}&onboarding=1`)
  await expect(page.getByTestId('onboarding-trail')).toBeVisible()
  await expect(page.getByTestId('onboarding-next-disabled')).toBeVisible()

  // Cleanup via API (admin session cookie is already set by gotoApp).
  const del = await page.request.delete(`/admin/workspaces/${wsId}`)
  expect(del.ok()).toBeTruthy()
})

test('onboarding: invalid workspace_id slug blocks Create', async ({ page }) => {
  await gotoApp(page, 'workspaces')
  await page.getByTestId('ws-new').click()
  await expect(page.getByTestId('ws-create-modal')).toBeVisible()
  await page.getByTestId('ws-create-id').fill('Bad ID With Spaces')
  // Submit stays disabled for an invalid slug (and/or no client selected).
  await expect(page.getByTestId('ws-create-submit')).toBeDisabled()
})
