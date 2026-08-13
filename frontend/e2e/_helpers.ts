import { test, expect, Locator, Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SCREENS = resolve(HERE, '__screens__')
mkdirSync(SCREENS, { recursive: true })

/** Attach a global "no 4xx/5xx during the flow" guard. */
export function guardNo4xx(page: Page, allow: RegExp[] = []) {
  const violations: string[] = []
  page.on('response', (resp) => {
    const url = resp.url()
    const code = resp.status()
    if (code < 400) return
    if (allow.some((re) => re.test(url))) return
    violations.push(`${code} ${resp.request().method()} ${url}`)
  })
  return {
    assertClean() {
      expect(violations, `unexpected 4xx/5xx during flow:\n  ${violations.join('\n  ')}`).toEqual([])
    },
    list: () => violations,
  }
}

export async function gotoApp(page: Page, path = '/') {
  // WAVE 19 TRACK F \u2014 admin pages are gated behind /auth/login. We programmatically
  // sign in once per page (idempotent: the cookie persists across goto calls in the
  // same context) so existing specs keep working without manual ceremony.
  await ensureSignedIn(page)
  // SSE keeps connections open, so 'networkidle' never fires; use DOMContentLoaded instead.
  await page.goto('/app/' + path.replace(/^\//, ''), { waitUntil: 'domcontentloaded' })
}

const _signedInContexts = new WeakSet<object>()
async function ensureSignedIn(page: Page) {
  const ctx = page.context() as unknown as object
  if (_signedInContexts.has(ctx)) return
  // Hit the API directly so we don't rely on the dashboard route for the login.
  const r = await page.request.post('/auth/login', {
    data: { username: 'admin', password: 'agnos' },
    headers: { 'Content-Type': 'application/json' },
  })
  if (!r.ok()) throw new Error(`/auth/login failed: ${r.status()} ${await r.text()}`)
  _signedInContexts.add(ctx)
}

/** Issue a unique workspace id per test run so reruns don't collide. */
export const uniqId = (prefix: string) => `${prefix}-${Date.now().toString(36)}`

/**
 * Type into a locator like a real user - character by character - and assert
 * the value accumulated AND focus stayed on the field. Catches the React
 * "component defined inside parent's render" remount bug that loses focus
 * after every keystroke (fill() would mask this entirely).
 *
 * Clears any default value first via select-all+delete (single keyboard shot,
 * doesn't trigger per-char remounts), then types the new value char-by-char.
 */
export async function typeRealUser(loc: Locator, text: string, delay = 20) {
  await loc.click()
  // clear in one keyboard shot (so the per-char path is exclusively pressSequentially)
  await loc.press('ControlOrMeta+a')
  await loc.press('Delete')
  await expect(loc).toHaveValue('')
  await loc.pressSequentially(text, { delay })
  await expect(loc).toBeFocused()
  await expect(loc).toHaveValue(text)
}

/** Capture a full-page screenshot for visual review. Lets animations settle. */
export async function snap(page: Page, name: string) {
  await page.waitForLoadState('domcontentloaded')
  await page.waitForTimeout(900)              // count-ups (600ms) + chart enter (300ms)
  const path = `${SCREENS}/${name}.png`
  await page.screenshot({ path, fullPage: true })
  return path
}

/**
 * Open a RowMenu (portal-rendered popover) by clicking the trigger and
 * asserting the popover lands fully in the viewport (proves the WAVE 16-UX-2
 * fix for the hidden ⋯ menu bug). Idempotent - if already open, returns the
 * existing popover. Returns the popover locator.
 */
export async function openRowMenu(page: Page, triggerTestId: string) {
  const trigger = page.getByTestId(triggerTestId)
  await expect(trigger).toBeVisible()
  const popover = page.getByTestId(`${triggerTestId}-popover`)
  if ((await popover.count()) === 0) {
    await trigger.click()
  }
  await expect(popover).toBeVisible()
  await expect(popover).toBeInViewport()
  return popover
}

/** Click a row-menu item by its testId after opening the menu. */
export async function clickRowMenuItem(page: Page, triggerTestId: string, itemTestId: string) {
  await openRowMenu(page, triggerTestId)
  const item = page.getByTestId(itemTestId)
  await expect(item).toBeVisible()
  await expect(item).toBeInViewport()
  await item.click()
}
