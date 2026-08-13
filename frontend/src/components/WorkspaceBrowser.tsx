// WorkspaceBrowser - a hierarchical, searchable, favorite-able browser for MANY
// clients/workspaces. Groups workspaces under their client (collapsible), pins
// starred favorites to the top, and filters as you type. Used by the all-
// workspaces overviews (Providers, Keys) to make 45+ workspaces navigable.
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search, Star } from 'lucide-react'

export interface WsBrowserItem {
  id: string
  display: string
  clientId?: string | null
  right?: React.ReactNode          // per-row trailing content (provider badges / key count)
}

const FAV_KEY = 'agnos_ws_favorites'

function loadFavs(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(FAV_KEY) || '[]')) } catch { return new Set() }
}
function saveFavs(s: Set<string>) {
  try { localStorage.setItem(FAV_KEY, JSON.stringify([...s])) } catch { /* ignore */ }
}

export function WorkspaceBrowser({
  items,
  clientNames = {},
  onPick,
  testIdPrefix = 'wsb',
}: {
  items: WsBrowserItem[]
  clientNames?: Record<string, string>   // client_id → display name
  onPick: (id: string) => void
  testIdPrefix?: string
}) {
  const [q, setQ] = useState('')
  const [favs, setFavs] = useState<Set<string>>(() => loadFavs())
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  useEffect(() => { saveFavs(favs) }, [favs])

  const toggleFav = (id: string) => setFavs((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleGroup = (c: string) => setCollapsed((s) => {
    const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n
  })

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return items
    return items.filter((it) =>
      it.display.toLowerCase().includes(query) || it.id.toLowerCase().includes(query) ||
      (it.clientId || '').toLowerCase().includes(query) ||
      (clientNames[it.clientId || ''] || '').toLowerCase().includes(query))
  }, [items, q, clientNames])

  const favItems = filtered.filter((it) => favs.has(it.id))

  // group the rest by client
  const groups = useMemo(() => {
    const g: Record<string, WsBrowserItem[]> = {}
    for (const it of filtered) {
      const c = it.clientId || '-'
      ;(g[c] ||= []).push(it)
    }
    return Object.entries(g).sort((a, b) =>
      (clientNames[a[0]] || a[0]).localeCompare(clientNames[b[0]] || b[0]))
  }, [filtered, clientNames])

  const Row = ({ it }: { it: WsBrowserItem }) => (
    <div className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-elevated/40 transition-colors group"
         data-testid={`${testIdPrefix}-row-${it.id}`}>
      <button onClick={() => toggleFav(it.id)} aria-label="favorite"
              data-testid={`${testIdPrefix}-fav-${it.id}`}
              className={`shrink-0 ${favs.has(it.id) ? 'text-amber-400' : 'text-muted opacity-40 group-hover:opacity-100'} hover:text-amber-400`}>
        <Star size={13} fill={favs.has(it.id) ? 'currentColor' : 'none'} />
      </button>
      <button onClick={() => onPick(it.id)} className="flex-1 min-w-0 text-left flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-gray-200 truncate">{it.display}</div>
          <div className="text-[11px] text-muted mono truncate">{it.id}</div>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap justify-end max-w-[55%]">{it.right}</div>
      </button>
    </div>
  )

  return (
    <div data-testid={`${testIdPrefix}`}>
      <div className="flex items-center gap-1.5 bg-app border border-border rounded-xl px-3 py-1.5 mb-2 mx-1">
        <Search size={13} className="text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search clients & workspaces…"
               data-testid={`${testIdPrefix}-search`}
               className="bg-transparent outline-none text-sm flex-1 text-gray-200" />
        <span className="text-[10px] text-muted whitespace-nowrap">{filtered.length}/{items.length}</span>
      </div>

      {favItems.length > 0 && (
        <div className="mb-1">
          <div className="text-[10px] uppercase tracking-wider text-amber-400/80 px-3 py-1 flex items-center gap-1">
            <Star size={10} fill="currentColor" /> Favorites
          </div>
          <div className="divide-y divide-border/60">{favItems.map((it) => <Row key={'fav-' + it.id} it={it} />)}</div>
        </div>
      )}

      {groups.map(([clientId, rows]) => {
        const isCol = collapsed.has(clientId)
        return (
          <div key={clientId} className="mb-1">
            <button onClick={() => toggleGroup(clientId)} data-testid={`${testIdPrefix}-group-${clientId}`}
                    className="w-full flex items-center gap-1.5 px-2 py-1.5 text-[11px] font-semibold text-gray-300 hover:text-white">
              {isCol ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              <span className="uppercase tracking-wider">{clientNames[clientId] || clientId}</span>
              <span className="text-muted font-normal">· {rows.length}</span>
            </button>
            {!isCol && <div className="divide-y divide-border/60">{rows.map((it) => <Row key={it.id} it={it} />)}</div>}
          </div>
        )
      })}

      {filtered.length === 0 && (
        <div className="p-8 text-center text-muted text-sm">No clients or workspaces match “{q}”.</div>
      )}
    </div>
  )
}
