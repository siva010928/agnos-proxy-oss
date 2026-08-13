import { useQuery } from '@tanstack/react-query'

/**
 * Extract a human-readable error message out of any FastAPI / OpenAI-shaped
 * error body. Handles four shapes seen in this codebase:
 *
 *   1. FastAPI 422 validation error - ``{detail: [{loc, msg, type}, ...]}``
 *   2. FastAPI HTTPException with a string detail - ``{detail: "..."}``
 *   3. FastAPI HTTPException with an OpenAI-shaped detail -
 *      ``{detail: {error: {message, type, code}}}``
 *      (this is what `/auth/login` returns on bad creds, what was rendering
 *      as ``"[object Object]"`` before WAVE 19's fix)
 *   4. Top-level OpenAI error body - ``{error: {message, ...}}``
 *
 * Returns a plain string suitable for a toast / inline error.
 */
function extractErrorMessage(body: any, status: number): string {
  if (!body) return `HTTP ${status}`
  // (4) Top-level OpenAI error
  if (body?.error?.message) return String(body.error.message)
  const detail = body?.detail
  if (detail == null) return `HTTP ${status}`
  // (1) FastAPI 422
  if (Array.isArray(detail)) {
    return detail.map((d: any) => {
      const loc = Array.isArray(d?.loc) ? d.loc.filter((x: any) => x !== 'body').join('.') : ''
      const msg = d?.msg || JSON.stringify(d)
      return loc ? `${loc}: ${msg}` : msg
    }).join('; ')
  }
  // (2) string detail
  if (typeof detail === 'string') return detail
  // (3) nested OpenAI-shaped detail (e.g. /auth/login 401)
  if (typeof detail === 'object') {
    if (detail?.error?.message) return String(detail.error.message)
    if (detail?.message) return String(detail.message)
    // last resort: stringify the inner object so the user sees *something*
    // useful instead of the literal "[object Object]"
    try { return JSON.stringify(detail) } catch { return `HTTP ${status}` }
  }
  return `HTTP ${status}`
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  // Merge headers; always send Content-Type + session cookie (credentials: include).
  // The admin token header is no longer needed - session cookie auth is used instead.
  const headers = { 'Content-Type': 'application/json', ...(opts.headers as Record<string, string> | undefined) }
  const r = await fetch(path, { ...opts, headers, credentials: 'include' })
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try {
      const body = await r.json()
      msg = extractErrorMessage(body, r.status)
    } catch { /* non-JSON body */ }
    const err = new Error(msg) as Error & { status?: number }
    err.status = r.status
    throw err
  }
  return r.json()
}

function poll(ms: number) { return { refetchInterval: ms, staleTime: ms / 2 } }

export interface AnalyticsFilters {
  client?: string         // WAVE 19 \u2014 the new tenancy root
  workspace?: string
  component?: string
  user?: string
  model?: string
  provider?: string
  status?: string
  use_case?: string
  request_id?: string
  event_kind?: string
  granularity?: 'day' | 'hour'
  from?: string
  to?: string
  include_synthetic?: boolean
}

function qs(f: AnalyticsFilters & Record<string, any>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(f)) if (v != null && v !== '') p.set(k, String(v))
  return p.toString()
}

// ── Hierarchical filter facets ──────────────────────────────────────────────
export interface Facets {
  clients: { client_id: string; name: string }[]
  workspaces: { workspace_id: string; client_id: string | null; display_name: string }[]
  components: string[]
  providers: string[]
  statuses: string[]
  event_kinds: string[]
  users: string[]
  models: string[]
  use_cases: string[]
}

// Only the keys the facets endpoint scopes by (so each dropdown cascades off the
// others). Passing the full current selection makes every facet reflect what's
// actually reachable given the other picks.
const FACET_SCOPE_KEYS = ['client', 'workspace', 'component', 'provider', 'model',
                          'user', 'status', 'use_case', 'include_synthetic'] as const

export function useFacets(filters: AnalyticsFilters = {}) {
  const scope: Record<string, any> = {}
  for (const k of FACET_SCOPE_KEYS) {
    const v = (filters as any)[k]
    if (v != null && v !== '' && v !== false) scope[k] = v
  }
  const q = qs(scope)
  return useQuery<Facets>({
    queryKey: ['facets', scope],
    queryFn: () => api(`/admin/request-logs/facets?${q}`),
    ...poll(30000),
  })
}

export function useWorkspaces() {
  return useQuery({ queryKey: ['workspaces'], queryFn: () => api('/admin/workspaces'), ...poll(8000) })
}
export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: () => api('/admin/stats'), ...poll(6000) })
}
export function useCost(groupBy: string, filters: AnalyticsFilters = {}) {
  const q = qs({ group_by: groupBy, ...filters })
  return useQuery({ queryKey: ['cost', groupBy, filters], queryFn: () => api(`/admin/cost?${q}`), ...poll(8000) })
}
export function useTimeseries(filters: AnalyticsFilters = {}) {
  const f = { granularity: 'day', ...filters } as AnalyticsFilters
  const q = qs(f)
  return useQuery({ queryKey: ['ts', f], queryFn: () => api(`/admin/usage/timeseries?${q}`), ...poll(10000) })
}
export function useBreakdown(dim: string, filters: AnalyticsFilters = {}) {
  const f = { dim, granularity: 'day', ...filters } as AnalyticsFilters & { dim: string }
  const q = qs(f)
  return useQuery({ queryKey: ['breakdown', dim, f], queryFn: () => api(`/admin/usage/breakdown?${q}`), ...poll(15000) })
}
export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: () => api('/admin/models'), ...poll(30000) })
}
export function useGuardrails() {
  return useQuery({ queryKey: ['guardrails'], queryFn: () => api('/admin/guardrails'), ...poll(6000) })
}
export function useParity() {
  return useQuery({ queryKey: ['parity'], queryFn: () => api('/admin/parity'), staleTime: 60000 })
}
export function useHealth() {
  return useQuery({ queryKey: ['admin-health'], queryFn: () => api('/admin/health'), ...poll(10000) })
}
export function useEngineCatalog() {
  return useQuery({ queryKey: ['engine-catalog'], queryFn: () => api('/admin/engine/catalog'), ...poll(5000) })
}
export function useProviders() {
  return useQuery({ queryKey: ['providers'], queryFn: () => api('/health/providers'), ...poll(10000) })
}
export function useMetricsText() {
  return useQuery({
    queryKey: ['metrics'],
    queryFn: async () => (await fetch('/metrics')).text(),
    ...poll(5000),
  })
}

// crude Prometheus text parser: returns array of {name, labels, value}
export function parseMetrics(text: string) {
  const out: { name: string; labels: Record<string, string>; value: number }[] = []
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue
    const m = line.match(/^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+([-\d.eE+]+)$/)
    if (!m) continue
    const labels: Record<string, string> = {}
    if (m[2]) for (const kv of m[2].slice(1, -1).split(',')) {
      const i = kv.indexOf('=')
      if (i > 0) labels[kv.slice(0, i)] = kv.slice(i + 1).replace(/^"|"$/g, '')
    }
    out.push({ name: m[1], labels, value: Number(m[3]) })
  }
  return out
}
