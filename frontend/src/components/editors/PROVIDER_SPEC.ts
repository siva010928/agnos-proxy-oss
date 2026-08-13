// Single source of truth for provider config: the field schema, what's
// required, defaults, and the list of supported regions. Both ProviderEditor
// and the Onboarding wizard's provider step import this - no two divergent
// REQUIRED_CREDS / DEFAULT_MODEL / region lists anymore.
import type { ProviderType } from '../../api/types'

export interface FieldSpec {
  key: string
  label: string
  hint?: string
  type?: 'text' | 'password' | 'select' | 'url' | 'number'
  placeholder?: string
  required?: boolean
  options?: { value: string; label: string }[]
  // where this lands in the API body. Default = credentials. Some go on `config`.
  destination?: 'credentials' | 'config'
  // Conditional visibility: only show this field when another field's value is in
  // `in` (e.g. show `profile_name` only when `auth_type` = 'sso').
  showIf?: { field: string; in: string[] }
}

export interface ProviderSpec {
  id: ProviderType
  label: string
  blurb: string
  // Default model id used when the user hasn't picked one yet (e.g. for the
  // Test Connection probe). Tests can also pre-populate the form with this.
  defaultModelId: string
  fields: FieldSpec[]
}

const AWS_REGIONS = [
  'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
  'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1', 'eu-north-1',
  'ap-south-1', 'ap-northeast-1', 'ap-northeast-2', 'ap-southeast-1', 'ap-southeast-2',
  'ca-central-1', 'sa-east-1',
]

export const PROVIDER_SPECS: Record<ProviderType, ProviderSpec> = {
  bedrock: {
    id: 'bedrock',
    label: 'AWS Bedrock',
    blurb: 'Foundation models on AWS (Claude, Titan, Llama). Three auth modes: static IAM keys, a Bedrock API key (bearer), or a local AWS SSO profile - pick one below.',
    defaultModelId: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
    fields: [
      { key: 'auth_type', label: 'Authentication', type: 'select', required: true,
        options: [
          { value: 'static', label: 'Static IAM keys' },
          { value: 'api-key', label: 'Bedrock API key (bearer)' },
          { value: 'sso', label: 'AWS SSO profile' },
        ],
        hint: 'How the gateway authenticates to Bedrock (matches the DirectEngine session logic).' },
      { key: 'access_key', label: 'Access key ID', type: 'password', required: true, placeholder: 'AKIA...',
        showIf: { field: 'auth_type', in: ['static'] }, hint: 'IAM access key with bedrock:InvokeModel permission.' },
      { key: 'secret_key', label: 'Secret access key', type: 'password', required: true,
        showIf: { field: 'auth_type', in: ['static'] }, hint: 'Pairs with the access key. Stored encrypted at rest.' },
      { key: 'session_token', label: 'Session token (optional)', type: 'password',
        showIf: { field: 'auth_type', in: ['static'] }, hint: 'Only required for STS/temporary (ASIA…) credentials.' },
      { key: 'bedrock_api_key', label: 'Bedrock API key', type: 'password', required: true,
        showIf: { field: 'auth_type', in: ['api-key'] }, hint: 'Amazon Bedrock API key - exported to AWS_BEARER_TOKEN_BEDROCK at request time.' },
      { key: 'profile_name', label: 'AWS SSO profile name', type: 'text', required: true, placeholder: 'my-sso-profile',
        showIf: { field: 'auth_type', in: ['sso'] }, hint: 'A local profile from `aws configure sso`. Run `aws sso login --profile <name>` first.' },
      { key: 'region', label: 'AWS region', type: 'select', required: true, destination: 'config',
        options: AWS_REGIONS.map((r) => ({ value: r, label: r })),
        hint: 'Bedrock model availability varies by region (e.g. claude-sonnet-4-5 is in us-east-1, us-east-2, us-west-2).' },
    ],
  },
  anthropic: {
    id: 'anthropic',
    label: 'Anthropic',
    blurb: 'Direct Anthropic API for Claude models. Auth via API key (sk-ant-…).',
    defaultModelId: 'claude-sonnet-4-5-20250929',
    fields: [
      { key: 'api_key', label: 'API key', type: 'password', required: true, placeholder: 'sk-ant-...', hint: 'From console.anthropic.com → Settings → API Keys.' },
    ],
  },
  gemini: {
    id: 'gemini',
    label: 'Google AI Studio (Gemini)',
    blurb: 'Google AI Studio API for Gemini models (a.k.a. google_genai). Auth via an AI Studio API key (AIza…). For service-account / Vertex, use the Vertex AI provider instead.',
    defaultModelId: 'gemini-2.5-flash',
    fields: [
      { key: 'api_key', label: 'API key', type: 'password', required: true, placeholder: 'AIza...', hint: 'From aistudio.google.com → Get API key.' },
    ],
  },
  openai: {
    id: 'openai',
    label: 'OpenAI',
    blurb: 'OpenAI API for GPT/o-series models. Auth via API key (sk-…). Optional base_url for OpenAI-compatible endpoints (e.g. self-hosted).',
    defaultModelId: 'gpt-4o',
    fields: [
      { key: 'api_key', label: 'API key', type: 'password', required: true, placeholder: 'sk-...', hint: 'From platform.openai.com → API keys.' },
      { key: 'base_url', label: 'Base URL (optional)', type: 'url', destination: 'config', placeholder: 'https://api.openai.com/v1', hint: 'Override only for OpenAI-compatible endpoints.' },
    ],
  },
  azure: {
    id: 'azure',
    label: 'Azure OpenAI',
    blurb: 'Azure-hosted OpenAI deployment. Auth via api_key + endpoint + api_version.',
    defaultModelId: 'gpt-4o',
    fields: [
      { key: 'api_key', label: 'API key', type: 'password', required: true, hint: 'From the Azure portal → your OpenAI resource → Keys and Endpoint.' },
      { key: 'endpoint', label: 'Endpoint', type: 'url', required: true, destination: 'config',
        placeholder: 'https://my-resource.openai.azure.com', hint: 'The "Endpoint" value next to your keys in Azure portal.' },
      { key: 'api_version', label: 'API version', type: 'text', destination: 'config',
        placeholder: '2024-10-21', hint: 'Format YYYY-MM-DD (e.g. 2024-10-21) or YYYY-MM-DD-preview.' },
    ],
  },
  vertex_ai: {
    id: 'vertex_ai',
    label: 'Google Vertex AI',
    blurb: 'Google Vertex AI for Gemini + more (service-account auth). Paste the service-account JSON and set the GCP project. Served by our DirectEngine.',
    defaultModelId: 'gemini-2.5-flash',
    fields: [
      { key: 'api_key', label: 'Service-account JSON', type: 'password', required: true,
        placeholder: '{ "type": "service_account", ... }', hint: 'The full service-account JSON (or a file path to it). Stored encrypted at rest.' },
      { key: 'vertex_project', label: 'GCP project id', type: 'text', required: true, destination: 'config',
        placeholder: 'my-gcp-project', hint: 'The Google Cloud project that hosts your Vertex AI.' },
      { key: 'vertex_location', label: 'Location', type: 'text', destination: 'config',
        placeholder: 'us-central1', hint: 'Vertex region. Defaults to us-central1.' },
    ],
  },
  litellm_proxy: {
    id: 'litellm_proxy',
    label: 'LiteLLM Proxy',
    blurb: 'An OpenAI-compatible LiteLLM proxy gateway to many providers. Auth via base_url + key. Served by our DirectEngine (forwarded verbatim on the OpenAI wire).',
    defaultModelId: 'claude-sonnet-4-5',
    fields: [
      { key: 'base_url', label: 'Proxy base URL', type: 'url', required: true, destination: 'config',
        placeholder: 'https://your-litellm-proxy.example.com', hint: 'The OpenAI-compatible /v1 base URL of your LiteLLM proxy.' },
      { key: 'api_key', label: 'Proxy API key', type: 'password', required: true, hint: 'The key your LiteLLM proxy expects.' },
    ],
  },
  ollama: {
    id: 'ollama',
    label: 'Ollama (local)',
    blurb: 'A local Ollama server via its OpenAI-compatible /v1 API. No API key. Served by our DirectEngine.',
    defaultModelId: 'llama3.1',
    fields: [
      { key: 'base_url', label: 'Base URL', type: 'url', destination: 'config',
        placeholder: 'http://localhost:11434/v1', hint: 'Ollama OpenAI-compatible endpoint. Defaults to http://localhost:11434/v1.' },
    ],
  },
  hosted_vllm: {
    id: 'hosted_vllm',
    label: 'vLLM / LM Studio (local)',
    blurb: 'Any OpenAI-compatible server exposing /v1 (vLLM, LM Studio, etc.). No API key required. Served by our DirectEngine.',
    defaultModelId: '',
    fields: [
      { key: 'base_url', label: 'Base URL', type: 'url', destination: 'config',
        placeholder: 'http://127.0.0.1:1234/v1', hint: 'The OpenAI-compatible /v1 base URL. Defaults to http://127.0.0.1:1234/v1.' },
      { key: 'api_key', label: 'API key (optional)', type: 'password', hint: 'Only if your server requires one.' },
    ],
  },
}

// Common optional field on EVERY provider: per-request timeout pushed to the
// gateway → Bifrost (network_config.default_request_timeout_in_seconds). Bifrost
// defaults to 30s which 504s on long completions; raise it here per provider.
const TIMEOUT_FIELD: FieldSpec = {
  key: 'request_timeout_seconds', label: 'Request timeout (seconds)', type: 'number',
  destination: 'config', placeholder: '120',
  hint: 'Max time the gateway waits for this provider before failing over. Leave blank for the default (120s). Raise for long completions; lower to fail fast.',
}
for (const spec of Object.values(PROVIDER_SPECS)) {
  spec.fields.push({ ...TIMEOUT_FIELD })
}

// A field is shown only when its showIf condition is satisfied (the controlling
// field's value - looked up in creds then config - is in the allowed list).
export function fieldVisible(f: FieldSpec, creds: Record<string, string>, config: Record<string, string>): boolean {
  if (!f.showIf) return true
  const v = (creds?.[f.showIf.field] || config?.[f.showIf.field] || '')
  return f.showIf.in.includes(v)
}

export function isCredsValid(provider: ProviderType, creds: Record<string, string>, config: Record<string, string>): boolean {
  const spec = PROVIDER_SPECS[provider]
  if (!spec) return false
  for (const f of spec.fields) {
    if (!f.required) continue
    if (!fieldVisible(f, creds, config)) continue   // hidden fields aren't required
    const bag = f.destination === 'config' ? config : creds
    const v = (bag?.[f.key] || '').trim()
    if (!v) return false
  }
  return true
}
