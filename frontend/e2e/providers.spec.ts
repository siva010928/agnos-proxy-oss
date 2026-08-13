import { test, expect, type Page } from '@playwright/test'

// Admin · Providers - verifies the provider editor across ALL providers + all
// bedrock auth modes (conditional-field UI) + Test-Connection gating.
// Uses a workspace deep-link (?workspace=) so "Add provider" is enabled without
// depending on picker timing.

const WS = process.env.E2E_WORKSPACE || 'san-bedrock-static-direct'

async function openEditor(page: Page) {
  await page.goto(`/app/admin/providers?workspace=${WS}`)
  await expect(page.getByRole('heading', { name: /Providers/i })).toBeVisible()
  const add = page.getByTestId('provider-new')
  await expect(add).toBeEnabled({ timeout: 20_000 })
  await add.click()
  await expect(page.getByTestId('provider-option-bedrock')).toBeVisible()
}

test.describe('Admin · Providers', () => {
  test('all providers are selectable in the editor', async ({ page }) => {
    await openEditor(page)
    // gemini IS Google AI Studio (google_genai) - one provider, not two.
    for (const id of ['bedrock', 'anthropic', 'gemini', 'openai', 'azure',
                       'vertex_ai', 'litellm_proxy', 'ollama', 'hosted_vllm']) {
      await expect(page.getByTestId(`provider-option-${id}`)).toBeVisible()
    }
  })

  test('bedrock auth modes show the right conditional fields', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('provider-option-bedrock').click()
    const authType = page.getByTestId('provider-field-auth_type')
    await expect(authType).toBeVisible()

    await authType.selectOption('static')
    await expect(page.getByTestId('provider-field-access_key')).toBeVisible()
    await expect(page.getByTestId('provider-field-secret_key')).toBeVisible()
    await expect(page.getByTestId('provider-field-bedrock_api_key')).toHaveCount(0)
    await expect(page.getByTestId('provider-field-profile_name')).toHaveCount(0)

    await authType.selectOption('api-key')
    await expect(page.getByTestId('provider-field-bedrock_api_key')).toBeVisible()
    await expect(page.getByTestId('provider-field-access_key')).toHaveCount(0)

    await authType.selectOption('sso')
    await expect(page.getByTestId('provider-field-profile_name')).toBeVisible()
    await expect(page.getByTestId('provider-field-access_key')).toHaveCount(0)

    await expect(page.getByTestId('provider-field-region')).toBeVisible()
  })

  test('litellm_proxy requires a base_url field', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('provider-option-litellm_proxy').click()
    await expect(page.getByTestId('provider-field-base_url')).toBeVisible()
    await expect(page.getByTestId('provider-field-api_key')).toBeVisible()
  })

  test('vertex_ai requires SA JSON + project', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('provider-option-vertex_ai').click()
    await expect(page.getByTestId('provider-field-api_key')).toBeVisible()
    await expect(page.getByTestId('provider-field-vertex_project')).toBeVisible()
  })

  test('Test Connection is gated until required creds are filled', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('provider-option-anthropic').click()
    await expect(page.getByTestId('provider-test')).toBeDisabled()
    await page.getByTestId('provider-field-api_key').fill('sk-ant-test-placeholder')
    await expect(page.getByTestId('provider-test')).toBeEnabled()
  })
})
