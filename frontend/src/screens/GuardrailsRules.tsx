// Guardrails → Configuration (Rules) page.
// List + create/edit modal with visual builder + live CEL preview + Test panel.

import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Check, ClipboardCheck, FlaskConical, Globe, Loader2, Pencil, Plus, Power,
  Search, ShieldCheck, Target, Trash2, X,
} from 'lucide-react'
import { admin } from '../api/client'
import { useWorkspaces } from '../lib/api'
import type { GuardrailTestResult } from '../api/types'
import { toastError, toastOk, withToast } from '../components/Toast'
import { ClientWorkspacePicker } from '../components/ClientWorkspacePicker'
import { Card, EmptyState, Pill, SearchInput, Skeleton, Toggle } from '../components/ui'
import { RuleBuilder } from '../components/RuleBuilder'
import { Group, EMPTY_BUILDER, buildCel, treeComplete } from '../lib/celBuilder'

const ACTION_COLOR: Record<string, string> = { block: '#F87171', redact: '#FBBF24', audit: '#60A5FA' }

interface Rule {
  id: number; name: string; description: string; enabled: boolean;
  cel_expression: string; builder_spec: any | null;
  apply_to: 'input' | 'output' | 'both'; action: 'block' | 'redact' | 'audit';
  sampling_rate: number; timeout_ms: number; profile_ids: number[];
  scope: string; workspace_id: string | null; component: string | null;
}

interface Profile {
  id: number; name: string; detector_type: string; enabled: boolean; config: any;
}

export function GuardrailsRules() {
  const [rules, setRules] = useState<Rule[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [creating, setCreating] = useState(false)
  // filters
  const [query, setQuery] = useState('')
  const [scopeFilter, setScopeFilter] = useState<'all' | 'global' | 'workspace' | 'component'>('all')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [selectedClient, setSelectedClient] = useState<string | null>(null)
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null)

  const wsHook = useWorkspaces()
  const wsMeta = useMemo(() => {
    const m: Record<string, { client_id?: string; display?: string }> = {}
    for (const w of (wsHook.data?.workspaces || [])) {
      m[w.workspace_id] = { client_id: w.client_id, display: w.display_name || w.name }
    }
    return m
  }, [wsHook.data])

  const load = async () => {
    setLoading(true)
    try {
      const [r, p] = await Promise.all([admin.listRules(), admin.listProfiles()])
      setRules(r.rules || []); setProfiles(p.profiles || [])
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const filtered = useMemo(() => rules.filter((r) => {
    if (query && !(`${r.name} ${r.description || ''} ${r.workspace_id || ''}`.toLowerCase().includes(query.toLowerCase()))) return false
    if (scopeFilter !== 'all' && r.scope !== scopeFilter) return false
    if (statusFilter === 'active' && !r.enabled) return false
    if (statusFilter === 'inactive' && r.enabled) return false
    // Client scope: keep rules whose workspace belongs to the selected client
    // (plus global rules, which apply everywhere).
    if (selectedClient && r.scope !== 'global' && wsMeta[r.workspace_id || '']?.client_id !== selectedClient) return false
    // Workspace scope: a chosen workspace shows its own rules + the global ones
    // that also govern it (so you see everything in force for that workspace).
    if (selectedWorkspace && r.scope !== 'global' && r.workspace_id !== selectedWorkspace) return false
    return true
  }), [rules, query, scopeFilter, statusFilter, selectedClient, selectedWorkspace, wsMeta])

  // Tier the rules by authority boundary (matches the runtime scope hierarchy).
  const tierGlobal = filtered.filter((r) => r.scope === 'global')
  const tierScoped = filtered.filter((r) => r.scope === 'workspace' || r.scope === 'component')

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Guardrails · Configuration</h1>
          <p className="text-muted text-sm">Rules decide <span className="text-gray-300">when</span> to evaluate. Each links one or more detector profiles and runs in our gateway layer (engine-independent).</p>
        </div>
        <button data-testid="rule-new" className="btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} /> New rule
        </button>
      </div>

      {/* Filter bar - one shared shape: client→workspace picker + search + scope + status */}
      <Card className="p-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <ClientWorkspacePicker
            selectedClient={selectedClient}
            selectedWorkspace={selectedWorkspace}
            onClientChange={setSelectedClient}
            onWorkspaceChange={setSelectedWorkspace}
            allowAll
          />
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search rules by name, description, or workspace…"
            className="flex-1 min-w-[200px]"
            testId="rule-filter-search"
          />
          <select data-testid="rule-filter-scope" className="input h-9 text-sm w-auto"
                  value={scopeFilter} onChange={(e) => setScopeFilter(e.target.value as any)}>
            <option value="all">Scope: All</option>
            <option value="global">Global</option>
            <option value="workspace">Workspace</option>
            <option value="component">Component</option>
          </select>
          <select data-testid="rule-filter-status" className="input h-9 text-sm w-auto"
                  value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as any)}>
            <option value="all">Status: All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>
      </Card>

      {loading ? (
        <Card className="p-6"><Skeleton h={220} /></Card>
      ) : rules.length === 0 ? (
        <Card className="p-10">
          <EmptyState icon={<ShieldCheck size={32} />}
            title="No guardrail rules yet"
            hint="Create a rule that links one or more detector profiles. Use the visual builder to target specific models, components, or messages - or leave it empty to apply to every request."
            cta={<button className="btn-primary" onClick={() => setCreating(true)}><Plus size={14} /> Create your first rule</button>} />
        </Card>
      ) : filtered.length === 0 ? (
        <Card className="p-10">
          <EmptyState icon={<Search size={28} />} title="No rules match your filters"
            hint="Try a different search term, scope, or status." />
        </Card>
      ) : (
        <div className="space-y-5" data-testid="rule-list">
          <RuleTier
            icon={<Globe size={14} />}
            title="Global enforcement"
            subtitle="Runs on every request across all clients and workspaces."
            accent="var(--color-accent)"
            rules={tierGlobal}
            profiles={profiles} wsMeta={wsMeta}
            onEdit={setEditing} reload={load}
          />
          <RuleTier
            icon={<Target size={14} />}
            title="Workspace & component policies"
            subtitle="Scoped to a specific workspace (or component) - isolated from other tenants."
            accent="var(--color-text-secondary)"
            rules={tierScoped}
            profiles={profiles} wsMeta={wsMeta}
            onEdit={setEditing} reload={load}
          />
        </div>
      )}

      {/* Sparse-state guide - keeps the page from looking hollow with few rules,
          and teaches the rules ↔ profiles model. */}
      {!loading && rules.length > 0 && rules.length < 3 && (
        <Card className="bg-app/40">
          <div className="text-[11px] uppercase tracking-wider font-semibold text-muted mb-3">How guardrails fit together</div>
          <div className="flex items-center gap-2 flex-wrap text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 bg-surface">
              <ShieldCheck size={13} className="text-accent" /> A <strong className="text-gray-200">Rule</strong> (CEL: when to run)
            </span>
            <span className="text-muted">links →</span>
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 bg-surface">
              one or more <strong className="text-gray-200">Detector Profiles</strong> (regex · secrets · PII · Bedrock)
            </span>
            <span className="text-muted">→ applies</span>
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 bg-surface">
              an <strong className="text-gray-200">Action</strong>: block · redact · audit
            </span>
          </div>
          <div className="text-[11.5px] text-muted mt-3">
            Build reusable profiles under <span className="text-gray-300">Guardrails → Detector Profiles</span>, then attach
            them to rules here. A rule with CEL <span className="mono text-gray-300">true</span> applies to every request;
            narrow it by model, component, or message content with the visual builder.
          </div>
        </Card>
      )}

      <AnimatePresence>
        {(editing || creating) && (
          <RuleEditor rule={editing} profiles={profiles}
                      onClose={() => { setEditing(null); setCreating(false) }}
                      onSaved={() => { setEditing(null); setCreating(false); load() }} />
        )}
      </AnimatePresence>
    </div>
  )
}

function RuleTier({ icon, title, subtitle, accent, rules, profiles, wsMeta, onEdit, reload }: {
  icon: React.ReactNode; title: string; subtitle: string; accent: string;
  rules: Rule[]; profiles: Profile[]; wsMeta: Record<string, { client_id?: string; display?: string }>;
  onEdit: (r: Rule) => void; reload: () => void
}) {
  if (rules.length === 0) return null
  return (
    <section>
      <div className="flex items-center gap-2 mb-2 px-0.5">
        <span style={{ color: accent }}>{icon}</span>
        <span className="text-[12px] uppercase tracking-wider font-semibold text-gray-200">{title}</span>
        <span className="text-[11px] text-muted">- {subtitle}</span>
        <span className="ml-auto text-[11px] text-muted tabular-nums">{rules.length}</span>
      </div>
      <div className="space-y-2">
        {rules.map((r) => (
          <RuleCard key={r.id} rule={r} profiles={profiles} wsMeta={wsMeta}
                    accent={accent} onEdit={() => onEdit(r)} reload={reload} />
        ))}
      </div>
    </section>
  )
}

function ScopeBadge({ rule, wsMeta }: { rule: Rule; wsMeta: Record<string, { client_id?: string; display?: string }> }) {
  if (rule.scope === 'global') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded"
            style={{ background: 'var(--color-accent-soft)', color: 'var(--color-accent)' }}>
        <Globe size={10} /> Global override
      </span>
    )
  }
  const meta = rule.workspace_id ? wsMeta[rule.workspace_id] : undefined
  return (
    <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-border text-muted"
          title={meta?.client_id ? `client: ${meta.client_id}` : undefined}>
      <Target size={10} />
      {rule.scope === 'component' ? 'Component' : 'Workspace'}: {rule.workspace_id || '-'}
      {rule.component ? ` · ${rule.component}` : ''}
    </span>
  )
}

function RuleCard({ rule, profiles, wsMeta, accent, onEdit, reload }: {
  rule: Rule; profiles: Profile[]; wsMeta: Record<string, { client_id?: string; display?: string }>;
  accent: string; onEdit: () => void; reload: () => void
}) {
  const r = rule
  return (
    <div
      data-testid={`rule-row-${r.id}`}
      onClick={onEdit}
      className="group rounded-xl border border-border bg-surface hover:bg-elevated/40 transition cursor-pointer"
      style={{ minHeight: 72, borderLeft: `3px solid ${accent}` }}
    >
      <div className="flex items-center gap-3 px-4 py-3 flex-wrap">
        {/* identity */}
        <div className="min-w-[220px] flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-gray-100 font-medium leading-tight">{r.name}</span>
            <ScopeBadge rule={r} wsMeta={wsMeta} />
          </div>
          <div className="text-[10.5px] text-muted mono truncate max-w-[420px] mt-1">
            <span className="text-muted/70">when </span>{r.cel_expression || 'true'}
          </div>
        </div>

        {/* profiles */}
        <div className="hidden md:flex flex-wrap gap-1 max-w-[200px]">
          {(r.profile_ids || []).length === 0
            ? <span className="text-[11px] text-muted">no profiles</span>
            : (r.profile_ids || []).map((pid) => {
                const p = profiles.find((x) => x.id === pid)
                return <Pill key={pid} color="#A78BFA">{p?.name || `#${pid}`}</Pill>
              })}
        </div>

        {/* action + apply-to */}
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-gray-300 rounded bg-app border border-border px-1.5 py-0.5">{r.apply_to}</span>
          <Pill color={ACTION_COLOR[r.action]}>{r.action}</Pill>
        </div>

        {/* status */}
        <div className="w-[78px]">
          {r.enabled
            ? <span className="inline-flex items-center gap-1 text-[11px] font-medium">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} />
                <span style={{ color: 'var(--color-ok)' }}>Active</span>
              </span>
            : <span className="inline-flex items-center gap-1 text-[11px] text-muted">
                <span className="w-1.5 h-1.5 rounded-full bg-muted" /> Inactive
              </span>}
        </div>

        {/* persistent, always-visible actions (no hidden menu, no clipping) */}
        <div className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <button data-testid={`rule-edit-${r.id}`} onClick={onEdit}
                  className="inline-flex items-center gap-1 text-[12px] text-muted hover:text-gray-100 px-2 py-1 rounded-lg hover:bg-app">
            <Pencil size={13} /> Edit
          </button>
          <button data-testid={`rule-toggle-${r.id}`}
                  onClick={async () => { await withToast(() => admin.updateRule(r.id, { enabled: !r.enabled })); reload() }}
                  className="inline-flex items-center gap-1 text-[12px] text-muted hover:text-gray-100 px-2 py-1 rounded-lg hover:bg-app">
            <Power size={13} /> {r.enabled ? 'Disable' : 'Enable'}
          </button>
          <button data-testid={`rule-delete-${r.id}`}
                  onClick={async () => { await withToast(() => admin.deleteRule(r.id), 'Rule deleted'); reload() }}
                  className="inline-flex items-center gap-1 text-[12px] text-danger hover:text-white px-2 py-1 rounded-lg hover:bg-danger/15">
            <Trash2 size={13} /> Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────── editor (large modal) ───────────────────────

function RuleEditor({ rule, profiles, onClose, onSaved }:
  { rule: Rule | null; profiles: Profile[]; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!rule
  const [name, setName] = useState(rule?.name ?? '')
  const [description, setDescription] = useState(rule?.description ?? '')
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [applyTo, setApplyTo] = useState<'input' | 'output' | 'both'>(rule?.apply_to ?? 'input')
  const [action, setAction] = useState<'block' | 'redact' | 'audit'>(rule?.action ?? 'block')
  const [profileIds, setProfileIds] = useState<number[]>(rule?.profile_ids ?? [])
  const [sampling, setSampling] = useState((rule?.sampling_rate ?? 1) * 100)
  const [timeout, setTimeoutS] = useState(rule ? Math.round((rule.timeout_ms ?? 1000) / 1000) : 60)
  const [tree, setTree] = useState<Group>(
    (rule?.builder_spec && rule.builder_spec.type === 'group') ? rule.builder_spec : EMPTY_BUILDER)
  const [advanced, setAdvanced] = useState(false)
  const [rawCel, setRawCel] = useState(rule?.cel_expression ?? 'true')
  // Scope: where this rule applies. Global = every workspace of every client;
  // Workspace = only the selected workspace. Default to workspace-scoped for NEW
  // rules so "global" is an explicit, deliberate choice (avoids surprise blocks).
  const [scope, setScope] = useState<'global' | 'workspace'>(
    rule ? (rule.scope === 'global' ? 'global' : 'workspace') : 'workspace')
  const [scopeWorkspace, setScopeWorkspace] = useState<string>(rule?.workspace_id ?? '')
  const wsHook = useWorkspaces()
  const workspaces = (wsHook.data?.workspaces || [])
  // group workspaces by client for the dropdown
  const wsByClient = useMemo(() => {
    const m: Record<string, any[]> = {}
    for (const w of workspaces) (m[w.client_id || 'unassigned'] ||= []).push(w)
    return m
  }, [workspaces])
  const cel = useMemo(() => advanced ? rawCel : buildCel(tree), [advanced, rawCel, tree])
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState<GuardrailTestResult | null>(null)

  const valid = name.trim().length > 0 && (advanced || treeComplete(tree)) &&
                (scope === 'global' || !!scopeWorkspace)

  async function save() {
    if (!valid) return
    setBusy(true)
    try {
      const body = {
        name: name.trim(), description: description.trim(), enabled,
        cel_expression: cel, builder_spec: advanced ? null : tree,
        apply_to: applyTo, action,
        sampling_rate: Math.max(0, Math.min(100, sampling)) / 100,
        timeout_ms: Math.max(100, timeout * 1000),
        profile_ids: profileIds,
        scope,
        workspace_id: scope === 'workspace' ? scopeWorkspace : null,
      }
      if (isEdit) await admin.updateRule(rule!.id, body)
      else await admin.createRule(body)
      toastOk(isEdit ? 'Rule updated' : 'Rule created')
      onSaved()
    } catch (e: any) { toastError(e.message || 'save failed') }
    finally { setBusy(false) }
  }

  return (
    <>
      <motion.div className="fixed inset-0 bg-black/60 z-40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <motion.div data-testid="rule-editor"
          className="w-[820px] max-w-[96vw] max-h-[90vh] card p-0 flex flex-col overflow-hidden pointer-events-auto"
          initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.96 }}>
          <div className="px-6 pt-5 pb-3 border-b border-border flex items-center justify-between">
            <div>
              <div className="text-base font-semibold text-white">{isEdit ? 'Edit rule' : 'New guardrail rule'}</div>
              <div className="text-[11px] text-muted">Rules combine when (CEL) + which detectors. Evaluated in our gateway layer.</div>
            </div>
            <button onClick={onClose} className="text-muted hover:text-gray-200"><X size={18} /></button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-6">
            <StageHeader n={1} title="Identity & scope" sub="Name the rule, choose where it applies, and what it does on match." />
            {/* identity */}
            <div className="grid grid-cols-2 gap-4">
              <Field label="Rule name" hint="Short, human-readable. Shown in the rules list.">
                <input data-testid="rule-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Status" hint="Rule will be active and applied to matching requests.">
                <div className="flex items-center gap-3 h-9 px-3 rounded-xl bg-app border border-border">
                  <Toggle checked={enabled} onChange={setEnabled} label={enabled ? 'Active' : 'Inactive'} />
                </div>
              </Field>
            </div>
            <Field label="Description" hint="Optional. Helps teammates understand the intent.">
              <textarea data-testid="rule-description" rows={4}
                className="input min-h-[96px] resize-y leading-relaxed"
                placeholder="What does this rule protect against, and why? (visible to teammates auditing guardrails)"
                value={description}
                onChange={(e) => setDescription(e.target.value)} />
            </Field>

            {/* Scope - where this rule applies. Highlighted so it's never ambiguous. */}
            <Field label="Scope - where does this rule apply?"
                   hint="Global applies to EVERY workspace of EVERY client. Workspace applies to one workspace only.">
              <div className="grid grid-cols-2 gap-2" data-testid="rule-scope">
                <button type="button" data-testid="rule-scope-global" onClick={() => setScope('global')}
                  className="text-left rounded-lg border-2 p-3 transition"
                  style={scope === 'global'
                    ? { borderColor: 'var(--color-accent)', background: 'var(--color-accent-soft)' }
                    : { borderColor: 'var(--color-border)', background: 'var(--color-app)' }}>
                  <div className="flex items-center gap-1.5 text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>
                    🌐 Global
                  </div>
                  <div className="text-[11px] text-muted mt-0.5">Applies to every workspace of every client.</div>
                </button>
                <button type="button" data-testid="rule-scope-workspace" onClick={() => setScope('workspace')}
                  className="text-left rounded-lg border-2 p-3 transition"
                  style={scope === 'workspace'
                    ? { borderColor: 'var(--color-accent)', background: 'var(--color-accent-soft)' }
                    : { borderColor: 'var(--color-border)', background: 'var(--color-app)' }}>
                  <div className="flex items-center gap-1.5 text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>
                    🎯 Specific workspace
                  </div>
                  <div className="text-[11px] text-muted mt-0.5">Applies to one workspace only.</div>
                </button>
              </div>
              {scope === 'workspace' && (
                <select data-testid="rule-scope-workspace-select"
                        className="input mt-2"
                        value={scopeWorkspace}
                        onChange={(e) => setScopeWorkspace(e.target.value)}>
                  <option value="">- select a workspace -</option>
                  {Object.entries(wsByClient).map(([client, list]) => (
                    <optgroup key={client} label={client}>
                      {list.map((w: any) => (
                        <option key={w.workspace_id} value={w.workspace_id}>
                          {w.display_name || w.name || w.workspace_id} ({w.workspace_id})
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              )}
              {scope === 'global' && (
                <div className="mt-2 text-[11px] rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-warn">
                  ⚠️ This rule will run on <strong>every request to every workspace</strong> across all clients.
                  Use a specific workspace unless you intend a platform-wide policy.
                </div>
              )}
            </Field>

            {/* apply to + action */}
            <div className="grid grid-cols-2 gap-4">
              <Field label="Apply on" hint="Where in the request lifecycle this rule runs.">
                <div className="flex bg-elevated rounded-xl p-1" data-testid="rule-apply-to">
                  {(['input', 'output', 'both'] as const).map((v) => (
                    <button key={v} type="button" data-testid={`rule-apply-${v}`} onClick={() => setApplyTo(v)}
                      className={`px-3 py-1.5 rounded-lg text-xs capitalize flex-1 ${applyTo === v ? 'bg-accent text-white' : 'text-muted'}`}>
                      {v === 'input' ? 'Input only' : v === 'output' ? 'Output only' : 'Both'}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="Action on match" hint="What happens when a linked detector fires.">
                <div className="flex bg-elevated rounded-xl p-1" data-testid="rule-action">
                  {(['block', 'redact', 'audit'] as const).map((v) => (
                    <button key={v} type="button" data-testid={`rule-action-${v}`} onClick={() => setAction(v)}
                      className={`px-3 py-1.5 rounded-lg text-xs capitalize flex-1 ${action === v ? 'bg-accent text-white' : 'text-muted'}`}>
                      {v}
                    </button>
                  ))}
                </div>
              </Field>
            </div>

            {/* profiles */}
            <Field label="Guardrail profiles" hint="One or more detector profiles to run when the rule matches.">
              <ProfileMultiSelect profiles={profiles} selected={profileIds} onChange={setProfileIds} />
            </Field>

            {/* sampling + timeout */}
            <div className="grid grid-cols-2 gap-4">
              <Field label="Sampling rate (%)" hint="Percentage of matching requests to process.">
                <input data-testid="rule-sampling" className="input" type="number" min={0} max={100}
                  value={sampling} onChange={(e) => setSampling(Number(e.target.value))} />
              </Field>
              <Field label="Max execution time (s)" hint="Per-rule budget. Detectors that exceed it fail open.">
                <input data-testid="rule-timeout" className="input" type="number" min={1}
                  value={timeout} onChange={(e) => setTimeoutS(Number(e.target.value))} />
              </Field>
            </div>

            {/* visual rule builder + CEL preview */}
            <StageHeader n={2} title="Conditions" sub="Plain-English criteria for when this rule should evaluate." />
            <div className="space-y-2">
              <div className="flex items-center justify-end">
                <label className="text-[11px] text-muted inline-flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)}
                    data-testid="rule-advanced" /> Advanced (raw CEL)
                </label>
              </div>
              {advanced ? (
                <div>
                  <textarea data-testid="rule-raw-cel" className="input mono text-xs min-h-[120px]"
                    value={rawCel} onChange={(e) => setRawCel(e.target.value)} />
                  <div className="text-[11px] text-muted mt-1">Editing raw CEL disables the visual builder.</div>
                </div>
              ) : (
                <RuleBuilder value={tree} onChange={setTree} />
              )}
            </div>

            {/* test panel */}
            <StageHeader n={3} title="Verification" sub="Dry-run the rule against sample content before saving." />
            <TestPanel cel={cel} action={action} profileIds={profileIds} setResult={setTest} result={test} />
          </div>

          <div className="px-6 py-3 border-t border-border flex justify-between items-center bg-surface">
            <div className="text-[11px] text-muted">{isEdit ? `Editing rule #${rule!.id}` : 'New rule'}</div>
            <div className="flex items-center gap-2">
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button data-testid="rule-save" className="btn-primary" disabled={!valid || busy} onClick={save}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {isEdit ? 'Save changes' : 'Save rule'}
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    </>
  )
}

// ─────────────────────── helpers ───────────────────────

function Field({ label, hint, children }:
  { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm text-gray-200 font-medium mb-1.5">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-muted mt-1">{hint}</div>}
    </div>
  )
}

// Numbered stage divider - groups the long rule form into clear, scannable
// stages (Identity → Conditions → Verification) to cut "configuration dump"
// fatigue, while keeping every field on one scroll (no hidden tabs).
function StageHeader({ n, title, sub }: { n: number; title: string; sub?: string }) {
  return (
    <div className="flex items-center gap-2.5 pt-1">
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold shrink-0"
            style={{ background: 'var(--color-accent-soft)', color: 'var(--color-accent)' }}>{n}</span>
      <div>
        <div className="text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>{title}</div>
        {sub && <div className="text-[11px] text-muted">{sub}</div>}
      </div>
      <span className="h-px flex-1 ml-1" style={{ background: 'var(--color-border)' }} />
    </div>
  )
}

function ProfileMultiSelect({ profiles, selected, onChange }:
  { profiles: Profile[]; selected: number[]; onChange: (s: number[]) => void }) {
  const [open, setOpen] = useState(false)
  const sel = profiles.filter((p) => selected.includes(p.id))
  const rest = profiles.filter((p) => !selected.includes(p.id))
  function toggle(id: number) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  }
  return (
    <div className="relative" data-testid="rule-profiles">
      <div className="bg-app border border-border rounded-xl px-2 py-2 min-h-[42px] flex flex-wrap items-center gap-1 cursor-pointer"
           onClick={() => setOpen((v) => !v)}>
        {sel.length === 0 && <span className="text-[11px] text-muted px-1">no profiles linked yet</span>}
        {sel.map((p) => (
          <span key={p.id} className="pill" data-testid={`profile-chip-${p.id}`}
                style={{ background: '#A78BFA1f', color: '#A78BFA', borderColor: '#A78BFA55' }}>
            {p.name}
            <button onClick={(e) => { e.stopPropagation(); toggle(p.id) }}
                    className="ml-1 hover:text-white"><X size={11} /></button>
          </span>
        ))}
      </div>
      {open && (
        <div className="absolute top-full mt-1 left-0 right-0 bg-surface border border-border rounded-xl shadow-xl z-10 py-1 max-h-60 overflow-y-auto"
             onMouseLeave={() => setOpen(false)}>
          {rest.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted">All profiles linked</div>
          ) : rest.map((p) => (
            <button key={p.id} data-testid={`profile-option-${p.id}`}
                    onClick={() => { toggle(p.id); setOpen(false) }}
                    className="w-full text-left px-3 py-1.5 text-sm hover:bg-elevated flex items-center justify-between">
              <span className="text-gray-200">{p.name}</span>
              <Pill color="#A78BFA">{p.detector_type}</Pill>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function TestPanel({ cel, action, profileIds, result, setResult }:
  { cel: string; action: 'block' | 'redact' | 'audit'; profileIds: number[];
    result: GuardrailTestResult | null; setResult: (r: GuardrailTestResult | null) => void }) {
  const [content, setContent] = useState('My SSN is 123-45-6789 and email is bob@test.com')
  const [model, setModel] = useState('claude-sonnet-4-5')
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      const r = await admin.testGuardrail({
        content, cel_expression: cel, action, profile_ids: profileIds, model })
      setResult(r)
    } catch (e: any) { toastError(e.message || 'test failed') }
    finally { setBusy(false) }
  }

  return (
    <div className="rounded-xl border border-border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-gray-200 inline-flex items-center gap-2">
            <FlaskConical size={14} /> Test panel
          </div>
          <div className="text-[11px] text-muted">Real evaluation - runs the rule's CEL + linked profiles against sample content.</div>
        </div>
        <button data-testid="rule-test-run" className="btn-ghost" disabled={busy || profileIds.length === 0} onClick={run}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : <ClipboardCheck size={13} />} Run test
        </button>
      </div>
      <div className="grid grid-cols-[1fr_180px] gap-2">
        <textarea data-testid="rule-test-content" className="input min-h-[64px] text-sm" value={content}
          onChange={(e) => setContent(e.target.value)} />
        <input data-testid="rule-test-model" className="input text-xs" value={model}
          onChange={(e) => setModel(e.target.value)} placeholder="sample model" />
      </div>
      {profileIds.length === 0 && (
        <div className="text-[11px] text-warn">Link at least one profile above to run the test.</div>
      )}
      {result && (
        <div data-testid="rule-test-result" className="rounded-lg border border-border p-3 space-y-2 bg-app/50">
          <div className="flex items-center gap-2 flex-wrap">
            <Pill color={result.violation ? '#F87171' : '#34D399'}>
              {result.violation ? `violation → ${result.action}` : 'pass'}
            </Pill>
            <Pill color={result.cel_matched ? '#34D399' : '#6B7280'}>
              CEL {result.cel_matched ? 'matched' : 'skipped'}
            </Pill>
            <span className="text-[11px] text-muted">total {result.processing_ms} ms · CEL {result.cel_processing_ms} ms</span>
          </div>
          {result.matched_condition && (
            <div className="text-[11px] text-muted">
              <span className="text-gray-300">Matched:</span> <span className="mono">{result.matched_condition}</span>
            </div>
          )}
          {result.findings.length > 0 && (
            <div className="space-y-1">
              {result.findings.map((f, i) => (
                <div key={i} className="text-[12px] flex items-center gap-2" data-testid={`finding-${i}`}>
                  <Pill color={ACTION_COLOR[f.action] || '#6B7280'}>{f.action}</Pill>
                  <span className="text-gray-200">{f.detector_type}</span>
                  <span className="text-muted mono">{f.detector}:{f.category}</span>
                  <span className="text-muted">→ {f.excerpt}</span>
                  <span className="text-[10px] text-muted ml-auto">{f.processing_ms} ms</span>
                </div>
              ))}
            </div>
          )}
          {result.errors.length > 0 && (
            <div className="text-[11px] text-warn">{result.errors.join('; ')}</div>
          )}
        </div>
      )}
    </div>
  )
}
