import { defineConfig, devices } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// E2E against the LIVE gateway-served dashboard (http://localhost:8090/app).
// Start the gateway first:  cd .. && source .venv/bin/activate && PYTHONPATH=. python gateway_server.py
// Then:  npm run test:e2e         (browser: `npx playwright install chromium` once)
//
// Auth: global-setup logs in via POST /auth/login and saves the session cookie to
// e2e/.auth/state.json, which every test reuses (no per-test login).
const BASE = process.env.E2E_BASE_URL || 'http://localhost:8090'

// Best-effort: hydrate process.env from the gitignored root .env so LOCAL runs pick
// up real provider keys (ANTHROPIC_API_KEY, AWS_*) that power the live "Test
// Connection" specs. Absent in CI - that is fine: those key-dependent tests self-skip
// (see admin-providers/guardrails/wizard). Never throws.
try {
  const raw = readFileSync(fileURLToPath(new URL('../.env', import.meta.url)), 'utf8')
  for (const line of raw.split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*"?([^"\n]*?)"?\s*$/)
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2]
  }
} catch { /* no .env (e.g. CI) - rely on the real environment */ }

// Screenshot/capture galleries and the deployed-screens capture are release tooling,
// not behavioral validation (they assert seeded analytics data / need a live deployed
// target + DEPLOY_PASS). Skip them on CI so the nightly e2e is deterministic; local
// runs (CI unset) still include them for refreshing the gallery.
const CAPTURE_SPECS = [
  '**/readme-deployed-screens.spec.ts',
  '**/readme-screens.spec.ts',
  '**/wave*-screenshots.spec.ts',
]

export default defineConfig({
  testDir: './e2e',
  testIgnore: process.env.CI ? CAPTURE_SPECS : [],
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
