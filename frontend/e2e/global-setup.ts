import { request, type FullConfig } from '@playwright/test'
import fs from 'node:fs'

// Log in once via the dashboard auth API and persist the session cookie so every
// spec starts authenticated (the login form lives behind the 3D story, so an API
// login is faster + more robust than driving the story each time).
export default async function globalSetup(_config: FullConfig) {
  const base = process.env.E2E_BASE_URL || 'http://localhost:8090'
  const user = process.env.DASHBOARD_ADMIN_USER || 'admin'
  const password = process.env.DASHBOARD_ADMIN_PASSWORD || 'agnos'
  const ctx = await request.newContext({ baseURL: base })
  const res = await ctx.post('/auth/login', { data: { username: user, password, preview_name: 'e2e' } })
  if (!res.ok()) {
    throw new Error(`dashboard login failed: ${res.status()} ${await res.text()}`)
  }
  // cwd is the frontend/ dir (where playwright.config.ts lives); write to the same
  // relative path the config's storageState reads from.
  fs.mkdirSync('e2e/.auth', { recursive: true })
  await ctx.storageState({ path: 'e2e/.auth/state.json' })
  await ctx.dispose()
}
