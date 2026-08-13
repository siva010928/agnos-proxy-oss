// Shared workspace picker - used by per-workspace screens (Components, Keys,
// Providers, Routing). Each screen owns the selected workspace state.

import { Boxes } from 'lucide-react'
import { useWorkspaces } from '../lib/api'

export function defaultWorkspace(workspaces: any[]): string | null {
  if (!workspaces.length) return null
  // No hidden filtering - just default to the first workspace in the list.
  return workspaces[0]?.workspace_id ?? null
}

export function WorkspacePicker({ value, onChange }:
  { value: string | null; onChange: (id: string) => void }) {
  const ws = useWorkspaces()
  const list: any[] = ws.data?.workspaces || []
  return (
    <div className="inline-flex items-center gap-2 bg-elevated rounded-xl border border-border px-2 py-1.5">
      <Boxes size={14} className="text-muted" />
      <select className="bg-transparent text-sm text-gray-200 outline-none cursor-pointer min-w-[220px]"
              data-testid="ws-picker" value={value || ''} onChange={(e) => onChange(e.target.value)}
              disabled={ws.isLoading}>
        {list.map((w) => (
          <option key={w.workspace_id} value={w.workspace_id}>
            {w.display_name || w.name || w.workspace_id} ({w.workspace_id})
          </option>
        ))}
      </select>
    </div>
  )
}
