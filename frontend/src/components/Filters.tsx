// Shared hierarchical / cascading filter bar used by Analytics, Request Logs and
// Routing. Each dropdown's options come from the parent-aware /facets endpoint
// (via useFacets), so:
//   • "All clients" ⇒ workspace shows every workspace of every client
//   • pick a client  ⇒ workspace narrows to that client's workspaces
//   • pick a workspace ⇒ component / use-case / provider / model / user narrow to
//     only values actually seen under that workspace
// Changing a parent clears its descendants so you can never hold an impossible
// combination. One component, one source of truth - no per-page duplication.
import type { AnalyticsFilters, Facets } from '../lib/api'

export type FilterField =
  | 'client' | 'workspace' | 'component' | 'provider'
  | 'model' | 'user' | 'status' | 'use_case' | 'event_kind'

// Which descendants to clear when a given field changes (hierarchy: client →
// workspace → {component, use_case, provider, model, user, status, event_kind}).
const CLEARS: Partial<Record<FilterField, FilterField[]>> = {
  client: ['workspace', 'component', 'use_case', 'provider', 'model', 'user', 'status', 'event_kind'],
  workspace: ['component', 'use_case', 'provider', 'model', 'user', 'status', 'event_kind'],
}

const LABELS: Record<FilterField, string> = {
  client: 'Client', workspace: 'Workspace', component: 'Component', provider: 'Provider',
  model: 'Model', user: 'User', status: 'Status', use_case: 'Use case', event_kind: 'Event',
}

function optionsFor(field: FilterField, f: Facets | undefined, value: AnalyticsFilters): { v: string; label: string }[] {
  if (!f) return []
  switch (field) {
    case 'client': return f.clients.map(c => ({ v: c.client_id, label: c.name || c.client_id }))
    case 'workspace': {
      // belt-and-suspenders: the backend already scopes by client, but also filter
      // client-side so a stale facet payload can never show a foreign workspace.
      const ws = value.client ? f.workspaces.filter(w => w.client_id === value.client) : f.workspaces
      return ws.map(w => ({ v: w.workspace_id, label: w.display_name || w.workspace_id }))
    }
    case 'component': return (f.components || []).map(x => ({ v: x, label: x }))
    case 'provider': return (f.providers || []).map(x => ({ v: x, label: x }))
    case 'model': return (f.models || []).map(x => ({ v: x, label: x }))
    case 'user': return (f.users || []).map(x => ({ v: x, label: x }))
    case 'status': return (f.statuses || []).map(x => ({ v: x, label: x }))
    case 'use_case': return (f.use_cases || []).map(x => ({ v: x, label: x }))
    case 'event_kind': return (f.event_kinds || []).map(x => ({ v: x, label: x }))
  }
}

function allLabel(field: FilterField, value: AnalyticsFilters): string {
  if (field === 'workspace') return value.client ? 'All workspaces' : 'All workspaces (all clients)'
  return `All ${LABELS[field].toLowerCase()}s`
}

export function HierarchicalFilters({
  value, onChange, facets, fields,
  includeSyntheticToggle = false, onClear,
}: {
  value: AnalyticsFilters
  onChange: (f: AnalyticsFilters) => void
  facets?: Facets
  fields: FilterField[]
  includeSyntheticToggle?: boolean
  onClear?: () => void
}) {
  function set(field: FilterField, v: string) {
    const next: AnalyticsFilters = { ...value, [field]: v || undefined }
    for (const child of (CLEARS[field] || [])) delete (next as any)[child]
    onChange(next)
  }

  const hasAny = fields.some(f => (value as any)[f]) || value.include_synthetic

  return (
    <div className="flex flex-wrap items-end gap-2">
      {fields.map(field => {
        const opts = optionsFor(field, facets, value)
        const disabled = !facets || (field === 'workspace' && !facets)
        return (
          <label key={field} className="text-[11px] text-muted">
            {LABELS[field]}
            <select
              value={(value as any)[field] || ''}
              disabled={disabled}
              onChange={e => set(field, e.target.value)}
              data-testid={`filter-${field}`}
              className="block mt-1 bg-app border border-border rounded-lg px-2 py-1.5 text-sm text-gray-200 min-w-[9rem] max-w-[16rem] disabled:opacity-50">
              <option value="">{allLabel(field, value)}</option>
              {opts.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
            </select>
          </label>
        )
      })}
      {includeSyntheticToggle && (
        <label className="text-[11px] text-muted inline-flex items-center gap-1.5 pb-1.5">
          <input type="checkbox" checked={!!value.include_synthetic}
                 onChange={e => onChange({ ...value, include_synthetic: e.target.checked || undefined })}
                 data-testid="filter-include-synthetic" />
          include synthetic
        </label>
      )}
      {hasAny && (
        <button onClick={() => (onClear ? onClear() : onChange({}))} data-testid="filter-clear"
                className="text-[11px] px-2 py-1.5 rounded-lg border border-border text-muted hover:bg-elevated">
          Clear filters
        </button>
      )}
    </div>
  )
}
