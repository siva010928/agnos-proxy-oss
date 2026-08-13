// Live Requests - real-time monitoring console (WAVE 24 rebuild).
//
// Layout:
//   Top:    rate strip (req/s · tok/s · err/s · streams · last event)
//   Left:   live event feed (filterable)
//   Right:  selected-request detail drawer
//   Bottom: provider distribution mini-bar
//
// Teaching empty state when idle: sample event, glossary, how-to-generate.

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, AlertTriangle, Clock, Cpu, Search, Zap } from 'lucide-react'
import { useFeed } from '../App'
import { GovEvent } from '../lib/sse'
import { LiveFeed } from '../components/LiveFeed'
import { Card, LiveDot, Pill, ProviderBadge, StatTile } from '../components/ui'
import { fmtMs, timeAgo } from '../lib/format'
import { useCurrency } from '../lib/currency'
import { useFacets, AnalyticsFilters } from '../lib/api'
import { HierarchicalFilters } from '../components/Filters'

const EVENT_KINDS = [
  ['', 'All events'],
  ['RequestSuccess', 'Succeeded'],
  ['GuardrailDecision', 'Guardrail'],
  ['Fallback', 'Fallback'],
  ['RateLimited', 'Rate-limited'],
  ['RequestError', 'Error'],
  ['CacheHit', 'Cache hit'],
]

export function LiveRequests() {
  const { events, connected } = useFeed()
  const { format: fmtMoney } = useCurrency()
  const [kind, setKind] = useState('')
  const [errorsOnly, setErrorsOnly] = useState(false)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<GovEvent | null>(null)
  // Shared hierarchical cascading filters (same as Analytics / Request Logs).
  const [filters, setFilters] = useState<AnalyticsFilters>({})
  const facets = useFacets(filters).data
  // client → workspace membership (the SSE event carries no client_id, so scope
  // by the client's workspace set from facets).
  const clientWs = useMemo(() => {
    if (!filters.client || !facets) return null
    return new Set(facets.workspaces.filter((w) => w.client_id === filters.client).map((w) => w.workspace_id))
  }, [filters.client, facets])

  // Filtered events (live buffer, client-side)
  const filtered = events.filter((e) =>
    (!kind || e.event_kind === kind) &&
    (!clientWs || (e.workspace_id ? clientWs.has(e.workspace_id) : false)) &&
    (!filters.workspace || e.workspace_id === filters.workspace) &&
    (!filters.component || e.component === filters.component) &&
    (!filters.provider || e.provider === filters.provider) &&
    (!filters.model || (e.model_alias || '').toLowerCase().includes(filters.model.toLowerCase())) &&
    (!filters.user || (e.user_id || '').toLowerCase().includes(filters.user.toLowerCase())) &&
    (!filters.use_case || e.use_case === filters.use_case) &&
    (!filters.status || (filters.status === 'error'
      ? e.event_kind === 'RequestError'
      : e.event_kind === 'RequestSuccess' || e.event_kind === 'CacheHit')) &&
    (!errorsOnly || e.event_kind === 'RequestError') &&
    (!q || (e.request_id || '').includes(q) || (e.use_case || '').includes(q) ||
           (e.workspace_id || '').includes(q) || (e.model_alias || '').includes(q)))

  // Rate metrics (rolling 60s window from the buffer)
  const now = Date.now()
  const window60 = events.filter((e) => e.ts_ms && (now - e.ts_ms) < 60_000)
  const reqPerSec = window60.length / 60
  const tokPerSec = window60.reduce((a, e) => a + (e.input_tokens || 0) + (e.output_tokens || 0), 0) / 60
  const errPerSec = window60.filter((e) => e.event_kind === 'RequestError').length / 60
  const lastEvent = events[0]?.ts_ms ? new Date(events[0].ts_ms) : null

  // Provider distribution from the buffer
  const provDist = useMemo(() => {
    const m: Record<string, number> = {}
    for (const e of events) if (e.provider) m[e.provider] = (m[e.provider] || 0) + 1
    const total = Object.values(m).reduce((a, b) => a + b, 0) || 1
    return Object.entries(m).map(([p, n]) => ({ provider: p, count: n, pct: Math.round(n / total * 100) }))
      .sort((a, b) => b.count - a.count)
  }, [events])

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Live Requests</h1>
          <p className="text-muted text-sm">
            Real-time governance event stream · {filtered.length} of {events.length} buffered
          </p>
        </div>
        <LiveDot on={connected} />
      </div>

      {/* Rate strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatTile label="Requests/sec" value={reqPerSec} decimals={1} color="#6366F1" icon={<Activity size={14} />} />
        <StatTile label="Tokens/sec" value={tokPerSec} decimals={0} color="#60A5FA" icon={<Cpu size={14} />} />
        <StatTile label="Errors/sec" value={errPerSec} decimals={2} color="#F87171" icon={<AlertTriangle size={14} />} />
        <StatTile label="Buffer depth" value={events.length} color="#A78BFA" icon={<Zap size={14} />} />
        <StatTile label="Last event"
                  value={lastEvent ? Math.round((now - lastEvent.getTime()) / 1000) : 0}
                  text={lastEvent ? undefined : 'never'}
                  suffix="s ago" color="#34D399" icon={<Clock size={14} />} />
      </div>

      {/* Filters */}
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 bg-app border border-border rounded-xl px-3 py-1.5 flex-1 min-w-[180px]">
            <Search size={13} className="text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="Search (contains): request_id, use_case, workspace, model"
                   className="bg-transparent outline-none text-sm flex-1 text-gray-200" />
          </div>
          <select className="input w-auto text-xs" value={kind} onChange={(e) => setKind(e.target.value)}>
            {EVENT_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer whitespace-nowrap">
            <input type="checkbox" className="accent-accent" checked={errorsOnly}
                   onChange={(e) => setErrorsOnly(e.target.checked)} />
            Errors only
          </label>
        </div>
        <div className="mt-2 pt-2 border-t border-border/60">
          <HierarchicalFilters value={filters} onChange={setFilters} facets={facets}
            fields={['client', 'workspace', 'component', 'provider', 'model', 'user', 'status', 'use_case']} />
        </div>
      </Card>

      {/* Main content: feed + detail + provider dist */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
        <Card className="p-4">
          {events.length === 0 ? (
            <div className="py-10 space-y-4">
              <div className="text-center">
                <div className="text-gray-200 text-base mb-1">Waiting for events…</div>
                <div className="text-muted text-sm max-w-md mx-auto">
                  Events appear here in real-time as requests flow through the gateway.
                  Each row shows workspace, component, provider, model, tokens, cost, and latency.
                </div>
              </div>
              {/* Teaching: sample event row */}
              <div className="bg-app border border-border rounded-xl p-3 max-w-lg mx-auto text-[11.5px] space-y-1.5">
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Example event shape</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  <span className="text-muted">request_id</span><span className="mono text-gray-300">req-7e56f3a…</span>
                  <span className="text-muted">workspace_id</span><span className="mono text-gray-300">ws-novatech-payments</span>
                  <span className="text-muted">component</span><span className="mono text-gray-300">document-processing</span>
                  <span className="text-muted">provider</span><span className="text-gray-300">bedrock</span>
                  <span className="text-muted">model</span><span className="mono text-gray-300">claude-sonnet-4-5</span>
                  <span className="text-muted">tokens (in/out)</span><span className="text-gray-300 tabular-nums">14 / 32</span>
                  <span className="text-muted">cost</span><span className="text-gray-300 tabular-nums">$0.00039</span>
                  <span className="text-muted">latency</span><span className="text-gray-300 tabular-nums">1847ms</span>
                </div>
              </div>
              <div className="text-center text-[11px] text-muted">
                Send requests via the <Link to="/playground" className="text-accent hover:underline">Playground</Link> or any component with a workspace key.
                {lastEvent && <span className="ml-3">· last event: {timeAgo(lastEvent.getTime())}</span>}
              </div>
            </div>
          ) : (
            <LiveFeed events={filtered} height="52vh" onSelect={setSelected} />
          )}
        </Card>

        {/* Right panel: selected event detail + provider distribution */}
        <div className="space-y-4">
          <Card className="p-4">
            <div className="text-xs uppercase tracking-wider text-muted mb-2">
              {selected ? 'Request detail' : 'Select an event'}
            </div>
            {selected ? (
              <div className="space-y-1.5 text-[12px]">
                <DetailRow k="request_id" v={selected.request_id} mono />
                <DetailRow k="event_kind" v={selected.event_kind} />
                <DetailRow k="workspace" v={selected.workspace_id} mono />
                <DetailRow k="component" v={(selected as any).component} />
                <DetailRow k="user" v={selected.user_id} mono />
                <DetailRow k="provider" v={selected.provider} />
                <DetailRow k="model" v={selected.model_alias} mono />
                <DetailRow k="tokens" v={`${selected.input_tokens || 0} in / ${selected.output_tokens || 0} out`} />
                <DetailRow k="cost" v={fmtMoney(selected.cost_usd || 0)} />
                <DetailRow k="latency" v={fmtMs(selected.latency_ms || 0)} />
                {selected.action && <DetailRow k="action" v={selected.action} />}
                {selected.rule && <DetailRow k="rule" v={selected.rule} />}
                {selected.error_type && <DetailRow k="error" v={selected.error_type} />}
                {selected.from_provider && <DetailRow k="fallback" v={`${selected.from_provider} → ${selected.to_provider}`} />}
              </div>
            ) : (
              <div className="text-muted text-xs py-4">
                Click an event in the feed to inspect its full detail here.
              </div>
            )}
          </Card>

          <Card className="p-4">
            <div className="text-xs uppercase tracking-wider text-muted mb-2">Provider distribution (buffer)</div>
            {provDist.length === 0 ? (
              <div className="text-muted text-xs py-2">No events yet.</div>
            ) : (
              <div className="space-y-1.5">
                {provDist.map((d) => (
                  <div key={d.provider} className="flex items-center gap-2">
                    <ProviderBadge provider={d.provider} />
                    <div className="flex-1 h-2 bg-elevated rounded-full overflow-hidden">
                      <div className="h-full bg-accent rounded-full" style={{ width: `${d.pct}%` }} />
                    </div>
                    <span className="text-[11px] text-muted tabular-nums w-10 text-right">{d.pct}%</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}

function DetailRow({ k, v, mono }: { k: string; v: any; mono?: boolean }) {
  if (v == null || v === '') return null
  return (
    <div className="flex gap-2">
      <span className="text-muted w-[90px] shrink-0">{k}</span>
      <span className={`text-gray-200 break-all ${mono ? 'mono' : ''}`}>{String(v)}</span>
    </div>
  )
}
