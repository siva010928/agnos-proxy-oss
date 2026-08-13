import { useState, useMemo, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Boxes, ChevronDown, ChevronRight, ShieldCheck, Activity, AlertCircle, CheckCircle2, Pencil, X, Trash2, Plus } from 'lucide-react'
import { useWorkspaces, useCost, api } from '../lib/api'
import { Card, ProviderBadge, Skeleton, EmptyState } from '../components/ui'
import { fmtInt } from '../lib/format'
import { useCurrency } from '../lib/currency'
import { admin } from '../api/client'
import { withToast, toastOk, toastError } from '../components/Toast'
import {
  Modal, Field, GuardrailsBuilder, QuotaBudgetForm, ConfirmModal,
  type GuardrailsValue, type QuotaBudgetValue,
} from '../components/editors'

// workspace_id slug rule - mirrors the wizard + backend (lowercase, dashes).
const WS_SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/

interface ChatTarget { provider: string; model_id: string; weight?: number; context_window?: number }

function guardrailLabels(g: Record<string, any> = {}): string[] {
  const out: string[] = []
  if (g.secrets_detection) out.push('Secrets')
  if (g.pii_detection) out.push('PII')
  if (g.presidio) out.push('Presidio')
  if (g.keywords && (g.keywords as string[]).length) out.push(`Keywords(${(g.keywords as string[]).length})`)
  if (g.mode) out.push(`Mode: ${g.mode}`)
  return out
}

function healthFor(workspace: any, vol: any): { status: 'healthy' | 'warning' | 'critical' | 'idle'; reason: string } {
  const wsBudget = workspace.budgets?.workspace_usd || 0
  const spent = vol?.cost_usd || 0
  if (wsBudget > 0 && spent / wsBudget > 0.9) {
    return { status: 'critical', reason: `${Math.round((spent/wsBudget)*100)}% of $${wsBudget} workspace budget used` }
  }
  if (wsBudget > 0 && spent / wsBudget > 0.75) {
    return { status: 'warning', reason: `${Math.round((spent/wsBudget)*100)}% of $${wsBudget} budget used` }
  }
  if (!vol || vol.requests === 0) {
    return { status: 'idle', reason: 'No traffic in last 45 days' }
  }
  return { status: 'healthy', reason: 'Operating normally' }
}

const HEALTH_COLORS = {
  healthy: { dot: 'bg-green-400', text: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30' },
  warning: { dot: 'bg-orange-400', text: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/30' },
  critical: { dot: 'bg-red-400', text: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30' },
  idle: { dot: 'bg-muted', text: 'text-muted', bg: 'bg-elevated', border: 'border-border' },
}

export function Workspaces() {
  const ws = useWorkspaces()
  const nav = useNavigate()
  // Explicit 45-day window so the "Cost (45d)" column + budget-health logic
  // actually reflect a 45-day spend (not the server's default/all-time window).
  const since45d = useMemo(() => {
    const d = new Date(Date.now() - 45 * 24 * 3600 * 1000)
    return d.toISOString()
  }, [])
  const cost = useCost('workspace', { from: since45d })
  const { format: fmtCost } = useCurrency()
  const [openId, setOpenId] = useState<string | null>(null)
  const [editWs, setEditWs] = useState<any | null>(null)
  const [delWs, setDelWs] = useState<any | null>(null)
  const [creating, setCreating] = useState(false)
  const [clientFilter, setClientFilter] = useState('')
  // Guardrail RULES (incl. global-scope) + profiles, so each workspace can show
  // every guardrail that ACTUALLY applies to it - not just its inline detectors.
  // A global rule applies to all workspaces but isn't part of a workspace's own
  // config, which previously made the page look like "no guardrail" while the
  // playground enforced one. This reconciles that.
  const [rules, setRules] = useState<any[]>([])
  const [profilesById, setProfilesById] = useState<Record<number, any>>({})
  useEffect(() => {
    import('../api/client').then(({ admin }) => {
      admin.listRules().then((r: any) => setRules(r.rules || [])).catch(() => {})
      admin.listProfiles?.().then((r: any) => {
        const m: Record<number, any> = {}
        for (const p of (r.profiles || [])) m[p.id] = p
        setProfilesById(m)
      }).catch(() => {})
    })
  }, [])
  const applicableRules = (wsId: string) =>
    rules.filter((r) => r.enabled && (r.scope === 'global' || r.workspace_id === wsId))
  // Effective enforcement = the workspace mode (governor/ceiling) capped against
  // the rule's own action. An audit-only workspace downgrades every rule to audit.
  const SEV: Record<string, number> = { audit: 0, redact: 1, block: 2 }
  const effectiveAction = (ruleAction: string, mode?: string) => {
    if (!mode || !(mode in SEV)) return ruleAction
    return (SEV[ruleAction] ?? 2) <= SEV[mode] ? ruleAction : mode
  }

  const vol: Record<string, any> = {}
  for (const r of cost.data?.rows || []) vol[r.key] = r
  const allWs = (ws.data?.workspaces || [])
  const clientIds = Array.from(new Set((ws.data?.workspaces || []).map((w: any) => w.client_id).filter(Boolean))) as string[]
  const all = clientFilter ? allWs.filter((w: any) => w.client_id === clientFilter) : allWs

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Workspaces</h1>
          <p className="text-muted text-sm">Each workspace is a team's gateway slice - its own routing, guardrails, limits, budget, and API keys.</p>
        </div>
        <div className="flex items-center gap-2">
          {clientIds.length > 0 && (
            <label className="inline-flex items-center gap-2 bg-elevated rounded-xl border border-border px-2.5 py-1.5">
              <span className="text-[11px] text-muted">Client</span>
              <select className="bg-transparent text-sm text-gray-200 outline-none cursor-pointer"
                      value={clientFilter} onChange={(e) => setClientFilter(e.target.value)} data-testid="ws-client-filter">
                <option value="">All clients</option>
                {clientIds.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          )}
          <button onClick={() => setCreating(true)} data-testid="ws-new"
            className="btn-primary inline-flex items-center gap-1.5">
            <Plus size={16} /> New workspace
          </button>
        </div>
      </div>
      {ws.isLoading ? (
        <div className="grid grid-cols-3 gap-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={210} />)}</div>
      ) : all.length === 0 ? (
        <EmptyState icon={<Boxes size={32} />} title="No workspaces"
          hint="Create one to start - you'll be guided through providers and routing."
          cta={<button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setCreating(true)} data-testid="ws-new-empty"><Plus size={14} /> New workspace</button>} />
      ) : (
        <div className="space-y-3">
          {all.map((w: any) => {
            const v = vol[w.workspace_id] || {}
            const open = openId === w.workspace_id
            const aliases: [string, ChatTarget[]][] = Object.entries(w.chat_models || {}) as any
            const allTargets: ChatTarget[] = aliases.flatMap(([, ts]) => ts as ChatTarget[])
            const providers = Array.from(new Set(allTargets.map(t => t.provider)))
            const guardrailTags = guardrailLabels(w.guardrails)
            const health = healthFor(w, v)
            const hc = HEALTH_COLORS[health.status]
            const blocked = v.guardrail_blocks || 0
            return (
              <Card key={w.workspace_id} className={`p-0 overflow-hidden border ${hc.border}`}>
                <button onClick={() => setOpenId(open ? null : w.workspace_id)}
                  className="w-full text-left grid grid-cols-12 gap-4 p-4 hover:bg-elevated/40 transition">
                  <div className="col-span-12 md:col-span-3">
                    <div className="flex items-center gap-2">
                      {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
                      <span className="font-semibold text-gray-100 truncate">{w.display_name || w.workspace_id}</span>
                    </div>
                    <div className="mt-1 ml-5 mono text-[10px] text-muted truncate">{w.workspace_id}</div>
                    <div className={`mt-2 ml-5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] ${hc.bg} ${hc.text}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${hc.dot}`} />
                      <span className="capitalize font-medium">{health.status}</span>
                      <span className="opacity-70">· {health.reason}</span>
                    </div>
                  </div>

                  <div className="col-span-6 md:col-span-3">
                    <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Routing</div>
                    {aliases.length === 0 ? (
                      <div className="text-[11px] text-muted italic">no aliases</div>
                    ) : aliases.slice(0, 2).map(([alias, ts]) => (
                      <div key={alias} className="flex items-center gap-1 text-[11px] mb-0.5 truncate">
                        <span className="mono text-gray-300 truncate max-w-[110px]">{alias}</span>
                        <span className="text-muted">→</span>
                        <div className="flex items-center gap-0.5 flex-wrap">
                          {(ts as ChatTarget[]).map((t, i) => (
                            <ProviderBadge key={i} provider={t.provider} />
                          ))}
                        </div>
                      </div>
                    ))}
                    {aliases.length > 2 && <div className="text-[10px] text-muted">+{aliases.length - 2} more aliases</div>}
                  </div>

                  <div className="col-span-6 md:col-span-3">
                    <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Guardrails</div>
                    {guardrailTags.length === 0 ? (
                      <div className="text-[11px] text-muted italic">none configured</div>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {guardrailTags.map(g => (
                          <span key={g} className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-300 border border-violet-500/20">
                            {g}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="col-span-12 md:col-span-3 grid grid-cols-3 gap-2 text-center">
                    <Stat label="Requests" value={fmtInt(v.requests || 0)} icon={<Activity size={11} />} />
                    <Stat label="Cost (45d)" value={fmtCost(v.cost_usd || 0)} icon={null} accent="warn" />
                    <Stat label="Blocked" value={fmtInt(blocked)} icon={<ShieldCheck size={11} />} accent={blocked > 0 ? 'red' : undefined} />
                  </div>
                </button>

                {open && (
                  <div className="border-t border-border bg-app/40 p-4 space-y-4">
                    {/* Applicable guardrail rules (inline detectors + global/scoped rules) */}
                    <div>
                      <div className="text-[10.5px] uppercase tracking-wider font-semibold text-gray-200 mb-2">
                        Guardrails actually enforced on this workspace
                      </div>
                      <div className="bg-surface rounded border border-border p-3 space-y-2">
                        <div>
                          <div className="text-[10px] text-muted mb-1">Inline detectors (this workspace's own config)</div>
                          {guardrailTags.length === 0
                            ? <span className="text-[11px] text-muted italic">none</span>
                            : <div className="flex flex-wrap gap-1">{guardrailTags.map(g => (
                                <span key={g} className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-300 border border-violet-500/20">{g}</span>
                              ))}</div>}
                        </div>
                        <div className="border-t border-border/60 pt-2">
                          <div className="text-[10px] text-muted mb-1">
                            Rules enforced here - <span className="text-gray-300">🌐 global (all workspaces) + this workspace's own</span>. Effective action is capped by the workspace mode{w.guardrails?.mode ? <> (<span className="mono">{w.guardrails.mode}</span>)</> : ''}.
                          </div>
                          {applicableRules(w.workspace_id).length === 0 ? (
                            <span className="text-[11px] text-muted italic">none</span>
                          ) : (
                            <div className="flex flex-wrap gap-1.5">
                              {applicableRules(w.workspace_id).map((r) => {
                                const eff = effectiveAction(r.action, w.guardrails?.mode)
                                const downgraded = eff !== r.action
                                return (
                                <span key={r.id} className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border"
                                      style={{ background: 'var(--color-accent-soft)', borderColor: 'var(--color-accent)', color: 'var(--color-accent)' }}
                                      title={`rule action: ${r.action} · effective: ${eff}${downgraded ? ` (capped by workspace mode "${w.guardrails?.mode}")` : ''} · ${r.apply_to} · profiles: ${(r.profile_ids||[]).map((id:number)=>profilesById[id]?.name||('#'+id)).join(', ') || 'none'}`}>
                                  {r.scope === 'global' ? '🌐 ' : ''}{r.name}
                                  <span className="opacity-70">
                                    · {downgraded ? <span className="text-warn">{r.action}→{eff}</span> : eff}
                                  </span>
                                  {(r.profile_ids||[]).map((id:number)=>profilesById[id]).filter(Boolean).map((p:any)=>(
                                    <span key={p.id} className="opacity-70">· {p.detector_type}</span>
                                  ))}
                                </span>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Routing table */}
                    <div>
                      <div className="text-[10.5px] uppercase tracking-wider font-semibold text-gray-200 mb-2">Routing - alias → provider/model · weight</div>
                      <div className="bg-surface rounded border border-border overflow-hidden">
                        <table className="w-full text-[11.5px]">
                          <thead className="bg-elevated/50 text-[10px] uppercase tracking-wider text-muted">
                            <tr>
                              <th className="text-left px-3 py-1.5 font-normal">Alias</th>
                              <th className="text-left px-3 py-1.5 font-normal">Provider</th>
                              <th className="text-left px-3 py-1.5 font-normal">Model</th>
                              <th className="text-right px-3 py-1.5 font-normal">Weight</th>
                              <th className="text-right px-3 py-1.5 font-normal">Context</th>
                            </tr>
                          </thead>
                          <tbody>
                            {aliases.length === 0 ? (
                              <tr><td colSpan={5} className="px-3 py-3 text-muted text-center">No routing aliases configured.</td></tr>
                            ) : aliases.flatMap(([alias, ts]) =>
                              (ts as ChatTarget[]).map((t, i) => (
                                <tr key={`${alias}-${i}`} className="border-t border-border/30">
                                  <td className="px-3 py-1.5 mono text-gray-200">{i === 0 ? alias : ''}</td>
                                  <td className="px-3 py-1.5"><ProviderBadge provider={t.provider} /></td>
                                  <td className="px-3 py-1.5 mono text-gray-300 text-[10.5px]">{t.model_id}</td>
                                  <td className="px-3 py-1.5 text-right text-muted">{t.weight ?? 1}</td>
                                  <td className="px-3 py-1.5 text-right text-muted">{t.context_window ? `${(t.context_window/1000).toFixed(0)}k` : '-'}</td>
                                </tr>
                              ))
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {/* Limits table */}
                      <div>
                        <div className="text-[10.5px] uppercase tracking-wider font-semibold text-gray-200 mb-2">Rate limits</div>
                        <div className="bg-surface rounded border border-border overflow-hidden">
                          <table className="w-full text-[11.5px]">
                            <thead className="bg-elevated/50 text-[10px] uppercase tracking-wider text-muted">
                              <tr>
                                <th className="text-left px-3 py-1.5 font-normal">Scope</th>
                                <th className="text-right px-3 py-1.5 font-normal">RPM</th>
                                <th className="text-right px-3 py-1.5 font-normal">TPM</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr className="border-t border-border/30">
                                <td className="px-3 py-1.5 text-gray-200">Workspace</td>
                                <td className="px-3 py-1.5 text-right text-gray-300 mono">{w.rate_limits?.rpm ? fmtInt(w.rate_limits.rpm) : '-'}</td>
                                <td className="px-3 py-1.5 text-right text-gray-300 mono">{w.rate_limits?.tpm ? fmtInt(w.rate_limits.tpm) : '-'}</td>
                              </tr>
                              {Object.entries(w.quotas || {}).map(([alias, q]: any) => (
                                <tr key={alias} className="border-t border-border/30">
                                  <td className="px-3 py-1.5 text-muted mono text-[10.5px]">{alias}</td>
                                  <td className="px-3 py-1.5 text-right text-gray-300 mono">{q?.rpm ? fmtInt(q.rpm) : '-'}</td>
                                  <td className="px-3 py-1.5 text-right text-gray-300 mono">{q?.tpm ? fmtInt(q.tpm) : '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* Budget table */}
                      <div>
                        <div className="text-[10.5px] uppercase tracking-wider font-semibold text-gray-200 mb-2">Monthly budgets</div>
                        <div className="bg-surface rounded border border-border overflow-hidden">
                          <table className="w-full text-[11.5px]">
                            <thead className="bg-elevated/50 text-[10px] uppercase tracking-wider text-muted">
                              <tr>
                                <th className="text-left px-3 py-1.5 font-normal">Scope</th>
                                <th className="text-right px-3 py-1.5 font-normal">Limit (USD)</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr className="border-t border-border/30">
                                <td className="px-3 py-1.5 text-gray-200">Workspace</td>
                                <td className="px-3 py-1.5 text-right text-gray-300 mono">{w.budgets?.workspace_usd ? `$${w.budgets.workspace_usd.toLocaleString()}` : '-'}</td>
                              </tr>
                              <tr className="border-t border-border/30">
                                <td className="px-3 py-1.5 text-gray-200">Per user</td>
                                <td className="px-3 py-1.5 text-right text-gray-300 mono">{w.budgets?.user_usd ? `$${w.budgets.user_usd.toLocaleString()}` : '-'}</td>
                              </tr>
                              {Object.entries(w.budgets?.per_model || {}).map(([m, c]: any) => (
                                <tr key={m} className="border-t border-border/30">
                                  <td className="px-3 py-1.5 text-muted mono text-[10.5px]">{m}</td>
                                  <td className="px-3 py-1.5 text-right text-gray-300 mono">${c}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </div>

                    {/* Identity + Actions */}
                    <div className="flex items-center justify-between pt-2 border-t border-border/40">
                      <div className="text-[10px] text-muted">
                        Client: <span className="text-gray-300 mono">{w.client_id || '-'}</span> ·
                        Engine routing: <span className="text-gray-300 mono">gateway-wide</span> <span className="text-muted/70">(set on Routing)</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button onClick={() => setEditWs(w)}
                          data-testid={`ws-edit-${w.workspace_id}`}
                          className="text-[11px] px-2 py-1 rounded bg-surface border border-border hover:border-accent/50 text-gray-300 hover:text-white transition flex items-center gap-1">
                          <Pencil size={10} /> Edit workspace
                        </button>
                        <Link to={`/admin/routing?workspace=${w.workspace_id}`}
                          className="text-[11px] px-2 py-1 rounded bg-surface border border-border hover:border-accent/50 text-gray-300 hover:text-white transition flex items-center gap-1">
                          <Pencil size={10} /> Edit routing
                        </Link>
                        <Link to={`/admin/keys?workspace=${w.workspace_id}`}
                          className="text-[11px] px-2 py-1 rounded bg-surface border border-border hover:border-accent/50 text-gray-300 hover:text-white transition">
                          Manage keys
                        </Link>
                        <Link to={`/logs?workspace=${w.workspace_id}`}
                          className="text-[11px] px-2 py-1 rounded bg-accent/10 border border-accent/30 text-accent hover:bg-accent/20 transition">
                          View request logs →
                        </Link>
                        <button onClick={() => setDelWs(w)}
                          data-testid={`ws-delete-${w.workspace_id}`}
                          className="text-[11px] px-2 py-1 rounded border border-danger/40 text-danger hover:bg-danger/10 transition flex items-center gap-1 ml-auto">
                          <Trash2 size={10} /> Delete
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
      {editWs && (
        <EditWorkspaceModal
          ws={editWs}
          clientIds={clientIds}
          onClose={() => setEditWs(null)}
          onSaved={() => { setEditWs(null); ws.refetch() }}
        />
      )}

      {creating && (
        <CreateWorkspaceModal
          onClose={() => setCreating(false)}
          onCreated={async (id) => {
            // Guided onboarding: a fresh workspace has no providers yet, so jump
            // straight to Providers config (step 2), carrying onboarding=1 so the
            // next screens show the "next step" trail (Providers -> Routing).
            // Await the refetch FIRST so the new workspace is in the shared query
            // cache before Providers mounts - otherwise its workspace picker can't
            // find it and falls back to auto-selecting a different workspace.
            setCreating(false)
            await ws.refetch()
            nav(`/admin/providers?workspace=${encodeURIComponent(id)}&onboarding=1`)
          }}
        />
      )}

      <ConfirmModal
        open={!!delWs}
        onCancel={() => setDelWs(null)}
        title={`Delete workspace '${delWs?.workspace_id}'?`}
        message={
          <>
            <span className="text-warn font-medium">Permanently deletes this workspace and everything inside it</span> -
            provider credentials, API keys, components, routing aliases, and guardrail rules/profiles scoped to it
            (plus its Bifrost managed keys).
            {delWs && Object.keys(delWs.chat_models || {}).length > 0 && (
              <div className="mt-2 text-[11.5px] text-muted">
                Aliases removed: {Object.keys(delWs.chat_models).map((a) => <span key={a} className="mono text-gray-300">{a} </span>)}
              </div>
            )}
            <div className="mt-2 text-[11px]">Request logs &amp; audit history are retained. This cannot be undone.</div>
          </>
        }
        identifier={delWs ? `workspace '${delWs.workspace_id}'` : null}
        confirmLabel="Delete workspace"
        danger
        onConfirm={async () => {
          if (!delWs) return
          await withToast(async () => {
            await admin.deleteWorkspace(delWs.workspace_id)
            await ws.refetch()
          }, 'Workspace deleted')
          setDelWs(null)
        }}
        testId="ws-confirm-delete"
      />
    </div>
  )
}

// Full workspace editor - everything except the immutable workspace_id.
function EditWorkspaceModal({ ws, clientIds, onClose, onSaved }:
  { ws: any; clientIds: string[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState<string>(ws.display_name || ws.name || '')
  const [clientId, setClientId] = useState<string>(ws.client_id || '')
  const [defaultAlias, setDefaultAlias] = useState<string>(ws.default_chat_alias || '')
  const [guardrails, setGuardrails] = useState<GuardrailsValue>({
    pii_detection: !!ws.guardrails?.pii_detection,
    secrets_detection: !!ws.guardrails?.secrets_detection,
    auto_truncate: !!ws.guardrails?.auto_truncate,
    mode: ws.guardrails?.mode || 'block',
    rule_ids: ws.guardrails?.rule_ids || [],
  })
  const [limits, setLimits] = useState<QuotaBudgetValue>({
    rpm: ws.rate_limits?.rpm ?? null,
    tpm: ws.rate_limits?.tpm ?? null,
    workspace_usd: ws.budgets?.workspace_usd ?? null,
    user_usd: ws.budgets?.user_usd ?? null,
  })
  const [busy, setBusy] = useState(false)
  const aliases = Object.keys(ws.chat_models || {})
  const [guardrailsEnabled, setGuardrailsEnabled] = useState(
    Object.keys(ws.guardrails || {}).length > 0
  )

  const save = async () => {
    setBusy(true)
    try {
      await withToast(() => admin.updateWorkspace(ws.workspace_id, {
        name,
        client_id: clientId || null,
        default_chat_alias: defaultAlias || null,
        guardrails: guardrailsEnabled ? { ...(ws.guardrails || {}), ...guardrails } : {},
        rate_limits: { rpm: limits.rpm ?? null, tpm: limits.tpm ?? null },
        budgets: { workspace_usd: limits.workspace_usd ?? null, user_usd: limits.user_usd ?? null },
      }), 'Workspace updated')
      onSaved()
    } finally { setBusy(false) }
  }

  return (
    <Modal open onClose={onClose} title={`Edit workspace · ${ws.workspace_id}`} size="lg" testId="ws-edit-modal"
      footer={
        <>
          <button className="btn-ghost text-sm" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary text-sm disabled:opacity-50" onClick={save} disabled={busy} data-testid="ws-edit-save">
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </>
      }>
      <div className="space-y-4">
        <div className="text-[11px] text-muted">
          <span className="mono text-gray-300">workspace_id</span> is immutable. Everything else can be changed here.
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Display name">
            <input className="input text-sm" value={name} onChange={(e) => setName(e.target.value)} data-testid="ws-edit-name" />
          </Field>
          <Field label="Client" hint="Parent tenant for hierarchical budgets/attribution.">
            <select className="input text-sm" value={clientId} onChange={(e) => setClientId(e.target.value)} data-testid="ws-edit-client">
              <option value="">- none -</option>
              {clientIds.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Default chat alias" hint='Resolved when a request omits the model or sends "default".'>
            {aliases.length === 0 ? (
              <div className="rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[11.5px]">
                <div className="text-warn font-medium">No routing aliases yet.</div>
                <div className="text-muted mt-0.5">
                  Until you add one, requests that send <span className="mono">model="default"</span> will fail.{' '}
                  <Link to={`/admin/routing?workspace=${ws.workspace_id}`} className="text-accent hover:underline">
                    Create an alias in Routing →
                  </Link>
                </div>
              </div>
            ) : (
              <select className="input text-sm" value={defaultAlias} onChange={(e) => setDefaultAlias(e.target.value)} data-testid="ws-edit-default-alias">
                <option value="">- none -</option>
                {aliases.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            )}
          </Field>
        </div>
        <div className="border-t border-border pt-3">
          <label className="flex items-center gap-2 mb-2 cursor-pointer">
            <input type="checkbox" className="accent-accent" checked={guardrailsEnabled}
              onChange={(e) => setGuardrailsEnabled(e.target.checked)} data-testid="ws-edit-guardrails-enabled" />
            <span className="text-[11px] uppercase tracking-wider font-semibold text-gray-200">Guardrails</span>
            <span className="text-[11px] text-muted">- optional; uncheck for a governance-only workspace</span>
          </label>
          {guardrailsEnabled
            ? <GuardrailsBuilder value={guardrails} onChange={setGuardrails} testIdPrefix="ws-edit-guardrails" workspaceId={ws.workspace_id} />
            : <div className="text-[11.5px] text-muted">No guardrails - requests pass through with governance only.</div>}
        </div>
        <div className="border-t border-border pt-3">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-2">Rate limits & budgets</div>
          <QuotaBudgetForm value={limits} onChange={setLimits} />
        </div>
      </div>
    </Modal>
  )
}

function Stat({ label, value, icon, accent }: { label: string; value: string; icon: React.ReactNode; accent?: 'warn' | 'red' }) {
  const color = accent === 'warn' ? 'text-warn' : accent === 'red' ? 'text-red-400' : 'text-gray-100'
  return (
    <div>
      <div className={`text-base font-semibold tabular-nums ${color}`}>{value}</div>
      <div className="text-[10px] text-muted flex items-center justify-center gap-1">{icon}{label}</div>
    </div>
  )
}

// Step 1 of the guided onboarding: create the workspace skeleton (id + client +
// name). No aliases/providers yet - the caller redirects to Providers next.
function CreateWorkspaceModal({ onClose, onCreated }:
  { onClose: () => void; onCreated: (workspaceId: string) => void }) {
  const [clients, setClients] = useState<{ client_id: string; name?: string }[]>([])
  const [wsId, setWsId] = useState('')
  const [name, setName] = useState('')
  const [clientId, setClientId] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/admin/clients').then((r: any) => {
      const cs = r.clients || []
      setClients(cs)
      if (cs.length === 1) setClientId(cs[0].client_id)
    }).catch(() => {})
  }, [])

  const slug = wsId.trim().toLowerCase()
  const slugValid = WS_SLUG_RE.test(slug)
  const valid = slugValid && !!clientId

  async function create() {
    if (!valid) return
    setBusy(true)
    try {
      await admin.createWorkspace({
        workspace_id: slug, client_id: clientId, name: name.trim() || slug,
      } as any)
      toastOk(`Workspace '${slug}' created - now add a provider`)
      onCreated(slug)
    } catch (e: any) {
      toastError(e?.message || 'create failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open onClose={onClose} title="New workspace · step 1 of 3" size="md" testId="ws-create-modal"
      footer={
        <>
          <button className="btn-ghost text-sm" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary text-sm disabled:opacity-50" onClick={create}
            disabled={busy || !valid} data-testid="ws-create-submit">
            {busy ? 'Creating…' : 'Create & add providers →'}
          </button>
        </>
      }>
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-[11px]">
          {['Workspace', 'Providers', 'Routing'].map((s, i) => (
            <span key={s} className="inline-flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded-full ${i === 0 ? 'bg-accent/15 text-accent border border-accent/40' : 'bg-elevated text-muted border border-border'}`}>{i + 1}. {s}</span>
              {i < 2 && <span className="text-muted">→</span>}
            </span>
          ))}
        </div>
        <div className="text-[11.5px] text-muted">
          We'll create the workspace, then take you to <span className="text-gray-200">Providers</span> to add credentials,
          then to <span className="text-gray-200">Routing</span> to define an alias. A sensible default alias is set for you.
        </div>
        <Field label="Workspace ID" hint="Lowercase slug, immutable (e.g. eshop-checkout). Used in attribution + keys." required>
          <input className="input mono text-sm" value={wsId} onChange={(e) => setWsId(e.target.value)}
            placeholder="eshop-checkout" data-testid="ws-create-id" autoFocus />
          {wsId && !slugValid && (
            <div className="text-[11px] text-danger mt-1">Use lowercase letters, digits and dashes (2-64 chars).</div>
          )}
        </Field>
        <Field label="Display name" hint="Human-friendly label shown in the dashboard.">
          <input className="input text-sm" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="eShop Checkout" data-testid="ws-create-name" />
        </Field>
        <Field label="Client" hint="Parent tenant for hierarchical budgets + attribution." required>
          <select className="input text-sm" value={clientId} onChange={(e) => setClientId(e.target.value)} data-testid="ws-create-client">
            <option value="">- select a client -</option>
            {clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.name || c.client_id}</option>)}
          </select>
          {clients.length === 0 && (
            <div className="text-[11px] text-warn mt-1">
              No clients yet. Create one under <Link to="/admin/clients" className="text-accent hover:underline">Admin → Clients</Link> first.
            </div>
          )}
        </Field>
      </div>
    </Modal>
  )
}
