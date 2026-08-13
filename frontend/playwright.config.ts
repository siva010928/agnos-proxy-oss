import { defineConfig, devices } from '@playwright/test'

// E2E against the LIVE gateway-served dashboard (http://localhost:8090/app).
// Start the gateway first:  cd .. && source .venv/bin/activate && PYTHONPATH=. python gateway_server.py
// Then:  npm run test:e2e         (browser: `npx playwright install chromium` once)
//
// Auth: global-setup logs in via POST /auth/login and saves the session cookie to
// e2e/.auth/state.json, which every test reuses (no per-test login).
const BASE = process.env.E2E_BASE_URL || 'http://localhost:8090'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,           // admin mutations touch shared workspaces - run serial
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e/report' }]],
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: BASE,
    storageState: 'e2e/.auth/state.json',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Prefer the downloaded chromium; fall back to system Chrome if present.
    channel: process.env.E2E_CHANNEL || undefined,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
