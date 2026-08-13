import { expect, test } from '@playwright/test'
import { gotoApp, guardNo4xx, typeRealUser } from './_helpers'

const ALLOW = [/\/events($|\?)/]

/**
 * WAVE 20 E2 - CLS (Cumulative Layout Shift) fix proof.
 *
 * This test verifies that typing into a form field while the live SSE feed
 * is streaming does NOT cause:
 *   (a) focus loss (the user's cursor stays in the field),
 *   (b) value corruption (characters don't get swallowed),
 *   (c) layout shift (a stable reference element's boundingBox stays put).
 *
 * The fix: SSE updates are throttled to 500ms batches AND paused entirely
 * while any input/select/textarea is focused. So the React tree never
 * re-renders mid-keystroke.
 */
test('E2 CLS fix: typing into a form while SSE streams causes no focus loss or layout shift', async ({ page }) => {
  const guard = guardNo4xx(page, ALLOW)

  // Navigate to a page with both a live SSE feed AND a form (Admin → Onboarding
  // wizard has both: the SSE connection is open globally, and the wizard has
  // inputs).
  await gotoApp(page, 'admin')
  await expect(page.getByText('Admin · Onboarding')).toBeVisible()

  // Open the wizard (which lives in a modal with several inputs).
  await page.getByTestId('open-wizard').click()
  await expect(page.getByTestId('wizard')).toBeVisible()

  // Select a client so we can type the workspace ID (this may cause a
  // minor one-time height change as the dropdown value renders; take the
  // bounding box snapshot AFTER this interaction settles).
  await page.getByTestId('wizard-client-id').selectOption('novatech')
  await page.waitForTimeout(200) // let any reflow settle

  // Capture the wizard modal's boundingBox AFTER the form is stable
  const modal = page.getByTestId('wizard')
  const boxBefore = await modal.boundingBox()
  expect(boxBefore).not.toBeNull()

  // Simulate a live SSE burst by generating traffic via the API while the
  // operator is typing. We fire 5 rapid requests that will each emit a
  // governance event on the /events stream (which the page is subscribed to).
  const burst = async () => {
    for (let i = 0; i < 5; i++) {
      await page.request.post('/admin/engine', {
        headers: { 'X-Admin-Token': 'platform-admin-secret', 'Content-Type': 'application/json' },
        data: { engine: 'bifrost' },
      }).catch(() => {})
    }
  }

  // Start the burst in the background while we type
  const burstPromise = burst()

  // Type into the workspace ID field character-by-character (typeRealUser uses
  // pressSequentially + toBeFocused + toHaveValue - catches any focus loss).
  await typeRealUser(page.getByTestId('wizard-ws-id'), 'cls-test-workspace')

  await burstPromise

  // Assert (a) focus: the field still has the value we typed
  await expect(page.getByTestId('wizard-ws-id')).toHaveValue('cls-test-workspace')

  // Assert (b) focus retention: the field is still focused
  await expect(page.getByTestId('wizard-ws-id')).toBeFocused()

  // Assert (c) no layout shift: the modal's position has not moved
  const boxAfter = await modal.boundingBox()
  expect(boxAfter).not.toBeNull()
  // Allow 1px tolerance for sub-pixel rendering
  expect(Math.abs((boxAfter!.x - boxBefore!.x))).toBeLessThanOrEqual(1)
  expect(Math.abs((boxAfter!.y - boxBefore!.y))).toBeLessThanOrEqual(1)
  expect(Math.abs((boxAfter!.width - boxBefore!.width))).toBeLessThanOrEqual(1)
  expect(Math.abs((boxAfter!.height - boxBefore!.height))).toBeLessThanOrEqual(1)

  // Close the wizard
  await page.locator('button:has-text("Cancel")').first().click()

  guard.assertClean()
})
