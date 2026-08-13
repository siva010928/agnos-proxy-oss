// Admin → Routing screen (rebuilt for WAVE 16-UX-2).
// - Alias editor uses the shared AliasTargetsBuilder (provider dropdown sourced
//   from THIS workspace's configured providers, model autocomplete from
//   /admin/models filtered to provider, weight + reorder + delete).
// - Delete uses the shared ConfirmModal (replaces native confirm()).
// - Resolve Preview panel is preserved (it's the strong piece) and now calls
//   /admin/routing/preview live whenever workspace/alias changes.
// - Row ⋯ menu uses portal RowMenu so it never gets clipped.

import { motion, AnimatePresence } from 'framer-motion'
import { Eye, Loader2, Network, Plus, Save, Zap } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { admin, type ParityResult } from '../api/client'
import { useWorkspaces } from '../lib/api'
import {
  AliasTarget, AliasTargetsBuilder, ConfirmModal, Field, Modal, RowMenu,
  targetsValid,
} from '../components/editors'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Pill, ProviderBadge, Skeleton } from '../components/ui'
import { ClientWorkspacePicker } from '../components/ClientWorkspacePicker'
import { OnboardingTrail } from '../components/OnboardingTrail'

type ChatModels = Record<string, AliasTarget[]>

// Alias names mirror model-id conventions, which legitimately contain dots
// (e.g. gemini-3.1-pro-preview) and underscores. The backend only requires a
// non-empty string, so the frontend must not be stricter - an over-strict
// regex here would reject already-saved aliases and trap users on edit.
const SLUG_RE = /^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/

export function Routing() {
  const ws = useWorkspaces()
  const [searchParams] = useSearchParams()
  // Deep-link: /app/admin/routing?workspace=ws-x preselects that workspace.
  const urlWorkspace = searchParams.get('workspace')
  const onboarding = searchParams.get('onboarding') === '1'
  // Routing configures ONE workspace at a time, so the selection must be STABLE
  // across reloads / navigation (the old code force-picked the global-first
  // workspace on every mount, which made it "land in a different workspace" and
  // made per-workspace engine-split / alias edits look lost). Persist the choice
  // and restore it; the ClientWorkspacePicker's own client-filtered auto-select
  // fills in a sensible default when nothing valid is chosen.
  const LS_WS = 'agnos_routing_ws', LS_CL = 'agnos_routing_client'
  const [selected, setSelected] = useState<string | null>(
    () => urlWorkspace || (typeof localStorage !== 'undefined' ? localStorage.getItem(LS_WS) : null))
  const [selectedClient, setSelectedClient] = useState<string | null>(
    () => (typeof localStorage !== 'undefined' ? localStorage.getItem(LS_CL) : null))
  const [clientSeeded, setClientSeeded] = useState(false)
  const [editing, setEditing] = useState<{ alias: string; targets: AliasTarget[] } | null>(null)
  const [creating, setCreating] = useState(false)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)

  // persist the selection so reload / returning to the page keeps the SAME workspace
  useEffect(() => { try { selected ? localStorage.setItem(LS_WS, selected) : localStorage.removeItem(LS_WS) } catch { /* ignore */ } }, [selected])
  useEffect(() => { try { selectedClient ? localStorage.setItem(LS_CL, selectedClient) : localStorage.removeItem(LS_CL) } catch { /* ignore */ } }, [selectedClient])

  // deep-link only: seed the client filter from ?workspace= once (the picker
  // auto-selects the workspace itself when the current one isn't valid).
  useEffect(() => {
    const list = ws.data?.workspaces || []
    if (urlWorkspace && !clientSeeded && selected && list.length) {
      const match = list.find((w: any) => w.workspace_id === selected)
      if (match?.client_id) setSelectedClient(match.client_id)
      setClientSeeded(true)
    }
  }, [ws.data, selected, urlWorkspace, clientSeeded])

  const wsObj = (ws.data?.workspaces || []).find((w: any) => w.workspace_id === selected)
  const chat: ChatModels = (wsObj?.chat_models as any) || {}

  async function saveAlias(alias: string, targets: AliasTarget[]) {
    const next = { ...chat, [alias]: targets }
    const res: any = await admin.updateWorkspace(selected!, { chat_models: next })
    // The backend auto-sets default_chat_alias when an admin saves aliases
    // without picking a default. Surface the warning loudly so the admin sees
    // exactly which alias became the default and can change it if they want.
    if (res?.warning) {
      toastOk(`Alias '${alias}' saved`)
      toastError(res.warning)
    } else {
      toastOk(`Alias '${alias}' saved`)
    }
    ws.refetch()
  }
  async function deleteAlias(alias: string) {
    const next = { ...chat }
    delete next[alias]
    // If this alias was the workspace default, re-point it (to a remaining alias,
    // or none) in the SAME patch so the workspace never references a removed alias.
    const patch: any = { chat_models: next }
    if (wsObj?.default_chat_alias === alias) {
      patch.default_chat_alias = Object.keys(next)[0] || null
    }
    await admin.updateWorkspace(selected!, patch)
    toastOk(`Alias '${alias}' removed`)
    ws.refetch()
  }

  return (
    <div className="space-y-5">
      {onboarding && (
        <OnboardingTrail step={3} workspace={selected}
          next={{ label: 'Finish: view workspace', to: '/workspaces' }}
          nextEnabled={Object.keys(chat).length > 0}
          nextHint={Object.keys(chat).length === 0 ? 'Add at least one alias to finish.' : 'A default alias is set for you.'} />
      )}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Admin · Routing</h1>
          <p className="text-muted text-sm">
            Define how an alias resolves to providers. The first target is the <span className="text-success">primary</span>;
            later targets form the <span className="text-warn">fallback chain</span>.
            Weighted targets at the same priority load-balance traffic. The Resolve Preview
            panel calls the gateway live so you see what a request will actually do.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ClientWorkspacePicker selectedClient={selectedClient} selectedWorkspace={selected} onClientChange={setSelectedClient} onWorkspaceChange={setSelected} />
          <button
            data-testid="alias-new"
            className="btn-primary"
            disabled={!selected}
            onClick={() => setCreating(true)}
          >
            <Plus size={16} /> New alias
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
        <Card className="p-0">
          {!selected ? (
            <div className="p-10">
              <EmptyState
                icon={<Network size={32} />}
                title="Select a workspace"
                hint="Routing is configured per workspace. Pick a client, then a workspace above to view and edit its alias routing and engine split." />
            </div>
          ) : Object.keys(chat).length === 0 ? (
            <div className="p-10">
              <EmptyState
                icon={<Network size={32} />}
                title={`No aliases in ${wsObj?.display_name || selected}`}
                hint={
                  <>
                    Create an alias that maps to one or more provider targets. The first target
                    is the primary; later targets form the fallback chain. Example:
                    <span className="block mono text-[11px] text-gray-300 mt-1">
                      claude-sonnet-4-5 → bedrock(weight 3) + anthropic(weight 1)
                    </span>
                  </>
                }
                cta={
                  <button className="btn-primary" onClick={() => setCreating(true)} disabled={!selected}
                          data-testid="alias-new-empty">
                    <Plus size={14} /> Create alias
                  </button>
                }
              />
            </div>
          ) : (
            <div className="divide-y divide-border" data-testid="alias-list">
              {Object.entries(chat).map(([alias, targets]) => {
                const list = (targets as any) as AliasTarget[]
                const totalWeight = list.reduce((a, t) => a + (t.weight || 1), 0)
                return (
                  <div key={alias} className="px-5 py-4" data-testid={`alias-row-${alias}`}>
                    <div className="flex items-center justify-between mb-2 gap-2">
                      <div className="min-w-0 flex-1">
                        <span className="mono text-sm text-gray-100 font-medium truncate">{alias}</span>
                        <span className="text-[11px] text-muted ml-2">
                          {list.length} target{list.length === 1 ? '' : 's'}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          data-testid={`alias-edit-${alias}`}
                          className="btn-ghost text-xs"
                          onClick={() => setEditing({ alias, targets: [...list] })}
                        >
                          <Save size={12} /> edit
                        </button>
                        <RowMenu
                          testId={`alias-menu-${alias}`}
                          items={[
                            {
                              label: 'Edit',
                              onSelect: () => setEditing({ alias, targets: [...list] }),
                              testId: `alias-menu-edit-${alias}`,
                            },
                            {
                              label: 'Delete',
                              danger: true,
                              onSelect: () => setConfirmDel(alias),
                              testId: `alias-delete-${alias}`,
                            },
                          ]}
                        />
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium"
                            style={{ background: 'var(--color-accent-soft)', color: 'var(--color-accent)' }}>
                        <Network size={11} /> request → <span className="mono">{alias}</span>
                      </span>
                      <span className="text-muted">→</span>
                      {(() => {
                        // Weighted load-balancing only kicks in when a weight differs
                        // from 1; otherwise it's pure failover (target[0] always primary,
                        // the rest are tried only on failure). Label accordingly so we
                        // don't imply 50/50 LB when it's actually 100% + failover.
                        const isWeightedLB = list.some(t => (t.weight || 1) !== 1)
                        return list.map((t, i) => {
                          const pct = Math.round(((t.weight || 1) / totalWeight) * 100)
                          const isPrimary = i === 0
                          return (
                            <div key={i} className="inline-flex items-center gap-1.5">
                              {i > 0 && (
                                <span className="text-[10px] text-muted inline-flex items-center gap-0.5" title="Tried only if earlier targets fail">
                                  ↳ on failure
                                </span>
                              )}
                              <span className={`inline-flex flex-col gap-1 rounded-lg px-2.5 py-1.5 border ${isPrimary ? 'border-accent/40 bg-elevated' : 'border-border bg-app'}`}>
                                <span className="inline-flex items-center gap-1.5">
                                  <ProviderBadge provider={t.provider} />
                                  <span className="text-[10.5px] text-gray-300 mono break-all">{t.model_id}</span>
                                </span>
                                {isWeightedLB ? (
                                  <span className="inline-flex items-center gap-1.5">
                                    <span className="h-1 rounded-full bg-accent" style={{ width: `${Math.max(8, pct * 0.6)}px` }} />
                                    <span className="text-[9px] text-muted">{isPrimary ? 'primary · ' : 'weighted · '}{pct}% of traffic</span>
                                  </span>
                                ) : (
                                  <span className="text-[9px] uppercase tracking-wide"
                                        style={{ color: isPrimary ? 'var(--color-accent)' : 'var(--color-muted)' }}>
                                    {isPrimary ? 'primary · 100% of traffic' : 'active fallback (on failure)'}
                                  </span>
                                )}
                              </span>
                            </div>
                          )
                        })
                      })()}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Card>

        <ResolvePreview workspace={selected} chat={chat} />
      </div>

      {/* Engine routing is GATEWAY-WIDE (one setting for the whole gateway). We render
          it once a workspace is picked so parity can run against a real account. */}
      {selected && <EngineOverrideCard workspaceId={selected} chat={chat} />}

      <AnimatePresence>
        {(editing || creating) && selected && (
          <AliasEditor
            wsId={selected}
            edit={editing}
            chat={chat}
            onClose={() => { setEditing(null); setCreating(false) }}
            onSaved={async (alias, targets) => {
              await saveAlias(alias, targets)
              setEditing(null)
              setCreating(false)
            }}
          />
        )}
      </AnimatePresence>

      <ConfirmModal
        open={!!confirmDel}
        onCancel={() => setConfirmDel(null)}
        title={`Remove alias '${confirmDel}'?`}
        message={
          <>
            Existing requests using this alias will start returning <span className="mono">404</span> until
            you create a replacement or fall back to the workspace's <span className="mono">default_chat_alias</span>.
          </>
        }
        identifier={confirmDel ? `alias '${confirmDel}' in workspace '${selected}'` : null}
        confirmLabel="Remove alias"
        danger
        onConfirm={async () => {
          if (!confirmDel) return
          await withToast(() => deleteAlias(confirmDel))
          setConfirmDel(null)
        }}
        testId="confirm-alias-delete"
      />
    </div>
  )
}

// ───────────────────────── Resolve Preview ─────────────────────────

function ResolvePreview({ workspace, chat }: { workspace: string | null; chat: ChatModels }) {
  const aliases = Object.keys(chat || {})
  const [alias, setAlias] = useState<string>(aliases[0] || '')
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!alias && aliases.length) setAlias(aliases[0])
  }, [aliases.join(',')])

  async function probe() {
    if (!workspace) return
    setBusy(true)
    try {
      setData(await admin.routingPreview(workspace, alias || undefined))
    } catch (e: any) {
      toastError(e?.message || 'preview failed')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    if (workspace) probe()
  }, [workspace, alias])

  const targets: any[] = data?.resolved_targets || []
  const totalWeight = targets.reduce((a, t) => a + (t.weight || 1), 0)

  return (
    <Card className="p-0 sticky top-0 self-start" data-testid="resolve-preview">
      <div className="px-5 py-4 border-b border-border">
        <div className="flex items-center gap-2">
          <Eye size={14} className="text-accent" />
          <span className="text-sm font-semibold text-gray-100">Resolve Preview</span>
        </div>
        <div className="text-[11px] text-muted mt-1">
          Live introspection · what a request to this alias resolves to right now.
        </div>
      </div>
      <div className="px-5 py-4 space-y-3">
        <Field label="Alias" hint="Pick an alias to preview the resolved targets.">
          <select
            data-testid="preview-alias"
            className="input mono text-xs"
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
          >
            {aliases.length === 0 && <option value="">- no aliases -</option>}
            {aliases.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </Field>
        {busy ? (
          <Skeleton h={120} />
        ) : !data ? (
          <div className="text-[11px] text-muted py-4">Pick an alias to preview.</div>
        ) : targets.length === 0 ? (
          <div className="text-[11px] text-warn py-2">Alias has no targets registered.</div>
        ) : (
          <div className="space-y-2" data-testid="preview-targets">
            {targets.map((t, i) => (
              <div key={i} className="bg-app border border-border rounded-xl p-2.5">
                <div className="flex items-center justify-between mb-1 gap-2">
                  <div className="flex items-center gap-1.5 min-w-0 flex-1">
                    <ProviderBadge provider={t.provider} />
                    {i === 0 ? (
                      <Pill color="#34D399"><Zap size={10} /> primary</Pill>
                    ) : (
                      <Pill color="#FBBF24">fallback #{i}</Pill>
                    )}
                  </div>
                  {t.weight && t.weight !== 1 && (
                    <span className="text-[11px] text-muted">w={t.weight}</span>
                  )}
                </div>
                <div className="mono text-[11px] text-gray-300 break-all">{t.model_id}</div>
                {t.weight && totalWeight > 0 && i === 0 && (
                  <div className="mt-1.5 h-1 rounded-full bg-border overflow-hidden">
                    <div
                      className="h-full bg-accent"
                      style={{ width: `${(t.weight / totalWeight) * 100}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {data?.guardrails && Object.keys(data.guardrails).length > 0 && (
          <div className="pt-2 border-t border-border">
            <div className="text-[11px] text-muted mb-1">Guardrails</div>
            <div className="flex flex-wrap gap-1">
              {Object.entries(data.guardrails).map(([k, v]: any) => (
                <Pill key={k} color="#A78BFA">
                  {k}{typeof v === 'string' ? `:${v}` : ''}
                </Pill>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}

// ───────────────────────── Alias editor (shared builder) ─────────────────────────

function AliasEditor({
  wsId,
  edit,
  chat,
  onClose,
  onSaved,
}: {
  wsId: string
  edit: { alias: string; targets: AliasTarget[] } | null
  chat: ChatModels
  onClose: () => void
  onSaved: (alias: string, targets: AliasTarget[]) => Promise<void>
}) {
  const isEdit = !!edit
  const [alias, setAlias] = useState(edit?.alias ?? '')
  const [targets, setTargets] = useState<AliasTarget[]>(edit?.targets ?? [])
  const [busy, setBusy] = useState(false)
  const [triedSave, setTriedSave] = useState(false)

  // Validation surfaced in the footer.
  // NOTE: the alias name is immutable on edit (the input is disabled), so we
  // only validate the name when CREATING. Re-validating a field the user can't
  // change would trap them in an un-savable modal whenever an existing alias
  // uses characters this regex doesn't cover (e.g. dots in gemini-3.1-...).
  const errors: string[] = []
  if (!isEdit) {
    if (!alias.trim()) errors.push('alias name is required')
    else if (!SLUG_RE.test(alias)) errors.push('alias must be lowercase letters, digits, hyphens, dots or underscores (no leading/trailing dash)')
    else if (chat[alias]) errors.push(`alias '${alias}' already exists`)
  }
  const tv = targetsValid(targets)
  if (!tv.ok) errors.push(...tv.errors)
  const valid = errors.length === 0

  async function save() {
    if (!valid) { setTriedSave(true); return }
    setBusy(true)
    try {
      await onSaved(alias, targets)
    } catch (e: any) {
      toastError(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={isEdit ? `Edit alias · ${edit!.alias}` : 'New alias'}
      subtitle={`Workspace ${wsId}`}
      size="lg"
      testId="alias-editor"
      footer={
        <>
          <button type="button" className="btn-ghost text-sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary text-sm disabled:opacity-50"
            disabled={busy}
            onClick={save}
            data-testid="alias-save"
          >
            {busy ? <><Loader2 size={14} className="animate-spin" /> Saving…</>
                  : (isEdit ? 'Save changes' : 'Create alias')}
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <Field
          label={isEdit ? 'Alias name (immutable)' : 'Alias name'}
          hint={isEdit
            ? <>The alias name is the contract your components send in <code>model="…"</code> - renaming it would silently break every caller. To change it, <strong>delete this alias and create a new one</strong>. You can freely edit the targets, weights, and fallback chain below.</>
            : 'Lowercase, hyphens. This is the value your code passes in the model field - e.g. claude-sonnet-4-5.'}
          required
        >
          <input
            className="input mono text-xs disabled:opacity-60 disabled:cursor-not-allowed"
            value={alias}
            disabled={isEdit}
            onChange={(e) => setAlias(e.target.value.toLowerCase())}
            placeholder="claude-sonnet-4-5"
            data-testid="alias-name"
          />
        </Field>

        <div>
          <div className="text-[12px] font-semibold text-white mb-1">Model targets</div>
          <AliasTargetsBuilder
            workspaceId={wsId}
            targets={targets}
            onChange={setTargets}
            testIdPrefix="target"
            showHelp
            showErrors={false}
          />
        </div>

        {/* Validation only appears AFTER a save attempt - a pristine new-alias
            form shouldn't yell. Deduped so each issue shows once. */}
        {triedSave && errors.length > 0 && (
          <div className="bg-danger/10 border border-danger/40 rounded-lg p-3 text-[11.5px] text-danger space-y-0.5">
            {[...new Set(errors)].map((e, i) => <div key={i}>• {e}</div>)}
          </div>
        )}
      </div>
    </Modal>
  )
}


// ───────────────────────── Engine Override per workspace ─────────────────────────

// Engine override value per provider:
//   '' → rented (Bifrost, default) · 'direct' → owned (DirectEngine) · number 1-99 → split % to owned
type OverrideVal = string | number
const mode = (v: OverrideVal | undefined): 'rented' | 'owned' | 'split' =>
  v === 'direct' ? 'owned' : (typeof v === 'number' && v > 0 && v < 100) ? 'split' : 'rented'
const splitPct = (v: OverrideVal | undefined): number =>
  v === 'direct' ? 100 : typeof v === 'number' ? v : 0

function EngineOverrideCard({ workspaceId, chat }: { workspaceId: string; chat: ChatModels }) {
  // Engine routing is GATEWAY-WIDE - ONE setting for the whole gateway, identical
  // for every client + workspace (not per-workspace). We load it from and save it
  // to /admin/engine-routing. `workspaceId` is used ONLY to run the parity check
  // against a real provider account. A local `draft` holds unsaved edits.
  const [serverOverrides, setServerOverrides] = useState<Record<string, OverrideVal>>({})
  const [draft, setDraft] = useState<Record<string, OverrideVal> | null>(null)
  const overrides = draft ?? serverOverrides
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    admin.getEngineRouting()
      .then((r) => { setServerOverrides((r.overrides || {}) as Record<string, OverrideVal>); setLoaded(true) })
      .catch(() => setLoaded(true))
  }, [])
  // Inline parity per provider: run the SAME prompt on the rented (Bifrost) and
  // owned (Direct) engine so the admin sees exactly how a split would flow before
  // dialing it up (rented vs direct latency + verdict + similarity).
  const [parity, setParity] = useState<Record<string, { loading?: boolean; res?: ParityResult; err?: string }>>({})
  // A sensible probe model per provider so the parity check works WITHOUT needing
  // an alias (engine routing is per-provider, independent of aliases). An alias
  // target for the provider is preferred when present.
  const DEFAULT_PARITY_MODEL: Record<string, string> = {
    anthropic: 'claude-haiku-4-5-20251001',
    bedrock: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    gemini: 'gemini-2.5-flash',
    openai: 'gpt-4o-mini',
    azure: 'gpt-4o-mini',
  }
  function modelFor(p: string): string | null {
    for (const targets of Object.values(chat || {})) {
      const list = Array.isArray(targets) ? targets : [targets]
      const hit = (list as any[]).find((t) => t.provider === p)
      if (hit) return hit.model_id
    }
    return DEFAULT_PARITY_MODEL[p] || null
  }
  async function checkParity(p: string) {
    const model = modelFor(p)
    if (!model) { setParity((s) => ({ ...s, [p]: { err: 'no default model for this provider - add an alias target above' } })); return }
    setParity((s) => ({ ...s, [p]: { loading: true } }))
    try {
      const res = await admin.parityRun({ workspace_id: workspaceId, provider: p, model_id: model,
                                          prompt: 'Reply with exactly one word: pong', max_tokens: 32 })
      setParity((s) => ({ ...s, [p]: { res } }))
    } catch (e: any) {
      setParity((s) => ({ ...s, [p]: { err: e?.message || 'parity failed' } }))
    }
  }

  async function save() {
    setBusy(true)
    try {
      const r = await admin.setEngineRouting(overrides as Record<string, string | number>)
      setServerOverrides(((r as any)?.overrides || overrides) as Record<string, OverrideVal>)
      setDraft(null)
      toastOk('Engine routing saved (gateway-wide)')
    } catch (e: any) {
      toastError(e?.message || 'save failed')
    } finally { setBusy(false) }
  }

  // Dual-engine providers: Bifrost (rented) can serve them, so rented↔split↔owned
  // all apply. Direct-only providers have no Bifrost adapter, so they are ALWAYS
  // served by our DirectEngine (nothing to toggle).
  const PROVIDERS = ['anthropic', 'bedrock', 'gemini', 'openai', 'azure']
  const DIRECT_ONLY = ['vertex_ai', 'litellm_proxy', 'ollama', 'hosted_vllm']
  const PROVIDER_LABELS: Record<string, string> = {
    anthropic: 'Anthropic', bedrock: 'Amazon Bedrock', gemini: 'Google AI Studio (Gemini)',
    openai: 'OpenAI', azure: 'Azure OpenAI',
    vertex_ai: 'Google Vertex AI', litellm_proxy: 'LiteLLM Proxy', ollama: 'Ollama', hosted_vllm: 'vLLM / LM Studio',
  }
  function setMode(p: string, m: 'rented' | 'owned' | 'split') {
    setDraft({ ...overrides, [p]: m === 'rented' ? '' : m === 'owned' ? 'direct' : 50 })
  }
  function setPct(p: string, pct: number) {
    setDraft({ ...overrides, [p]: pct <= 0 ? '' : pct >= 100 ? 'direct' : pct })
  }

  const splitCount = PROVIDERS.filter((p) => mode(overrides[p]) !== 'rented').length
  if (!loaded) return null

  return (
    <Card>
      <div className="mb-3">
        <div className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>⚙️ Engine routing · rented ↔ owned</div>
        <div className="text-[11px] text-muted mt-0.5 max-w-3xl">
          Choose, <b>per provider</b>, which engine serves traffic: the <span className="text-gray-300">rented</span> engine
          (Bifrost, a fast Go sidecar) or our <span className="text-gray-300">owned</span> DirectEngine (in-process, no rented dependency).
          Identical contract + governance either way - this is how a provider is migrated rented→owned <b>incrementally</b>.
          <b className="text-gray-300"> This is a gateway-wide setting</b> - it applies to every client &amp; workspace (an infrastructure
          decision, not per-tenant) and is independent of your aliases.
        </div>
      </div>

      {/* how it works under the hood */}
      <div className="text-[11px] rounded-lg border border-border bg-app px-3 py-2 mb-3" style={{ color: 'var(--color-text-secondary)' }}>
        <b className="text-gray-200">How the split works:</b> every request to a provider is routed independently by weight -
        e.g. <span className="mono">Direct 30%</span> sends ~3 of 10 calls through DirectEngine, the rest through Bifrost. The
        engine that actually served is recorded on each request (see <span className="text-gray-300">engine</span> in Request Logs
        / Analytics), so you can watch parity + error rate as you dial it up. Start at a small % (canary), verify with
        <span className="text-gray-300"> Shadow parity</span> on Engine &amp; Health, then ramp to 100% (Owned).
        <div className="mt-1">To split across <b>models/providers</b> instead of engines, use the alias's weighted fallback targets above (e.g. 80% Anthropic / 20% Gemini).</div>
      </div>

      <div className="grid grid-cols-1 gap-2.5">
        {PROVIDERS.map((p) => {
          const m = mode(overrides[p])
          const pct = splitPct(overrides[p])
          const active = m !== 'rented'
          return (
            <div key={p} className="rounded-lg border px-3 py-2.5 transition-colors"
                 style={active
                   ? { borderColor: 'var(--color-accent)', background: 'var(--color-accent-soft)' }
                   : { borderColor: 'var(--color-border)', background: 'var(--color-app)' }}>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <span className="text-[11px] uppercase tracking-wider font-semibold min-w-[120px]" style={{ color: 'var(--color-text-secondary)' }}>
                  {PROVIDER_LABELS[p] || p}
                </span>
                <div className="flex items-center gap-2">
                  <button onClick={() => checkParity(p)} disabled={parity[p]?.loading}
                          data-testid={`engine-parity-${p}`}
                          className="text-[10px] px-2 py-1 rounded border border-border text-muted hover:text-accent hover:border-accent transition-colors whitespace-nowrap">
                    {parity[p]?.loading ? 'checking…' : 'check parity'}
                  </button>
                  <div className="flex rounded-lg overflow-hidden border border-border" data-testid={`engine-mode-${p}`}>
                    {(['rented', 'split', 'owned'] as const).map((mm) => (
                      <button key={mm} onClick={() => setMode(p, mm)}
                              className={`px-2.5 py-1 text-[11px] ${m === mm ? 'bg-accent text-white' : 'text-muted hover:text-gray-200'}`}
                              data-testid={`engine-mode-${p}-${mm}`}>
                        {mm === 'rented' ? 'Rented (Bifrost)' : mm === 'owned' ? 'Owned (Direct)' : 'Split'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              {m === 'split' && (
                <div className="mt-2 flex items-center gap-3">
                  <input type="range" min={0} max={100} step={5} value={pct}
                         onChange={(e) => setPct(p, Number(e.target.value))}
                         data-testid={`engine-split-${p}`} className="flex-1 accent-accent" />
                  <span className="text-[11px] tabular-nums whitespace-nowrap" style={{ color: 'var(--color-text)' }}>
                    <span className="text-accent font-semibold">{pct}%</span> owned · {100 - pct}% rented
                  </span>
                </div>
              )}
              {/* inline parity: how the SAME prompt flows on rented vs owned, so you
                  can verify before splitting. */}
              {parity[p] && !parity[p].loading && (
                <div className="mt-2 text-[10.5px]" data-testid={`engine-parity-result-${p}`}>
                  {parity[p].err ? (
                    <span className="text-warn">parity: {parity[p].err}</span>
                  ) : parity[p].res ? (() => {
                    const r = parity[p].res!
                    const vColor = r.verdict === 'divergent' || r.verdict === 'error' ? 'var(--color-danger)'
                      : r.verdict === 'moderate' ? 'var(--color-warn)' : 'var(--color-ok)'
                    return (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5" style={{ color: 'var(--color-text-secondary)' }}>
                        <span>rented <b style={{ color: r.bifrost.ok ? 'var(--color-text)' : 'var(--color-danger)' }}>{r.bifrost.ok ? `${Math.round(r.bifrost.latency_ms)}ms` : 'fail'}</b></span>
                        <span>owned <b style={{ color: r.direct.ok ? 'var(--color-text)' : 'var(--color-danger)' }}>{r.direct.ok ? `${Math.round(r.direct.latency_ms)}ms` : 'fail'}</b></span>
                        <span>verdict <b style={{ color: vColor }}>{r.verdict}</b> (sim {r.text_similarity})</span>
                        <span className="text-muted">Δ{Math.round(r.latency_delta_ms)}ms {r.latency_delta_ms < 0 ? 'direct faster' : 'bifrost faster'} · {r.model_id}</span>
                      </div>
                    )
                  })() : null}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Direct-only providers: no Bifrost adapter → always our DirectEngine. Shown
          for completeness so every configured provider is represented here. */}
      <div className="mt-3">
        <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">Direct-only providers (no rented engine available)</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {DIRECT_ONLY.map((p) => (
            <div key={p} className="rounded-lg border border-border bg-app px-3 py-2 flex items-center justify-between gap-2"
                 data-testid={`engine-directonly-${p}`}>
              <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: 'var(--color-text-secondary)' }}>
                {PROVIDER_LABELS[p] || p}
              </span>
              <span className="text-[10px] px-2 py-1 rounded border whitespace-nowrap"
                    style={{ borderColor: 'var(--color-ok)', color: 'var(--color-ok)' }}>Owned (Direct) · always</span>
            </div>
          ))}
        </div>
        <div className="text-[10px] text-muted mt-1">
          Bifrost has no adapter for these, so our DirectEngine serves them end-to-end - nothing to toggle.
        </div>
      </div>

      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
        <span className="text-[11px] text-muted">
          {splitCount === 0 ? 'All dual-engine providers on the rented Bifrost engine.'
            : `${splitCount} provider${splitCount > 1 ? 's' : ''} routing some/all traffic to the owned engine.`}
        </span>
        <button className="btn-primary text-xs" onClick={save} disabled={busy} data-testid="engine-override-save">
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </Card>
  )
}
