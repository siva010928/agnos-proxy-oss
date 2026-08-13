import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import {
  LayoutDashboard, Activity, Network, DollarSign, ShieldCheck,
  Boxes, Settings, LineChart, Server, FileSearch, TrendingUp, Play,
  Users, Key, Layers, LogOut, BookOpen,
} from 'lucide-react'
import { useProviders } from '../lib/api'
import { api } from '../lib/api'
import { LiveDot } from './ui'
import { CurrencyPicker } from '../lib/currency'
import { ThemeToggle } from '../lib/theme'

// Structured navigation - grouped by persona concern.
interface NavSection { label: string; items: NavItem[] }
interface NavItem { to: string; label: string; icon: any; end?: boolean; badge?: string }

const NAV: NavSection[] = [
  {
    label: 'Overview',
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
      { to: '/playground', label: 'Playground', icon: Play },
      { to: '/docs', label: 'Developer Docs', icon: BookOpen },
    ],
  },
  {
    label: 'Dashboard',
    items: [
      { to: '/live', label: 'Live Traffic', icon: Activity },
      { to: '/cost', label: 'Cost Analytics', icon: DollarSign },
      { to: '/logs', label: 'Request Logs', icon: FileSearch },
      { to: '/value', label: 'Value & Savings', icon: TrendingUp },
    ],
  },
  {
    label: 'Configuration',
    items: [
      { to: '/workspaces', label: 'Workspaces', icon: Boxes },
      { to: '/routing', label: 'Routing Map', icon: Network },
      { to: '/guardrails/rules', label: 'Guardrail Rules', icon: ShieldCheck },
      { to: '/guardrails/providers', label: 'Guardrail Detectors', icon: ShieldCheck },
    ],
  },
  {
    label: 'Administration',
    items: [
      { to: '/admin', label: 'Onboarding', icon: Layers, end: true },
      { to: '/admin/clients', label: 'Clients', icon: Users },
      { to: '/admin/providers', label: 'Providers', icon: Server },
      { to: '/admin/routing', label: 'Routing Config', icon: Network },
      { to: '/admin/keys', label: 'API Keys', icon: Key },
      { to: '/admin/pricing', label: 'Pricing', icon: DollarSign },
    ],
  },
  {
    label: 'Platform',
    items: [
      { to: '/observability', label: 'Observability', icon: LineChart },
      { to: '/engine', label: 'Engine & Health', icon: Server },
    ],
  },
]

export function Shell({ connected }: { connected: boolean }) {  const { data: providers } = useProviders()
  const engine = providers?.engine || '...'
  const location = useLocation()
  const COST_PATHS = ['/', '/cost', '/value', '/logs', '/live', '/admin/pricing']
  const showCurrencyPicker = COST_PATHS.some(p =>
    p === '/' ? location.pathname === '/' : location.pathname.startsWith(p)
  )

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--color-app)' }}>
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r flex flex-col" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}>
        <div className="px-4 py-5 flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center text-white font-bold text-sm"
               style={{ background: 'var(--color-accent)' }}>A</div>
          <div className="leading-tight">
            <div className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>Agnos Proxy</div>
            <div className="text-[10px]" style={{ color: 'var(--color-muted)' }}>LLM Gateway</div>
          </div>
        </div>
        <nav className="px-2 flex-1 space-y-4 overflow-y-auto pb-4">
          {NAV.map((section) => (
            <div key={section.label}>
              <div className="px-3 py-1 text-[10px] uppercase tracking-widest font-semibold" style={{ color: 'var(--color-muted)' }}>
                {section.label}
              </div>
              <div className="space-y-0.5 mt-1">
                {section.items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.end as boolean}
                    className={({ isActive }) => `navlink ${isActive ? 'navlink-active' : ''}`}>
                    <item.icon size={15} strokeWidth={1.8} />
                    <span className="flex-1">{item.label}</span>
                    {item.badge && (
                      <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full uppercase tracking-wider"
                            style={{ background: 'var(--color-accent)', color: '#fff' }}>
                        {item.badge}
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="px-4 py-3 text-[10px]" style={{ color: 'var(--color-muted)', borderTop: '1px solid var(--color-border)' }}>
          engine: <span className="font-mono" style={{ color: 'var(--color-text)' }}>{engine}</span>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 shrink-0 flex items-center justify-end px-6 gap-4"
                style={{ borderBottom: '1px solid var(--color-border)' }}>
          {showCurrencyPicker && <CurrencyPicker />}
          <ThemeToggle />
          <LiveDot on={connected} />
          <UserMenu />
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

// Account menu - shows the signed-in user and a Logout action.
function UserMenu() {
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [user, setUser] = useState<string>('admin')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api('/auth/me').then((r: any) => { if (r?.user) setUser(r.user) }).catch(() => {})
  }, [])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const logout = async () => {
    try { await api('/auth/logout', { method: 'POST' }) } catch { /* ignore */ }
    nav('/login')
  }

  const initials = user.slice(0, 2).toUpperCase()

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        data-testid="user-menu-trigger"
        title={`Signed in as ${user}`}
        className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-medium transition-colors hover:opacity-80"
        style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
      >
        {initials}
      </button>
      {open && (
        <div
          data-testid="user-menu"
          className="absolute right-0 mt-2 w-48 rounded-lg shadow-lg py-1 z-50"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <div className="px-3 py-2 border-b" style={{ borderColor: 'var(--color-border)' }}>
            <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--color-muted)' }}>Signed in as</div>
            <div className="text-sm font-medium truncate" style={{ color: 'var(--color-text)' }}>{user}</div>
          </div>
          <button
            onClick={logout}
            data-testid="logout-button"
            className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-elevated transition-colors text-left"
            style={{ color: 'var(--color-danger)' }}
          >
            <LogOut size={14} /> Log out
          </button>
        </div>
      )}
    </div>
  )
}
