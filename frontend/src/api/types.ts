// Canonical request/response types - hand-mirrored from the backend Pydantic
// models (gateway/routes/admin_crud.py). Keep in sync; every admin call uses these.

export type DetectorType = 'regex' | 'secrets' | 'keyword' | 'presidio' | 'bedrock' | 'azure' | 'model-armor'
export type ApplyTo = 'input' | 'output' | 'both'
export type Action = 'block' | 'redact' | 'audit'
// NOTE: `gemini` IS Google AI Studio (the same API sometimes called `google_genai`)
// - one provider, not two. Vertex AI (service-account) is the separate one.
export type ProviderType = 'bedrock' | 'anthropic' | 'gemini' | 'azure' | 'openai'
  | 'vertex_ai' | 'litellm_proxy' | 'ollama' | 'hosted_vllm'

export interface WorkspaceIn {
  workspace_id: string
  client_id?: string | null    // WAVE 19: parent Client (required server-side on create)
  name?: string
  chat_models?: Record<string, any>
  embedding_models?: Record<string, any>
  default_chat_alias?: string | null
  guardrails?: Record<string, any>
  quotas?: Record<string, any>
  budgets?: Record<string, any>
  rate_limits?: Record<string, any>
}

export interface ProviderIn {
  provider: ProviderType
  credentials: Record<string, string>
  config?: Record<string, any>
}

export interface ProviderTestIn {
  provider: ProviderType
  credentials: Record<string, string>
  config?: Record<string, any>
  model_id?: string | null
}

export interface ProviderTestResult {
  ok: boolean
  provider: string
  latency_ms?: number
  detail?: { status?: number; error?: string; request_id?: string; model?: string }
  error?: string
}

export interface ProfileIn {
  name: string
  detector_type: DetectorType
  policy_name?: string
  enabled?: boolean
  config?: Record<string, any>
  scope?: 'global' | 'workspace' | 'component'
  workspace_id?: string | null
  component?: string | null
}

export interface RuleIn {
  name: string
  description?: string
  enabled?: boolean
  cel_expression?: string
  builder_spec?: any | null
  apply_to?: ApplyTo
  action?: Action
  sampling_rate?: number
  timeout_ms?: number
  profile_ids?: number[]
  scope?: 'global' | 'workspace' | 'component'
  workspace_id?: string | null
  component?: string | null
}

export interface GuardrailTestIn {
  content: string
  cel_expression?: string
  action?: Action
  profiles?: { detector_type: DetectorType; config?: Record<string, any> }[]
  profile_ids?: number[]
  headers?: Record<string, string>
  model?: string
}

export interface TestFinding {
  detector_type: string
  detector: string
  category: string
  excerpt: string
  action: string
  processing_ms: number
}

export interface GuardrailTestResult {
  cel_matched: boolean
  matched_condition: string | null
  violation: boolean
  action: string
  findings: TestFinding[]
  errors: string[]
  processing_ms: number
  cel_processing_ms: number
}

export interface ValidateCelResult {
  ok: boolean
  error?: string
  warning?: string
  line?: number | null
  column?: number | null
}

export interface PricingIn {
  model_substr: string
  input_per_1k: number
  output_per_1k: number
  note?: string
}

// ComponentIn was removed in WAVE 20 TRACK 1. Components are a runtime
// attribution dimension (auto-registered by X-Gateway-Component header),
// not an admin-created entity. No create/edit/delete surface exists.
