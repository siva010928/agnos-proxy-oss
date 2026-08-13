// Client → Workspace cascading picker (WAVE 25).
// Replaces the flat WorkspacePicker on Admin screens. Admin first picks a
// Client, then sees only that client's workspaces. Used on Providers, Keys,
// Routing, and anywhere that operates per-workspace.

import { useEffect, useMemo, useState } from 'react'
import { Building2, Boxes } from 'lucide-react'
import { api, useWorkspaces } from '../lib/api'



interface Client { client_id: string; name: string }

export function ClientWorkspacePicker({
  selectedClient,
  selectedWorkspace,
  onClientChange,
  onWorkspaceChange,
  allowAll = false,
}: {
  selectedClient: string | null
  selectedWorkspace: string | null
  onClientChange: (id: string | null) => void
  onWorkspaceChange: (id: string | null) => void
  // When true, offer an "All workspaces" option (value null) and DON'T force
  // auto-selecting the first workspace - so pages can show an all-workspaces view.
  allowAll?: boolean
}) {
  const [clients, setClients] = useState<Client[]>([])
  const ws = useWorkspaces()

  useEffect(() => {
    api('/admin/clients')
      .then((r: any) => setClients(r.clients || []))
      .catch(() => {})
  }, [])

  const workspaces = useMemo(() => {
    const all = ws.data?.workspaces || []
    if (!selectedClient) return all
    return all.filter((w: any) => w.client_id === selectedClient)
  }, [ws.data, selectedClient])

  // Auto-select first workspace when client changes - UNLESS allowAll, where the
  // neutral "All workspaces" (null) is a valid, first-class selection.
  useEffect(() => {
    if (allowAll) return
    if (workspaces.length > 0 && !workspaces.find((w: any) => w.workspace_id === selectedWorkspace)) {
      onWorkspaceChange(workspaces[0].workspace_id)
    }
  }, [workspaces, selectedWorkspace, allowAll])

  return (
    <div className="inline-flex items-center gap-2">
      <div className="inline-flex items-center gap-1.5 bg-elevated rounded-xl border border-border px-2 py-1.5">
        <Building2 size={13} className="text-muted" />
        <select
          className="bg-transparent text-sm text-gray-200 outline-none cursor-pointer min-w-[140px]"
          value={selectedClient || ''}
          onChange={(e) => { onClientChange(e.target.value || null); onWorkspaceChange(null) }}
          data-testid="client-picker"
        >
          <option value="">All clients</option>
          {clients.map((c) => (
            <option key={c.client_id} value={c.client_id}>
              {c.name || c.client_id}
            </option>
          ))}
        </select>
      </div>
      <div className="inline-flex items-center gap-1.5 bg-elevated rounded-xl border border-border px-2 py-1.5">
        <Boxes size={13} className="text-muted" />
        <select
          className="bg-transparent text-sm text-gray-200 outline-none cursor-pointer min-w-[180px]"
          value={selectedWorkspace || ''}
          onChange={(e) => onWorkspaceChange(e.target.value || null)}
          data-testid="ws-picker"
          disabled={ws.isLoading}
        >
          {allowAll && <option value="">{selectedClient ? 'All workspaces' : 'All workspaces (all clients)'}</option>}
          {!allowAll && workspaces.length === 0 && <option value="">No workspaces</option>}
          {workspaces.map((w: any) => (
            <option key={w.workspace_id} value={w.workspace_id}>
              {w.display_name || w.name || w.workspace_id}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
