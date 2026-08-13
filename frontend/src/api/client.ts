// Typed admin API client - the single path for every admin mutation. Uses
// session-cookie auth (no explicit token header). The api() function sends
// credentials: 'include' so the cookie goes automatically.
import { api } from '../lib/api'
import type {
  GuardrailTestIn, GuardrailTestResult, PricingIn, ProfileIn,
  ProviderIn, ProviderTestIn, ProviderTestResult, RuleIn, ValidateCelResult, WorkspaceIn,
} from './types'


function post<T = any>(path: string, body: unknown) {
  return api<T>(path, { method: 'POST', body: JSON.stringify(body) })
}
function patch<T = any>(path: string, body: unknown) {
  return api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}
function del<T = any>(path: string) {
  return api<T>(path, { method: 'DELETE' })
}
function get<T = any>(path: string) {
  return api<T>(path)
}

export const admin = {
  // workspaces
  createWorkspace: (b: WorkspaceIn) => post('/admin/workspaces', b),
  updateWorkspace: (id: string, b: Partial<WorkspaceIn>) => patch(`/admin/workspaces/${id}`, b),
  // Gateway-WIDE engine routing (rented↔owned per provider) - not per workspace.
  getEngineRouting: () => get<{ overrides: Record<string, string | number> }>('/admin/engine-routing'),
  setEngineRouting: (overrides: Record<string, string | number>) => patch('/admin/engine-routing', { overrides }),
  deleteWorkspace: (id: string) => del(`/admin/workspaces/${id}`),
  // providers
  listProviders: (id: string) => get(`/admin/workspaces/${id}/providers`),
  addProvider: (id: string, b: ProviderIn) => post(`/admin/workspaces/${id}/providers`, b),
  // Config-only edit (region/endpoint/api_version/request_timeout_seconds) - no creds re-entry.
  updateProviderConfig: (id: string, provider: string, config: Record<string, any>) =>
    patch(`/admin/workspaces/${id}/providers/${provider}`, { config }),
  deleteProvider: (id: string, provider: string) => del(`/admin/workspaces/${id}/providers/${provider}`),
  testProvider: (b: ProviderTestIn) => post<ProviderTestResult>('/admin/providers/test', b),
  // keys
  listKeys: (id: string) => get(`/admin/workspaces/${id}/keys`),
  issueKey: (id: string, b?: { expires_at?: string | null; roles?: string[] }) =>
    post<{ api_key: string }>(`/admin/workspaces/${id}/keys`, b ?? {}),
  rotateKey: (id: string, keyId: number) => post<{ api_key: string }>(`/admin/workspaces/${id}/keys/${keyId}/rotate`, {}),
  disableKey: (id: string, keyId: number) => del(`/admin/workspaces/${id}/keys/${keyId}`),
  // routing
  routingPreview: (workspace: string, alias?: string, component?: string) => {
    const q = new URLSearchParams({ workspace })
    if (alias) q.set('alias', alias)
    if (component) q.set('component', component)
    return get(`/admin/routing/preview?${q}`)
  },
  // guardrails
  listProfiles: () => get('/admin/guardrails/profiles'),
  createProfile: (b: ProfileIn) => post<{ id: number }>('/admin/guardrails/profiles', b),
  updateProfile: (id: number, b: Partial<ProfileIn>) => patch(`/admin/guardrails/profiles/${id}`, b),
  deleteProfile: (id: number) => del(`/admin/guardrails/profiles/${id}`),
  // listRules(): no arg → ALL rules (admin Rule Builder). With workspaceId →
  // only rules in scope for that workspace (global + its own), so per-workspace
  // editors never offer another workspace's scoped rules.
  listRules: (workspaceId?: string) =>
    get(`/admin/guardrails/rules${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`),
  createRule: (b: RuleIn) => post<{ id: number }>('/admin/guardrails/rules', b),
  updateRule: (id: number, b: Partial<RuleIn>) => patch(`/admin/guardrails/rules/${id}`, b),
  deleteRule: (id: number) => del(`/admin/guardrails/rules/${id}`),
  testGuardrail: (b: GuardrailTestIn) => post<GuardrailTestResult>('/admin/guardrails/test', b),
  testGuardrailProfile: (b: { detector_type: string; config: any }) => post<any>('/admin/guardrails/test-profile', b),
  validateCel: (cel: string) => post<ValidateCelResult>('/admin/guardrails/validate-cel', { cel_expression: cel }),
  // pricing
  listPricing: () => get('/admin/pricing'),
  upsertPricing: (b: PricingIn) => post('/admin/pricing', b),
  deletePricing: (substr: string) => del(`/admin/pricing/${encodeURIComponent(substr)}`),
  // engine
  setEngine: (engine: EngineName) => post<EngineSwap>('/admin/engine', { engine }),
  // engine slot (swappable translator): catalog + Quarantine & Evacuate
  engineCatalog: () => get<EngineCatalog>('/admin/engine/catalog'),
  quarantineEngine: (to: EngineName, incident: string) =>
    post<QuarantineResult>('/admin/engine/quarantine', { to, incident }),
  restoreEngine: (to?: EngineName) => post('/admin/engine/restore', to ? { to } : {}),
  // shadow parity: run one prompt through BOTH engines and score the agreement
  parityRun: (b: { workspace_id: string; provider: string; model_id: string; prompt: string; max_tokens?: number }) =>
    post<ParityResult>('/admin/parity/run', b),
}

export type EngineName = 'bifrost' | 'litellm' | 'portkey' | 'direct' | 'echo'

export interface EngineSwapEvidence {
  store: string; table: string; column: string; key: string
  previous: string | null; new: string
  runtime_prev: string; runtime_now: string
  stateful?: boolean; reconcile: 'none' | 'background'
}
export interface EngineSwap {
  engine: EngineName; governance: string; persisted: boolean
  unchanged?: boolean; evidence: EngineSwapEvidence
}

export interface EngineMeta {
  label: string; vendor: string; runtime: string; license: string
  stateful: boolean | null; holds_provider_keys: boolean | null; owned: boolean
  blast_radius: string; tagline: string
}
export interface EngineCatalog {
  current_engine: EngineName
  overrides: Record<string, string | number>
  providers: string[]
  safe_engines: EngineName[]
  engines: Record<string, EngineMeta>
  quarantined: boolean
}
export interface QuarantineResult {
  ok: boolean; action: string; incident: string
  from: { engine: string; meta: EngineMeta }
  to: { engine: string; meta: EngineMeta }
  providers_evacuated: string[]
  overrides: Record<string, string | number>
  message: string
}

export interface ParityLeg {
  engine: string; ok: boolean; status: number; latency_ms: number
  hops: string[]
  latency_samples?: number[]; latency_min?: number; latency_max?: number; samples?: number
  text: string; finish_reason: string; tool_calls: string[]
  input_tokens: number; output_tokens: number; cached_tokens?: number
  raw?: string; error: string | null
}
export interface ParityResult {
  provider: string; model_id: string; samples?: number
  bifrost: ParityLeg; direct: ParityLeg
  verdict: 'identical' | 'high' | 'moderate' | 'divergent' | 'error'
  text_similarity: number; exact: boolean; structural_parity: boolean
  same_tool_calls: boolean; same_finish_reason: boolean; latency_delta_ms: number
}
