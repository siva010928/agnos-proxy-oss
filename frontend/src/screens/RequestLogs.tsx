// Request Logs - the searchable per-request log (WAVE 19 TRACK D3 v2).
//
// UX rules (per the operator feedback):
//   * Enum-shaped fields are DROPDOWNS sourced from /admin/request-logs/facets,
//     never free-text. The operator should not have to remember `novatech`
//     or `bedrock|anthropic|gemini` letter-perfect.
//   * Free-text fields (user, request_id, model) accept a substring - the
//     server uses ILIKE so 'claude' matches every claude-* row.
//   * Date range uses real <input type="datetime-local"> + four quick presets
//     (Last 1h / 24h / 7d / 30d) that the operator can tap.
//   * Real pagination: page size selector (50/100/200/500), Prev/Next, page N
//     of M, jump-to-first/last.
//   * Filters cascade: picking a Client narrows the workspace dropdown; picking
//     a Workspace narrows component/user/model facets to that workspace's rows.
//   * Each row expands inline; "Open trace in Jaeger" is a per-row deep link.

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ChevronDown, ChevronRight, ChevronsLeft, ChevronsRight,
  ExternalLink, RefreshCw, X, ArrowUp, ArrowDown,
} from 'lucide-react'
import { api, AnalyticsFilters, useFacets } from '../lib/api'
import { HierarchicalFilters } from '../components/Filters'
import { Card, SectionTitle, Skeleton } from '../components/ui'
import { fmtMs, fmtDateTime } from '../lib/format'
import { useCurrency } from '../lib/currency'

interface LogRow {
  id: number
  timestamp: string
  request_id: string
  client_id: string | null
  workspace_id: string
  user_id: string | null
  component: string | null
  provider: string
  model_alias: string
  provider_model_id: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  latency_ms: number
  stream: boolean
  status: string
  error_type: string | null
  error_detail: ErrorDetail | null            // WAVE 26
  guardrail_violations: GuardrailViolationRow[]
  call_kind: string
  event_kind: string
  engine: string
  use_case: string | null
}

// WAVE 26 - structured failure context. Shape varies by category.
interface ErrorDetail {
  category?: 'guardrail' | 'rate_limit' | 'budget' | 'routing' | 'provider_error' | string
  // guardrail
  rule?: string
  action?: string
  stage?: string
  matches?: { detector: string; sub_category: string; excerpt: string; confidence: number }[]
  // rate_limit
  scope?: string
  limit_type?: string
  limit?: number
  current?: number
  exceeded_by?: number
  retry_after_seconds?: number
  // budget
  budget_usd?: number
  spent_usd?: number
  exceeded_by_usd?: number
  // routing
  alias_requested?: string
  available_aliases?: string[]
  reason?: string
  result?: string
  // provider_error
  provider?: string
  model_id?: string
  http_status?: number
  raw_response?: any
  raw_message?: string
  mapped_message?: string
  exception_type?: string
  retries?: number
  fallback_attempted?: boolean
  fallback_chain?: { from: string; to: string; reason: string }[]
  attempted_targets?: { provider: string; model_id: string; weight: number }[]
  // per-attempt outcomes in actual order, each with WHY it failed
  attempts?: { provider: string; model_id: string; attempt?: number; http_status?: number | null;
               error_type?: string; message?: string; ms?: number; skipped?: boolean }[]
  // timeout context (when error_type == "timeout") - the ACTUAL applied value
  timeout?: { effective_s?: number; source?: string; max_s?: number }
}

interface GuardrailViolationRow {
  rule: string
  detector: string
  action: string
  stage: string
  excerpt: string
  severity: string
  timestamp: string | null
}

function _obsUrl(localPort: string, path: string): string {
  if (typeof window === 'undefined') return path
  const host = window.location.hostname
  return (host === 'localhost' || host === '127.0.0.1') ? `http://localhost:${localPort}` : path
}
const JAEGER_URL = _obsUrl('16686', '/jaeger')
const GRAFANA_URL = _obsUrl('3001', '/grafana')
const PROMETHEUS_URL = _obsUrl('9090', '/prometheus')

const PAGE_SIZES = [50, 100, 200, 500]

// Request Logs uses the shared AnalyticsFilters shape (from/to, request_id,
// include_synthetic) so it speaks the same filter language as Analytics + the
// hierarchical filter bar. `from`/`to` hold the datetime-local wall-clock string.
type Filters = AnalyticsFilters

const QUICK_RANGES: { label: string; hours: number }[] = [
  { label: 'Last 1h',  hours: 1 },
  { label: 'Last 24h', hours: 24 },
  { label: 'Last 7d',  hours: 24 * 7 },
  { label: 'Last 30d', hours: 24 * 30 },
  { label: 'Last 90d', hours: 24 * 90 },
  { label: 'All',      hours: -1 },   // -1 = all-time (from the epoch)
]

function isoLocal(d: Date): string {
  // <input type="datetime-local"> wants 'YYYY-MM-DDTHH:mm' (no seconds, no Z)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// Convert a local-wall-clock datetime-local string (what the user sees/picks, in
// their browser TZ) into a NAIVE-UTC string ('YYYY-MM-DDTHH:MM:SS', no 'Z') that
// matches the gateway's naive-UTC `RequestLog.timestamp` column. Without this the
// from/to window is offset by the viewer's timezone (e.g. IST +5:30).
function localToUtcNaive(localStr: string): string {
  const d = new Date(localStr)          // a 'YYYY-MM-DDTHH:mm' string parses as LOCAL time
  if (isNaN(d.getTime())) return localStr
  return d.toISOString().slice(0, 19)   // drop '.sssZ' → naive UTC wall-clock
}

function statusColor(status: string): string {
  if (status === 'success') return '#34D399'
  if (status === 'error') return '#F87171'
  if (status === 'rate_limited') return '#FBBF24'
  if (status === 'blocked') return '#A78BFA'
  return '#6B7280'
}

export function RequestLogs() {
  // Seed filters from URL query params (e.g. ?workspace=ws-x&client=novatech)
  // so deep-links from Workspaces/Clients land pre-filtered.
  const [searchParams] = useSearchParams()
  const seedFilters = (): Filters => {
    const f: Filters = {}
    const keys: (keyof Filters)[] = ['client', 'workspace', 'user', 'component', 'provider', 'model', 'status', 'use_case', 'request_id', 'from', 'to']
    for (const k of keys) {
      const v = searchParams.get(k)
      if (v) (f as any)[k] = v
    }
    return f
  }
  const { format: fmtMoney } = useCurrency()
  const [filters, setFilters] = useState<Filters>(seedFilters)
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(100)
  const [data, setData] = useState<{ rows: LogRow[]; total: number; limit: number; offset: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [openRow, setOpenRow] = useState<number | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(false)
  // Cascading filter options scoped by the current selection (shared endpoint).
  const facets = useFacets(filters).data
  const [sortBy, setSortBy] = useState('timestamp')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const toggleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col); setSortDir('desc')
    }
    setPage(0)
  }

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v == null || v === '' || v === false) continue
      // from/to are picked in the viewer's LOCAL (IST) wall-clock, but the
      // gateway stores naive-UTC timestamps. Convert local → naive-UTC so the
      // window matches the actual log times (else the filter is offset by the
      // timezone and silently excludes recent rows).
      if (k === 'from' || k === 'to') p.set(k, localToUtcNaive(String(v).trim()))
      else p.set(k, String(v).trim())
    }
    p.set('limit', String(pageSize))
    p.set('offset', String(page * pageSize))
    p.set('sort_by', sortBy)
    p.set('sort_dir', sortDir)
    return p.toString()
  }, [filters, page, pageSize, sortBy, sortDir])

  const load = async () => {
    setLoading(true)
    try {
      const r = await api(`/admin/request-logs?${qs}`)
      setData(r)
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [qs])
  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [autoRefresh, qs])

  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => {
    setPage(0)   // any filter change resets to page 0
    setFilters((f) => ({ ...f, [k]: v }))
  }
  const clear = () => { setFilters({}); setPage(0) }
  // Shared hierarchical filter bar hands back the whole filter object; reset page.
  const onFilters = (f: AnalyticsFilters) => { setPage(0); setFilters(f) }

  const setQuickRange = (hours: number) => {
    const to = new Date()
    const from = hours < 0 ? new Date('2020-01-01T00:00:00Z') : new Date(Date.now() - hours * 3600 * 1000)
    setFilters((f) => ({ ...f, from: isoLocal(from), to: isoLocal(to) }))
    setPage(0)
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Request Logs</h1>
          <p className="text-muted text-sm">
            Searchable per-request log. Dropdowns auto-populate from your data;
            user / model / request_id accept partial matches. Click a row for
            full detail and to open its trace in Jaeger.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-[12px] text-muted cursor-pointer">
            <input type="checkbox" className="accent-accent" checked={autoRefresh}
                   onChange={(e) => setAutoRefresh(e.target.checked)} />
            auto-refresh (5s)
          </label>
          <button className="btn-ghost text-xs" onClick={load} data-testid="logs-refresh"
                  disabled={loading}>
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Filter bar - shared hierarchical cascading dropdowns + free-text/date */}
      <Card>
        <div data-testid="logs-filters" className="space-y-3">
          <HierarchicalFilters value={filters} onChange={onFilters} facets={facets}
            fields={['client', 'workspace', 'component', 'provider', 'model', 'user', 'status', 'use_case']}
            includeSyntheticToggle onClear={clear} />
          <div className="flex flex-wrap items-end gap-3 pt-2 border-t border-border/60">
            <TextInput label="Request ID (contains)" value={filters.request_id || ''}
                        onChange={(v) => set('request_id', v || undefined)}
                        placeholder="req-..."
                        testId="logs-filter-request-id" />
            <DateInput label="From" value={filters.from || ''}
                        onChange={(v) => set('from', v || undefined)}
                        testId="logs-filter-from" />
            <DateInput label="To" value={filters.to || ''}
                        onChange={(v) => set('to', v || undefined)}
                        testId="logs-filter-to" />
            <div className="flex items-end gap-2 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider text-muted self-center mr-1">Quick</span>
              {QUICK_RANGES.map((q) => (
                <button key={q.label} className="btn-ghost text-[11px] py-1 px-2"
                        onClick={() => setQuickRange(q.hours)}
                        data-testid={`logs-quick-${q.hours}h`}>{q.label}</button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between mt-3 text-[11px] text-muted gap-4 flex-wrap">
          <span>{data ? `${data.rows.length.toLocaleString()} of ${total.toLocaleString()} matching rows` : ''}</span>
          <div className="flex items-center gap-3 ml-auto">
            {Object.keys(filters).length > 0 && (
              <button className="text-accent hover:underline flex items-center gap-1"
                      onClick={clear} data-testid="logs-clear-filters">
                <X size={11} /> clear filters
              </button>
            )}
          </div>
        </div>
      </Card>

      {/* Pagination bar */}
      <div className="flex items-center justify-between flex-wrap gap-2 text-[12px]">
        <div className="flex items-center gap-2">
          <span className="text-muted">page size</span>
          <select className="input py-1 text-xs w-20"
                  value={pageSize}
                  onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0) }}
                  data-testid="logs-page-size">
            {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <button className="btn-ghost py-1 px-2 disabled:opacity-30"
                  disabled={page === 0} onClick={() => setPage(0)}
                  data-testid="logs-page-first" aria-label="First page">
            <ChevronsLeft size={14} />
          </button>
          <button className="btn-ghost py-1 px-2 disabled:opacity-30"
                  disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}
                  data-testid="logs-page-prev" aria-label="Previous page">
            <ChevronRight size={14} className="rotate-180" />
          </button>
          <span className="text-muted px-2 tabular-nums">
            page <span className="text-gray-100">{page + 1}</span> of {totalPages.toLocaleString()}
          </span>
          <button className="btn-ghost py-1 px-2 disabled:opacity-30"
                  disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}
                  data-testid="logs-page-next" aria-label="Next page">
            <ChevronRight size={14} />
          </button>
          <button className="btn-ghost py-1 px-2 disabled:opacity-30"
                  disabled={page + 1 >= totalPages} onClick={() => setPage(totalPages - 1)}
                  data-testid="logs-page-last" aria-label="Last page">
            <ChevronsRight size={14} />
          </button>
        </div>
      </div>

      {/* Rows */}
      <Card className="p-0">
        {loading && !data ? (
          <div className="p-6"><Skeleton h={200} /></div>
        ) : !data || data.rows.length === 0 ? (
          <div className="py-16 text-center text-muted text-sm">
            No matching rows. Try clearing filters or extending the time range.
          </div>
        ) : (
          <div className="tabular-nums" data-testid="logs-rows">
            {/* Column headers */}
            <div className="flex items-center gap-3 px-4 py-2 text-[10px] uppercase tracking-wider font-medium border-b"
                 style={{ color: 'var(--color-muted)', borderColor: 'var(--color-border)' }}>
              <span className="w-4" />
              <button onClick={() => toggleSort('timestamp')}
                      className="w-[210px] flex items-center gap-1 uppercase tracking-wider hover:opacity-70 transition-opacity cursor-pointer"
                      style={{ color: sortBy === 'timestamp' ? 'var(--color-text-primary)' : 'var(--color-muted)' }}>
                Timestamp {sortBy === 'timestamp' && (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
              </button>
              <span className="w-[92px]">Status</span>
              <span className="w-[100px]">Client</span>
              <span className="w-[140px]">Workspace</span>
              <span className="w-[100px]">Component</span>
              <span className="w-[120px]">Use Case</span>
              <span className="w-[120px]">Model</span>
              <button onClick={() => toggleSort('latency_ms')}
                      className="w-[70px] flex items-center justify-end gap-1 uppercase tracking-wider hover:opacity-70 transition-opacity cursor-pointer"
                      style={{ color: sortBy === 'latency_ms' ? 'var(--color-text-primary)' : 'var(--color-muted)' }}>
                Latency {sortBy === 'latency_ms' && (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
              </button>
              <button onClick={() => toggleSort('output_tokens')}
                      className="w-[90px] flex items-center justify-end gap-1 uppercase tracking-wider hover:opacity-70 transition-opacity cursor-pointer"
                      style={{ color: sortBy === 'output_tokens' ? 'var(--color-text-primary)' : 'var(--color-muted)' }}>
                Tokens {sortBy === 'output_tokens' && (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
              </button>
              <button onClick={() => toggleSort('cost_usd')}
                      className="w-[70px] flex items-center justify-end gap-1 uppercase tracking-wider hover:opacity-70 transition-opacity cursor-pointer"
                      style={{ color: sortBy === 'cost_usd' ? 'var(--color-text-primary)' : 'var(--color-muted)' }}>
                Cost {sortBy === 'cost_usd' && (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
              </button>
            </div>
            <div className="divide-y" style={{ borderColor: 'var(--color-border)' }}>
            {data.rows.map((r) => {
              const open = openRow === r.id
              return (
                <div key={r.id} className="text-[12.5px]" data-testid={`log-row-${r.request_id}`}>
                  <button className="w-full flex items-center gap-3 px-4 py-3 hover:bg-elevated/30 text-left"
                          onClick={() => setOpenRow(open ? null : r.id)}>
                    {open ? <ChevronDown size={12} className="text-muted" />
                          : <ChevronRight size={12} className="text-muted" />}
                    <span style={{ color: 'var(--color-muted)' }} className="text-[10.5px] w-[210px] shrink-0">
                      {r.timestamp ? fmtDateTime(r.timestamp) : '-'}
                    </span>
                    <span className="w-[92px] shrink-0 inline-flex items-center gap-1.5 text-[11px]"
                          title={`status: ${r.status}`}>
                      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: statusColor(r.status) }} />
                      <span className="truncate" style={{ color: 'var(--color-text-secondary)' }}>{r.status}</span>
                    </span>
                    <span className="w-[100px] truncate" style={{ color: 'var(--color-text-secondary)' }}>{r.client_id || '-'}</span>
                    <span className="w-[140px] truncate" style={{ color: 'var(--color-text-secondary)' }}>{r.workspace_id}</span>
                    <span className="w-[100px] truncate" style={{ color: 'var(--color-text-secondary)' }}>{r.component || '-'}</span>
                    <span className="w-[120px] truncate mono" style={{ color: 'var(--color-info)' }}>{r.use_case || '-'}</span>
                    <span className="w-[120px] truncate mono" style={{ color: 'var(--color-text-secondary)' }}>{r.model_alias}</span>
                    <span className="w-[70px] text-right" style={{ color: 'var(--color-muted)' }}>{fmtMs(r.latency_ms)}</span>
                    <span className="w-[90px] text-right" style={{ color: 'var(--color-muted)' }}>
                      {r.input_tokens.toLocaleString()}/{r.output_tokens.toLocaleString()}
                    </span>
                    <span className="w-[70px] text-right" style={{ color: 'var(--color-muted)' }} title={`$${r.cost_usd.toFixed(6)} USD`}>{fmtMoney(r.cost_usd)}</span>
                  </button>
                  {open && (
                    <ExpandedDetail row={r} jaegerUrl={JAEGER_URL} />
                  )}
                </div>
              )
            })}
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Filter input components ──

function TextInput({ label, value, onChange, placeholder, testId }:
  { label: string; value: string; onChange: (v: string) => void;
    placeholder?: string; testId?: string }) {
  return (
    <label className="block">
      <span className="text-[10.5px] uppercase tracking-wider text-gray-400">{label}</span>
      <input className="input mono mt-0.5 text-xs"
             value={value} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)}
             data-testid={testId} />
    </label>
  )
}

function DateInput({ label, value, onChange, testId }:
  { label: string; value: string; onChange: (v: string) => void; testId?: string }) {
  return (
    <label className="block">
      <span className="text-[10.5px] uppercase tracking-wider text-gray-400">{label}</span>
      <input className="input mt-0.5 text-xs"
             type="datetime-local"
             value={value}
             onChange={(e) => onChange(e.target.value)}
             data-testid={testId} />
    </label>
  )
}

function DetailKV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className="text-muted w-[140px] shrink-0">{k}</span>
      <span className={`text-gray-200 flex-1 break-all ${mono ? 'mono' : ''}`}>{v}</span>
    </div>
  )
}


// ─── WAVE 26: rich tabbed expanded detail ──────────────────────────────────
// Replaces the previous flat KV grid. Tabs available depend on the row:
//   Overview · Why  (failures only)  · Routing · Guardrails (if any) · Provider · Cost · Trace · Raw

const ERROR_TITLES: Record<string, string> = {
  guardrail: 'Guardrail blocked the request',
  rate_limit: 'Rate limit exceeded',
  budget: 'Budget exceeded',
  routing: 'Routing failed (no candidate)',
  provider_error: 'Provider returned an error',
}

function ExpandedDetail({ row, jaegerUrl }: { row: LogRow; jaegerUrl: string }) {
  const isError = row.status !== 'success'
  const cat = row.error_detail?.category
  const hasGuardrails = (row.guardrail_violations?.length ?? 0) > 0
  const tabs: string[] = ['Overview']
  if (isError) tabs.push('Why')
  tabs.push('Routing')
  if (hasGuardrails || cat === 'guardrail') tabs.push('Guardrails')
  tabs.push('Provider', 'Cost', 'Observability', 'Raw')
  const [tab, setTab] = useState<string>(isError ? 'Why' : 'Overview')

  return (
    <div className="bg-app/40 border-t border-border" data-testid={`log-expanded-${row.request_id}`}>
      <div className="flex border-b border-border">
        {tabs.map((t) => (
          <button key={t}
                  onClick={() => setTab(t)}
                  className={`px-4 py-2 text-[11.5px] uppercase tracking-wider transition border-b-2 ${
                    tab === t
                      ? 'text-accent border-accent bg-elevated/30'
                      : 'text-muted border-transparent hover:text-gray-200'
                  }`}
                  data-testid={`log-tab-${t.toLowerCase()}`}>
            {t}{t === 'Why' && cat ? <span className="ml-1 text-[9px] opacity-70">({cat})</span> : null}
          </button>
        ))}
      </div>
      <div className="p-4 text-[12px] min-h-[120px]">
        {tab === 'Overview' && <OverviewTab row={row} />}
        {tab === 'Why' && <WhyTab row={row} />}
        {tab === 'Routing' && <RoutingTab row={row} />}
        {tab === 'Guardrails' && <GuardrailsTab row={row} />}
        {tab === 'Provider' && <ProviderTab row={row} />}
        {tab === 'Cost' && <CostTab row={row} />}
        {tab === 'Observability' && <ObservabilityTab row={row} jaegerUrl={jaegerUrl} />}
        {tab === 'Raw' && <RawTab row={row} />}
      </div>
    </div>
  )
}

function OverviewTab({ row }: { row: LogRow }) {
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
      <DetailKV k="request_id" v={row.request_id} mono />
      <DetailKV k="timestamp" v={fmtDateTime(row.timestamp)} />
      <DetailKV k="status" v={row.status} />
      <DetailKV k="event_kind" v={row.event_kind} />
      <DetailKV k="client → workspace → user → component"
                v={`${row.client_id || '-'} → ${row.workspace_id} → ${row.user_id || '-'} → ${row.component || '-'}`} />
      <DetailKV k="use_case" v={row.use_case || '-'} mono />
      <DetailKV k="engine" v={row.engine} />
      {/* Show the caller's REQUESTED alias and the gateway's RESOLVED target
          separately - the requested alias is what a failing app actually sent. */}
      <DetailKV k="requested alias" v={row.model_alias || '-'} mono />
      <DetailKV k="resolved provider / model"
                v={row.provider_model_id ? `${row.provider} / ${row.provider_model_id}` : '- (not routed)'} mono />
      <DetailKV k="latency" v={fmtMs(row.latency_ms)} />
    </div>
  )
}

function WhyTab({ row }: { row: LogRow }) {
  const { format: fmtMoney } = useCurrency()
  const e = row.error_detail
  if (!e) {
    return (
      <div className="text-muted">
        No structured error detail captured for this request.<br />
        <span className="text-[11px]">error_type: <span className="mono text-gray-300">{row.error_type || '-'}</span></span>
      </div>
    )
  }
  const title = ERROR_TITLES[e.category || ''] || `Failed: ${e.category || row.error_type || 'unknown'}`
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="text-base font-semibold text-red-300">{title}</span>
        <span className="text-[10px] text-muted">{row.error_type ? `(${row.error_type})` : ''}</span>
      </div>

      {e.category === 'guardrail' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
            <DetailKV k="Rule" v={e.rule || '-'} />
            <DetailKV k="Action" v={(e.action || '').toUpperCase()} />
            <DetailKV k="Stage" v={e.stage || 'input'} />
            <DetailKV k="Match count" v={String(e.matches?.length ?? 0)} />
          </div>
          {e.matches?.length ? (
            <div className="rounded border border-red-500/30 bg-red-500/5 p-3 space-y-2">
              <div className="text-[11px] uppercase tracking-wider text-red-300/80">Matched detectors</div>
              {e.matches.map((m, i) => (
                <div key={i} className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11.5px]">
                  <DetailKV k="Detector" v={m.detector} />
                  <DetailKV k="Sub-category" v={m.sub_category} />
                  <DetailKV k="Confidence" v={`${(m.confidence * 100).toFixed(0)}%`} />
                  <DetailKV k="Excerpt (masked)" v={m.excerpt} mono />
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {e.category === 'rate_limit' && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
          <DetailKV k="Scope" v={e.scope || '-'} />
          <DetailKV k="Limit type" v={(e.limit_type || '').toUpperCase()} />
          <DetailKV k="Limit" v={e.limit !== undefined ? String(e.limit) : '-'} />
          <DetailKV k="Current" v={e.current !== undefined ? String(e.current) : '-'} />
          <DetailKV k="Exceeded by" v={e.exceeded_by !== undefined ? String(e.exceeded_by) : '-'} />
          <DetailKV k="Retry after" v={e.retry_after_seconds !== undefined ? `${e.retry_after_seconds}s` : '-'} />
        </div>
      )}

      {e.category === 'budget' && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
          <DetailKV k="Scope" v={e.scope || '-'} />
          <DetailKV k="Budget" v={e.budget_usd !== undefined ? fmtMoney(e.budget_usd) : '-'} />
          <DetailKV k="Spent" v={e.spent_usd !== undefined ? fmtMoney(e.spent_usd) : '-'} />
          <DetailKV k="Exceeded by" v={e.exceeded_by_usd !== undefined ? fmtMoney(e.exceeded_by_usd) : '-'} />
        </div>
      )}

      {e.category === 'routing' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
            <DetailKV k="Alias requested" v={e.alias_requested || '-'} mono />
            <DetailKV k="Result" v={e.result || '-'} />
          </div>
          <DetailKV k="Reason" v={e.reason || '-'} />
          {e.available_aliases?.length ? (
            <div>
              <span className="text-muted text-[11px]">Available aliases for this workspace:</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {e.available_aliases.map((a) => (
                  <span key={a} className="text-[11px] px-2 py-0.5 rounded bg-elevated border border-border mono">{a}</span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {e.category === 'provider_error' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
            <DetailKV k="Provider" v={e.provider || row.provider} />
            <DetailKV k="Model" v={e.model_id || row.provider_model_id} mono />
            <DetailKV k="HTTP status" v={String(e.http_status ?? '-')} />
            <DetailKV k="Retries" v={String(e.retries ?? 0)} />
            <DetailKV k="Fallback attempted" v={e.fallback_attempted ? 'yes' : 'no'} />
            {e.exception_type && <DetailKV k="Exception type" v={e.exception_type} mono />}
          </div>
          {e.timeout && (
            <div className="rounded border p-2.5 text-[11.5px]"
                 style={{ borderColor: 'var(--color-accent)', background: 'var(--color-accent-soft, rgba(99,102,241,0.07))' }}>
              <span className="text-muted">Effective request timeout applied: </span>
              <span className="mono" style={{ color: 'var(--color-text)' }}>{e.timeout.effective_s}s</span>
              <span className="text-muted"> (source: {e.timeout.source}; max {e.timeout.max_s}s). This is the value the gateway actually enforced - not an engine's generic default.</span>
            </div>
          )}
          <div className="rounded border border-red-500/30 bg-red-500/5 p-3">
            <div className="text-[11px] uppercase tracking-wider text-red-300/80 mb-1">Provider raw response</div>
            <pre className="mono text-[11px] text-red-100 whitespace-pre-wrap break-all">{
              typeof e.raw_response === 'string'
                ? e.raw_response
                : JSON.stringify(e.raw_response ?? e.raw_message ?? e.mapped_message ?? '', null, 2)
            }</pre>
          </div>
          {e.fallback_chain?.length ? (
            <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
              <div className="text-[11px] uppercase tracking-wider text-amber-300/80 mb-1">Failover sequence - in actual attempt order</div>
              {e.fallback_chain.map((f, i) => (
                <div key={i} className="text-[11.5px] flex items-center flex-wrap gap-1.5">
                  <span className="mono text-gray-200">{f.from}</span>
                  <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: 'var(--color-danger-soft, rgba(248,113,113,0.12))', color: 'var(--color-danger)' }}>failed</span>
                  <span className="text-muted">→ fell over to</span>
                  <span className="mono text-gray-200">{f.to}</span>
                </div>
              ))}
              <div className="text-[10px] text-muted mt-1.5">Left = tried first; the gateway only calls the next target after the one before it fails.</div>
            </div>
          ) : null}
          {e.attempts?.length ? (
            <div>
              <span className="text-muted text-[11px]">Attempts in actual order - why each target failed:</span>
              <div className="mt-1 space-y-1">
                {e.attempts.map((a, i) => (
                  <div key={i} className="flex items-start gap-2 text-[11px] rounded bg-elevated border border-border px-2 py-1.5">
                    <span className="text-muted mono shrink-0">{i + 1}.</span>
                    <span className="mono text-gray-200 shrink-0">{a.provider}/{a.model_id}</span>
                    {a.skipped ? (
                      <span className="text-[10px] px-1 py-0.5 rounded shrink-0" style={{ background: 'rgba(148,163,184,0.15)', color: 'var(--color-muted)' }}>skipped (circuit open)</span>
                    ) : (
                      <span className="text-[10px] px-1 py-0.5 rounded shrink-0" style={{ background: 'var(--color-danger-soft, rgba(248,113,113,0.12))', color: 'var(--color-danger)' }}>
                        {a.http_status ?? 'err'}{a.error_type ? ` · ${a.error_type}` : ''}{a.ms != null ? ` · ${a.ms}ms` : ''}
                      </span>
                    )}
                    {a.message && <span className="text-[10.5px] text-muted break-all">{a.message}</span>}
                  </div>
                ))}
              </div>
              <div className="text-[10px] text-muted mt-1.5">Left/top = tried first; the gateway only calls the next target after the one before it fails.</div>
            </div>
          ) : e.attempted_targets?.length ? (
            <div>
              <span className="text-muted text-[11px]">All attempted targets:</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {e.attempted_targets.map((t, i) => (
                  <span key={i} className="text-[11px] px-2 py-0.5 rounded bg-elevated border border-border mono">
                    {t.provider}/{t.model_id} (w={t.weight})
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* Unknown category fallback - show JSON */}
      {!['guardrail', 'rate_limit', 'budget', 'routing', 'provider_error'].includes(e.category || '') && (
        <pre className="mono text-[11px] text-gray-300 bg-elevated p-3 rounded border border-border whitespace-pre-wrap">
{JSON.stringify(e, null, 2)}
        </pre>
      )}
    </div>
  )
}

function RoutingTab({ row }: { row: LogRow }) {
  const e = row.error_detail
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
        <DetailKV k="Requested alias" v={row.model_alias} mono />
        <DetailKV k="Resolved provider" v={row.provider} />
        <DetailKV k="Resolved model" v={row.provider_model_id || '-'} mono />
        <DetailKV k="Engine" v={row.engine} />
      </div>
      {e?.attempted_targets?.length ? (
        <div>
          <span className="text-muted text-[11px]">All candidates considered:</span>
          <div className="mt-1 flex flex-wrap gap-1">
            {e.attempted_targets.map((t, i) => (
              <span key={i} className="text-[11px] px-2 py-0.5 rounded bg-elevated border border-border mono">
                {t.provider}/{t.model_id} (w={t.weight})
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {e?.fallback_chain?.length ? (
        <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3 mt-2">
          <div className="text-[11px] uppercase tracking-wider text-amber-300/80 mb-1">Failover sequence - in actual attempt order</div>
          {e.fallback_chain.map((f, i) => (
            <div key={i} className="text-[11.5px] flex items-center flex-wrap gap-1.5">
              <span className="mono text-gray-200">{f.from}</span>
              <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: 'var(--color-danger-soft, rgba(248,113,113,0.12))', color: 'var(--color-danger)' }}>failed</span>
              <span className="text-muted">→ fell over to</span>
              <span className="mono text-gray-200">{f.to}</span>
            </div>
          ))}
          <div className="text-[10px] text-muted mt-1.5">Left = tried first; a fallback runs only after the target before it fails.</div>
        </div>
      ) : null}
    </div>
  )
}

function GuardrailsTab({ row }: { row: LogRow }) {
  const violations = row.guardrail_violations || []
  if (violations.length === 0) {
    return <div className="text-muted">No guardrail violations recorded for this request.</div>
  }
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-muted">
        {violations.length} violation(s) joined from <span className="mono">guardrail_violations</span> on request_id.
      </div>
      {violations.map((v, i) => (
        <div key={i} className="rounded border border-red-500/30 bg-red-500/5 p-3 grid grid-cols-2 gap-x-6 gap-y-1">
          <DetailKV k="Rule" v={v.rule} />
          <DetailKV k="Detector" v={v.detector} />
          <DetailKV k="Action" v={v.action.toUpperCase()} />
          <DetailKV k="Stage" v={v.stage} />
          <DetailKV k="Severity" v={v.severity} />
          <DetailKV k="Timestamp" v={v.timestamp || '-'} />
          <div className="col-span-2 mt-1">
            <span className="text-muted text-[10px] uppercase tracking-wider">Excerpt (masked)</span>
            <div className="mono text-[11px] text-red-200 mt-0.5 break-all">{v.excerpt}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ProviderTab({ row }: { row: LogRow }) {
  const e = row.error_detail
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
      <DetailKV k="Provider" v={row.provider} />
      <DetailKV k="Model" v={row.provider_model_id || row.model_alias} mono />
      <DetailKV k="Engine" v={row.engine} />
      <DetailKV k="Stream" v={row.stream ? 'yes' : 'no'} />
      <DetailKV k="Latency" v={fmtMs(row.latency_ms)} />
      {e?.http_status !== undefined && <DetailKV k="HTTP status" v={String(e.http_status)} />}
      {e?.retries !== undefined && <DetailKV k="Retries" v={String(e.retries)} />}
      {e?.raw_response !== undefined && (
        <div className="col-span-2 rounded border border-red-500/30 bg-red-500/5 p-3 mt-2">
          <div className="text-[11px] uppercase tracking-wider text-red-300/80 mb-1">Raw provider response</div>
          <pre className="mono text-[11px] text-red-100 whitespace-pre-wrap break-all">{
            typeof e.raw_response === 'string' ? e.raw_response : JSON.stringify(e.raw_response, null, 2)
          }</pre>
        </div>
      )}
    </div>
  )
}

function CostTab({ row }: { row: LogRow }) {
  const { format: fmtMoney } = useCurrency()
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
      <DetailKV k="Input tokens" v={row.input_tokens.toLocaleString()} />
      <DetailKV k="Output tokens" v={row.output_tokens.toLocaleString()} />
      <DetailKV k="Total tokens" v={(row.input_tokens + row.output_tokens).toLocaleString()} />
      <DetailKV k="Cost" v={fmtMoney(row.cost_usd)} />
      <DetailKV k="Cost (USD)" v={`$${row.cost_usd.toFixed(6)}`} />
      <DetailKV k="Cost note"
                v={row.status !== 'success' ? 'failed request - provider was not invoked or returned no usage' : ''} />
    </div>
  )
}

// Every observability signal is correlated by this request's id. Instead of only
// the OTel/Jaeger link, surface ALL of them so an operator can debug from one place.
function ObservabilityTab({ row, jaegerUrl }: { row: LogRow; jaegerUrl: string }) {
  const cid = row.request_id
  const tagsParam = encodeURIComponent('{"correlation_id":"' + cid + '"}')
  const isErr = row.status !== 'success'
  const gv = row.guardrail_violations || []
  // Scope the aggregate signals to THIS request: a time window around it + its
  // metric label-set. (Counters/histograms are aggregate by design - they can't
  // be filtered by correlation_id - so we resolve them to this request's labels
  // and a ±2-min window, which is the closest per-request view metrics allow.)
  const ts = row.timestamp ? new Date(row.timestamp).getTime() : Date.now()
  const fromMs = ts - 120_000, toMs = ts + 120_000
  const promExpr = `gateway_requests_total{provider="${row.provider}",status="${row.status}"}`
  const promHref = `${PROMETHEUS_URL}/graph?g0.expr=${encodeURIComponent(promExpr)}&g0.tab=0&g0.range_input=15m`
  const grafanaHref = `${GRAFANA_URL}/?from=${fromMs}&to=${toMs}&var-workspace=${encodeURIComponent(row.workspace_id)}&var-provider=${encodeURIComponent(row.provider)}`

  const Signal = ({ title, desc, href, testId, badge }:
    { title: string; desc: React.ReactNode; href?: string; testId?: string; badge?: string }) => (
    <div className="rounded-lg border border-border bg-app px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-gray-100">{title}
          {badge && <span className="ml-2 text-[9px] uppercase tracking-wider text-accent">{badge}</span>}
        </span>
        {href && (
          <a href={href} target="_blank" rel="noreferrer" data-testid={testId}
             className="text-[11px] inline-flex items-center gap-1 text-accent hover:underline">
            <ExternalLink size={10} /> open
          </a>
        )}
      </div>
      <div className="text-[11px] text-muted mt-1">{desc}</div>
    </div>
  )

  const RecKV = ({ k, v }: { k: string; v: React.ReactNode }) => (
    <div className="min-w-0">
      <span className="text-muted">{k}: </span>
      <span className="mono text-gray-200 break-all">{v}</span>
    </div>
  )

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap text-[11.5px]">
        <span className="text-muted">correlation id</span>
        <span className="mono text-gray-100 bg-elevated px-2 py-0.5 rounded">{cid}</span>
        <button className="text-[11px] text-accent hover:underline"
                onClick={() => navigator.clipboard?.writeText(cid)}>copy</button>
        <span className="text-muted">- resolved to the exact request below.</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
        <Signal title="OpenTelemetry trace → Jaeger" badge={isErr ? 'error' : 'per-request'}
          testId={`log-trace-${cid}`}
          href={`${jaegerUrl}/search?service=agnos-proxy-llm-gateway&tags=${tagsParam}`}
          desc={<>Parent <span className="mono">gateway.chat</span> + child spans (auth · routing · guardrails · engine · governance) with per-stage timing.{isErr ? ' The failing span carries status=ERROR + http.status_code + error type.' : ''}</>} />

        <Signal title="Metrics → Prometheus / Grafana" badge="aggregate · scoped"
          testId={`log-metrics-${cid}`}
          href={promHref}
          desc={<>Metrics are aggregate (counters/histograms can\u2019t be filtered by correlation id), so this opens the exact series this call incremented: <span className="mono">{promExpr}</span>. <a className="text-accent hover:underline" href={grafanaHref} target="_blank" rel="noreferrer">Grafana ↗</a> scopes a dashboard to workspace <span className="mono">{row.workspace_id}</span> / provider <span className="mono">{row.provider}</span> around this time.</>} />
      </div>

      {/* the concrete per-request governance record (the request_logs row itself). */}
      <div className="rounded-lg border border-border bg-app px-3 py-2.5">
        <div className="text-[12px] font-semibold text-gray-100 mb-1.5">This request\u2019s governance record</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-[11px]">
          <RecKV k="status" v={<b className={isErr ? 'text-danger' : 'text-ok'}>{row.status}</b>} />
          <RecKV k="call_kind" v={row.call_kind} />
          <RecKV k="engine" v={row.engine} />
          <RecKV k="provider" v={row.provider} />
          <RecKV k="model" v={row.provider_model_id} />
          <RecKV k="tokens (in/out)" v={`${row.input_tokens}/${row.output_tokens}`} />
          <RecKV k="cost" v={`$${(row.cost_usd || 0).toFixed(6)}`} />
          <RecKV k="latency" v={fmtMs2(row.latency_ms)} />
          <RecKV k="workspace" v={row.workspace_id} />
          <RecKV k="component" v={row.component || '-'} />
          <RecKV k="use_case" v={row.use_case || '-'} />
          {isErr && <RecKV k="error_type" v={<span className="text-danger">{row.error_type || '-'}</span>} />}
        </div>
      </div>

      {gv.length > 0 && (
        <div className="rounded-lg border border-violet-500/40 bg-violet-500/5 px-3 py-2.5">
          <div className="text-[12px] font-semibold text-gray-100">Guardrail decisions ({gv.length})</div>
          <div className="text-[11px] text-muted mt-1">
            {gv.map((g: any, i: number) => (
              <div key={i} className="mono">{g.stage}/{g.detector} → {g.action} · {g.excerpt}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function fmtMs2(ms?: number | null): string {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

function RawTab({ row }: { row: LogRow }) {
  return (
    <pre className="mono text-[11px] text-gray-300 bg-elevated p-3 rounded border border-border whitespace-pre-wrap break-all">
{JSON.stringify(row, null, 2)}
    </pre>
  )
}
