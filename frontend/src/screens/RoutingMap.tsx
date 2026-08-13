// Routing Map - table-first admin view (WAVE 24 rebuild).
//
// Primary: sortable, searchable routing table answering:
//   - Which workspace costs most?
//   - Which provider receives most traffic?
//   - Which alias has fallbacks?
//   - What's the operational health?
//
// Secondary (toggle): the SVG visualization for demo eye-candy.

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Eye } from 'lucide-react'
import { useWorkspaces, useCost, useFacets, AnalyticsFilters } from '../lib/api'
import { HierarchicalFilters } from '../components/Filters'
import { Card, SectionTitle, ProviderBadge, Skeleton, SearchInput } from '../components/ui'
import { provColor } from '../lib/theme'
import { useCurrency } from '../lib/currency'

type SortKey = 'workspace' | 'alias' | 'requests' | 'cost'
type SortDir = 'asc' | 'desc'

interface RouteRow {
  workspace_id: string
  display_name: string
  client_id: string | null
  alias: string
  primary: string
  fallback: string | null
  targets: { provider: string; model_id: string; weight?: number }[]
  requests: number
  cost: number
  redundancy: 'resilient' | 'single'
}

export function RoutingMap() {
  const ws = useWorkspaces()
  const [filters, setFilters] = useState<AnalyticsFilters>({})
  const facets = useFacets(filters).data
  // Scope volume/share to the client+workspace selection (provider is applied to
  // the routes/graph below, not the roll-up, so the share strip stays meaningful).
  const scope = { client: filters.client, workspace: filters.workspace }
  const costByWs = useCost('workspace', scope)
  const { format: fmtCost } = useCurrency()
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('requests')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [showGraph, setShowGraph] = useState(false)

  // Scope the workspaces (table AND visualization) to the client/workspace filter.
  const workspaces = (ws.data?.workspaces || []).filter((w: any) =>
    (!filters.client || w.client_id === filters.client) &&
    (!filters.workspace || w.workspace_id === filters.workspace))
  const volume: Record<string, { requests: number; cost: number }> = {}
  for (const r of costByWs.data?.rows || []) {
    volume[r.key] = { requests: r.requests, cost: r.cost_usd }
  }

  // Build flat route rows
  const rows: RouteRow[] = useMemo(() => {
    const out: RouteRow[] = []
    for (const w of workspaces) {
      for (const [alias, targets] of Object.entries(w.chat_models || {})) {
        const tList = targets as any[]
        if (!tList.length) continue
        const primary = tList[0]?.provider || '?'
        const fallback = tList.length > 1 ? tList.slice(1).map((t: any) => t.provider).join(' → ') : null
        out.push({
          workspace_id: w.workspace_id,
          display_name: w.display_name || w.name || w.workspace_id,
          client_id: w.client_id,
          alias,
          primary,
          fallback,
          targets: tList,
          requests: volume[w.workspace_id]?.requests || 0,
          cost: volume[w.workspace_id]?.cost || 0,
          // Honest, config-derived redundancy (not a fake live health probe):
          // a route with ≥1 fallback target survives a primary outage.
          redundancy: tList.length > 1 ? 'resilient' : 'single',
        })
      }
    }
    return out
  }, [workspaces, volume])

  // Filter + sort (client/workspace already scoped above; provider narrows routes)
  const filtered = rows.filter((r) => {
    if (filters.provider && !r.targets.some((t) => t.provider === filters.provider)) return false
    if (!search) return true
    const s = search.toLowerCase()
    return r.workspace_id.includes(s) || r.display_name.toLowerCase().includes(s) ||
           r.alias.includes(s) || r.primary.includes(s) || (r.fallback || '').includes(s)
  })

  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0
    if (sortKey === 'workspace') cmp = a.display_name.localeCompare(b.display_name)
    else if (sortKey === 'alias') cmp = a.alias.localeCompare(b.alias)
    else if (sortKey === 'requests') cmp = a.requests - b.requests
    else if (sortKey === 'cost') cmp = a.cost - b.cost
    return sortDir === 'desc' ? -cmp : cmp
  })

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return null
    return sortDir === 'desc' ? <ChevronDown size={12} /> : <ChevronUp size={12} />
  }

  // Provider utilization (from costByProvider), scoped to client+workspace
  const provCost = useCost('provider', scope)
  const provReqs: Record<string, number> = {}
  for (const r of provCost.data?.rows || []) if (r.key) provReqs[r.key] = r.requests
  const totalReqs = Object.values(provReqs).reduce((a, b) => a + b, 0) || 1

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Routing Map</h1>
          <p className="text-muted text-sm">
            How aliases resolve to providers · sortable by traffic / cost · primary + fallback chain
          </p>
        </div>
        <button className="btn-ghost text-xs" onClick={() => setShowGraph((v) => !v)}>
          <Eye size={13} /> {showGraph ? 'Hide' : 'Show'} visualization
        </button>
      </div>

      {/* Provider utilization strip */}
      <Card className="p-3">
        <div className="flex items-center gap-4 flex-wrap">
          <span className="text-[10px] uppercase tracking-wider text-muted">Provider traffic share</span>
          {Object.entries(provReqs).sort((a, b) => b[1] - a[1]).map(([p, n]) => (
            <div key={p} className="flex items-center gap-1.5">
              <ProviderBadge provider={p} />
              <span className="text-xs text-gray-200 tabular-nums">
                {totalReqs > 0 ? Math.round(n / totalReqs * 100) : 0}%
              </span>
              <span className="text-[10px] text-muted">({n.toLocaleString()} req)</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Search + hierarchical filters (client → workspace → provider) - apply to
          BOTH the table and the visualization so you see the filtering power. */}
      <Card className="p-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <HierarchicalFilters value={filters} onChange={setFilters} facets={facets}
            fields={['client', 'workspace', 'provider']} />
          <div className="flex items-center gap-2">
            <SearchInput value={search} onChange={setSearch}
                         placeholder="Search workspace, alias, or provider"
                         className="w-64" />
            <span className="text-[11px] text-muted whitespace-nowrap">{sorted.length} routes</span>
          </div>
        </div>
      </Card>

      {/* Routing table (primary view) */}
      <Card className="p-0">
        {ws.isLoading ? <div className="p-6"><Skeleton h={200} /></div> : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <Th onClick={() => toggleSort('workspace')}>Workspace <SortIcon k="workspace" /></Th>
                  <Th onClick={() => toggleSort('alias')}>Alias <SortIcon k="alias" /></Th>
                  <th className="th">Primary</th>
                  <th className="th">Fallback chain</th>
                  <Th onClick={() => toggleSort('requests')}>Requests <SortIcon k="requests" /></Th>
                  <Th onClick={() => toggleSort('cost')}>Cost <SortIcon k="cost" /></Th>
                  <th className="th">Redundancy</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={`${r.workspace_id}-${r.alias}-${i}`} className="border-t border-border/60 hover:bg-elevated/30">
                    <td className="td">
                      <div className="text-sm text-gray-200">{r.display_name}</div>
                      <div className="text-[10px] text-muted mono">{r.workspace_id}</div>
                    </td>
                    <td className="td mono text-xs text-gray-300">{r.alias}</td>
                    <td className="td"><ProviderBadge provider={r.primary} /></td>
                    <td className="td">
                      {r.fallback
                        ? <span className="text-xs text-muted">{r.fallback}</span>
                        : <span className="text-[10px] text-muted italic">none</span>}
                    </td>
                    <td className="td text-right tabular-nums text-sm">{r.requests.toLocaleString()}</td>
                    <td className="td text-right tabular-nums text-sm">{fmtCost(r.cost)}</td>
                    <td className="td">
                      {r.redundancy === 'resilient' ? (
                        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2 py-0.5 rounded-full"
                              style={{ background: 'var(--color-accent-soft)', color: 'var(--color-ok)' }}
                              title="Has one or more fallback providers - survives a primary outage.">
                          <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} /> Resilient
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border border-warn/50 text-warn"
                              title="Single provider - no automatic failover configured.">
                          <span className="w-1.5 h-1.5 rounded-full bg-warn" /> Single
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td colSpan={7} className="td text-center text-muted py-8">
                    No routes match the search.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Optional graph visualization */}
      {showGraph && (
        <Card className="p-4">
          <SectionTitle>Routing visualization (Workspace → Alias → Provider)</SectionTitle>
          <div className="text-[11.5px] mb-2 flex flex-wrap items-center gap-x-4 gap-y-1" style={{ color: 'var(--color-text-secondary)' }}>
            <span className="inline-flex items-center gap-1.5">
              <svg width="22" height="8"><path d="M0 4 H22" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /></svg>
              Primary route - thickness = traffic volume
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg width="22" height="8"><path d="M0 4 H22" stroke="currentColor" strokeWidth="1.25" strokeDasharray="4 4" strokeLinecap="round" /></svg>
              Fallback route (failover only)
            </span>
            <span>Provider labels show traffic share %</span>
            <span className="inline-flex items-center gap-1 text-accent">
              <Eye size={11} /> hover a workspace or provider to spotlight its routes
            </span>
          </div>
          <RoutingGraph workspaces={workspaces} volume={volume} provReqs={provReqs}
                        totalReqs={totalReqs} providerFilter={filters.provider} />
        </Card>
      )}
    </div>
  )
}

function Th({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) {
  return (
    <th className="th cursor-pointer hover:text-gray-200 select-none" onClick={onClick}>
      <span className="inline-flex items-center gap-1">{children}</span>
    </th>
  )
}

// The SVG graph (demoted to optional) - interactive: hover a workspace or a
// provider to spotlight just its routes (edges + connected nodes brighten,
// everything else dims), so the diagram stays legible as the list grows.
function RoutingGraph({ workspaces, volume, provReqs, totalReqs, providerFilter }:
  { workspaces: any[]; volume: Record<string, any>; provReqs: Record<string, number>;
    totalReqs: number; providerFilter?: string }) {
  const [hover, setHover] = useState<{ kind: 'ws' | 'prov'; id: string } | null>(null)
  const providers = useMemo(() => {
    const set = new Set<string>()
    for (const w of workspaces) for (const t of Object.values(w.chat_models || {}).flat() as any[]) set.add(t.provider)
    const all = Array.from(set)
    // When a provider filter is active, draw only that provider's column.
    return providerFilter ? all.filter((p) => p === providerFilter) : all
  }, [workspaces, providerFilter])

  const maxVol = Math.max(1, ...Object.values(volume).map((v: any) => v.requests || 0))
  const W = 760, H = Math.max(300, workspaces.length * 80)
  const colX = [60, 360, 660]
  const wsY = (i: number) => 50 + i * (H - 80) / Math.max(1, workspaces.length - 1 || 1)
  const provY = (i: number) => 50 + i * (H - 80) / Math.max(1, providers.length - 1 || 1)

  // An edge/node is "lit" when nothing is hovered, or it belongs to the hovered
  // workspace / provider. Otherwise it dims to recede into the background.
  const edgeLit = (wsId: string, prov: string) =>
    !hover || (hover.kind === 'ws' ? hover.id === wsId : hover.id === prov)
  const wsLit = (wsId: string) => {
    if (!hover) return true
    if (hover.kind === 'ws') return hover.id === wsId
    const w = workspaces.find((x) => x.workspace_id === wsId)
    return !!w && (Object.values(w.chat_models || {}).flat() as any[]).some((t) => t.provider === hover.id)
  }
  const provLit = (prov: string) => {
    if (!hover) return true
    if (hover.kind === 'prov') return hover.id === prov
    const w = workspaces.find((x) => x.workspace_id === hover.id)
    return !!w && (Object.values(w.chat_models || {}).flat() as any[]).some((t) => t.provider === prov)
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[700px]" style={{ height: H }}
         onMouseLeave={() => setHover(null)}>
      {/* edges first (under nodes). Smooth cubic-bezier bands; primary = solid +
          traffic-weighted, fallback = thin dashed. Hover spotlights a subset. */}
      {workspaces.map((w: any, i: number) => {
        const y = wsY(i)
        const aliases = Object.entries(w.chat_models || {}) as [string, any[]][]
        const x1 = colX[0] + 200
        return aliases.map(([, targets], j) => targets.map((t: any, k: number) => {
          const pi = providers.indexOf(t.provider)
          if (pi < 0) return null
          const y2 = provY(pi)
          const dx = (colX[2] - x1) * 0.45
          const d = `M ${x1} ${y} C ${x1 + dx} ${y}, ${colX[2] - dx} ${y2}, ${colX[2]} ${y2}`
          const isPrimary = k === 0
          const share = (volume[w.workspace_id]?.requests || 0) / maxVol
          const lit = edgeLit(w.workspace_id, t.provider)
          const baseThick = isPrimary ? 1.5 + 4.5 * share : 1.25
          return (
            <path key={`${i}-${j}-${k}`} d={d} fill="none"
                  stroke={provColor(t.provider)} strokeWidth={lit ? baseThick + (hover ? 1 : 0) : baseThick}
                  opacity={lit ? (isPrimary ? 0.75 : 0.5) : 0.06}
                  strokeDasharray={isPrimary ? '' : '4 4'}
                  strokeLinecap="round" style={{ transition: 'opacity .15s, stroke-width .15s' }} />
          )
        }))
      })}
      {/* workspace nodes */}
      {workspaces.map((w: any, i: number) => {
        const y = wsY(i)
        const lit = wsLit(w.workspace_id)
        const active = hover?.kind === 'ws' && hover.id === w.workspace_id
        return (
          <g key={w.workspace_id} style={{ cursor: 'pointer', transition: 'opacity .15s' }}
             opacity={lit ? 1 : 0.25}
             onMouseEnter={() => setHover({ kind: 'ws', id: w.workspace_id })}>
            <rect x={colX[0]} y={y - 14} width={200} height={28} rx={8} fill="#1A1D27"
                  stroke={active ? 'var(--color-accent)' : '#2D3348'} strokeWidth={active ? 2 : 1} />
            <text x={colX[0] + 100} y={y + 4} fill={active ? '#fff' : '#E5E7EB'}
                  fontSize={active ? 12 : 11} fontWeight={active ? 700 : 400} textAnchor="middle">
              {(w.display_name || w.workspace_id).slice(0, 22)}
            </text>
          </g>
        )
      })}
      {providers.map((p, i) => {
        const y = provY(i)
        const pct = Math.round((provReqs[p] || 0) / totalReqs * 100)
        const lit = provLit(p)
        const active = hover?.kind === 'prov' && hover.id === p
        return (
          <g key={p} style={{ cursor: 'pointer', transition: 'opacity .15s' }}
             opacity={lit ? 1 : 0.25}
             onMouseEnter={() => setHover({ kind: 'prov', id: p })}>
            <rect x={colX[2]} y={y - 14} width={100} height={28} rx={8} fill="#14161D"
                  stroke={provColor(p)} strokeWidth={active ? 2.5 : 1} />
            <circle cx={colX[2] + 14} cy={y} r={4} fill={provColor(p)} />
            <text x={colX[2] + 44} y={y + 4} fill={provColor(p)}
                  fontSize={active ? 12 : 11} fontWeight={active ? 700 : 400} textAnchor="middle">{p}</text>
            <text x={colX[2] + 82} y={y + 4} fill="#9CA3AF" fontSize={9} textAnchor="middle">{pct}%</text>
          </g>
        )
      })}
    </svg>
  )
}
