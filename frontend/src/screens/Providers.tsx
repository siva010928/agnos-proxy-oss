// Admin → Providers screen (rebuilt for WAVE 16-UX-2).
// - Provider editor uses the shared ProviderEditor (single source of truth in
//   PROVIDER_SPEC; password reveal toggle; URL/region validation; per-provider
//   field set; isolated-creds Test that hard-blocks Save until it passes).
// - Edit-existing affordance: rows now open the editor with creds blank
//   (creds are write-only at the API; admins can update only by re-entering).
// - Row ⋯ menu uses the portal RowMenu so it never gets clipped.
// - ConfirmModal for delete.

import { motion, AnimatePresence } from 'framer-motion'
import { Loader2, Plus, Server } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { admin } from '../api/client'
import { useWorkspaces } from '../lib/api'
import { WorkspaceBrowser } from '../components/WorkspaceBrowser'
import type { ProviderType } from '../api/types'
import {
  ConfirmModal, Modal, ProviderEditor, ProviderEditorState, RowMenu,
  emptyProviderState,
} from '../components/editors'
import { PROVIDER_SPECS } from '../components/editors/PROVIDER_SPEC'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Pill, ProviderBadge, Skeleton } from '../components/ui'
import { ClientWorkspacePicker } from '../components/ClientWorkspacePicker'
import { OnboardingTrail } from '../components/OnboardingTrail'

interface ProviderRow {
  id: number
  provider: string
  config: Record<string, any>
  bifrost_key_name: string | null
  key_version: number
  updated_at: string | null
}

export function Providers() {
  const ws = useWorkspaces()
  const [searchParams] = useSearchParams()
  // Guided onboarding deep-link: /admin/providers?workspace=ws-x&onboarding=1
  const urlWorkspace = searchParams.get('workspace')
  const onboarding = searchParams.get('onboarding') === '1'
  const [selected, setSelected] = useState<string | null>(() => urlWorkspace)
  const [selectedClient, setSelectedClient] = useState<string | null>(null)
  const [providers, setProviders] = useState<ProviderRow[]>([])
  const [loading, setLoading] = useState(false)

  // Editor state (one editor for both add + re-credential)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorMode, setEditorMode] = useState<'add' | 'rotate'>('add')
  const [editorState, setEditorState] = useState<ProviderEditorState>(emptyProviderState('bedrock'))
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState<ProviderRow | null>(null)
  // Config-only settings editor (no credential re-entry, no Test gate)
  const [settingsRow, setSettingsRow] = useState<ProviderRow | null>(null)
  const [settingsConfig, setSettingsConfig] = useState<Record<string, string>>({})
  const [savingSettings, setSavingSettings] = useState(false)

  function openSettings(row: ProviderRow) {
    const cfg: Record<string, string> = {}
    for (const [k, v] of Object.entries(row.config || {})) cfg[k] = v == null ? '' : String(v)
    setSettingsConfig(cfg)
    setSettingsRow(row)
  }
  async function saveSettings() {
    if (!settingsRow || !selected) return
    setSavingSettings(true)
    try {
      await admin.updateProviderConfig(selected, settingsRow.provider, settingsConfig)
      toastOk(`Updated ${settingsRow.provider} settings`)
      setSettingsRow(null)
      await load(selected)
    } catch (e: any) {
      toastError(e?.message || 'update failed')
    } finally {
      setSavingSettings(false)
    }
  }

  // NO force-select: the default is "All clients → All workspaces" (selected=null),
  // which shows the cross-workspace OVERVIEW below. Picking a workspace drills in.
  // (Previously an effect snapped `selected` to the first workspace, which made
  // "All workspaces" impossible to keep - the exact bug being fixed here.)

  // ── All-workspaces overview: when no specific workspace is chosen, browse the
  //    providers configured across every in-scope workspace (scoped by client). ──
  const scopeWs = useMemo(() => {
    const all = ws.data?.workspaces || []
    return selectedClient ? all.filter((w: any) => w.client_id === selectedClient) : all
  }, [ws.data, selectedClient])
  const [overview, setOverview] = useState<{ id: string; display: string; clientId?: string | null; providers: ProviderRow[] }[]>([])
  const [ovLoading, setOvLoading] = useState(false)
  const clientNames = useMemo(() => {
    const m: Record<string, string> = {}
    for (const w of (ws.data?.workspaces || [])) if (w.client_id) m[w.client_id] = w.client_id
    return m
  }, [ws.data])
  useEffect(() => {
    if (selected) return
    let cancelled = false
    setOvLoading(true)
    Promise.all((scopeWs as any[]).map(async (w) => {
      const base = { id: w.workspace_id, display: w.display_name || w.name || w.workspace_id, clientId: w.client_id }
      try {
        const r = await admin.listProviders(w.workspace_id)
        return { ...base, providers: (r.providers || []) as ProviderRow[] }
      } catch {
        return { ...base, providers: [] as ProviderRow[] }
      }
    })).then((rows) => { if (!cancelled) setOverview(rows) })
      .finally(() => { if (!cancelled) setOvLoading(false) })
    return () => { cancelled = true }
  }, [selected, scopeWs])

  // Deep-link: seed the client filter to match the preselected workspace so the
  // ClientWorkspacePicker shows it (mirrors the Routing screen).
  const [clientSeeded, setClientSeeded] = useState(false)
  useEffect(() => {
    const list = ws.data?.workspaces || []
    if (urlWorkspace && !clientSeeded && list.length) {
      const match = list.find((w: any) => w.workspace_id === selected)
      if (match?.client_id) setSelectedClient(match.client_id)
      setClientSeeded(true)
    }
  }, [ws.data, selected, urlWorkspace, clientSeeded])

  const load = async (id: string) => {
    setLoading(true)
    try {
      const r = await admin.listProviders(id)
      setProviders((r.providers || []) as ProviderRow[])
    } catch (e: any) {
      toastError(e?.message || 'failed to load providers')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { if (selected) load(selected) }, [selected])

  const wsObj = (ws.data?.workspaces || []).find((w: any) => w.workspace_id === selected)

  function openAdd() {
    setEditorMode('add')
    setEditorState(emptyProviderState('bedrock'))
    setEditorOpen(true)
  }
  function openRotate(row: ProviderRow) {
    setEditorMode('rotate')
    const initial = emptyProviderState(row.provider as ProviderType)
    // Pre-fill non-secret config from the existing row (region/endpoint/api_version)
    initial.config = { ...(row.config || {}) }
    setEditorState(initial)
    setEditorOpen(true)
  }

  async function save() {
    if (!selected) return
    if (editorState.testStatus !== 'pass') {
      toastError('Run Test Connection first - saving with un-tested creds is not allowed.')
      return
    }
    setBusy(true)
    try {
      await admin.addProvider(selected, {
        provider: editorState.provider,
        credentials: editorState.credentials as any,
        config: editorState.config,
      } as any)
      toastOk(
        editorMode === 'add'
          ? `Provider ${editorState.provider} added`
          : `Provider ${editorState.provider} credentials rotated`
      )
      setEditorOpen(false)
      await load(selected)
    } catch (e: any) {
      toastError(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      {onboarding && (
        <OnboardingTrail step={2} workspace={selected}
          next={selected ? { label: 'Next: set up routing', to: `/admin/routing?workspace=${encodeURIComponent(selected)}&onboarding=1` } : undefined}
          nextEnabled={providers.length > 0}
          nextHint={providers.length === 0 ? 'Add at least one provider to continue.' : undefined} />
      )}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Admin · Providers</h1>
          <p className="text-muted text-sm">
            Encrypted at-rest provider credentials, per workspace. Test Connection performs a real
            1-token probe with <span className="text-gray-200">only the credentials you entered</span> -
            no env/IMDS fallback. Save is blocked until Test passes.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ClientWorkspacePicker selectedClient={selectedClient} selectedWorkspace={selected} onClientChange={setSelectedClient} onWorkspaceChange={setSelected} allowAll />
          <button data-testid="provider-new" className="btn-primary" disabled={!selected} onClick={openAdd}>
            <Plus size={16} /> Add provider
          </button>
        </div>
      </div>

      <Card className="p-0">
        {loading ? (
          <div className="p-6"><Skeleton h={120} /></div>
        ) : !selected ? (
          // All-workspaces browse: one row per in-scope workspace with its
          // providers; click to drill in and manage. This is what "All workspaces"
          // now shows (the default), instead of snapping to one workspace.
          <div className="p-3" data-testid="provider-overview">
            <div className="text-[11px] text-muted px-2 pb-1">
              {selectedClient ? 'Workspaces for this client' : 'All workspaces, grouped by client'} · star favorites, search, click to manage credentials.
            </div>
            {ovLoading ? (
              <div className="p-6"><Skeleton h={120} /></div>
            ) : (
              <WorkspaceBrowser
                testIdPrefix="provider-wsb"
                clientNames={clientNames}
                onPick={(id) => setSelected(id)}
                items={overview.map((o) => ({
                  id: o.id, display: o.display, clientId: o.clientId,
                  right: o.providers.length === 0
                    ? <span className="text-[11px] text-muted italic">no providers</span>
                    : o.providers.map((p) => (
                        <span key={p.id} className="text-[10px] px-2 py-0.5 rounded border border-border text-gray-300">{p.provider}</span>
                      )),
                }))}
              />
            )}
          </div>
        ) : providers.length === 0 ? (
          <div className="p-10">
            <EmptyState
              icon={<Server size={32} />}
              title={`No providers in ${wsObj?.display_name || selected}`}
              hint={
                <>
                  Add at least one provider before creating components or routing aliases.
                  Most workspaces start with one cloud provider (Bedrock or Azure) and one direct
                  API (Anthropic / Gemini / OpenAI) for fallback.
                </>
              }
              cta={
                <button className="btn-primary" onClick={openAdd}
                        data-testid="provider-new-empty">
                  <Plus size={14} /> Add your first provider
                </button>
              }
            />
          </div>
        ) : (
          <div className="divide-y divide-border" data-testid="provider-list">
            {providers.map((p) => (
              <div
                key={p.id}
                className="px-5 py-3 flex items-center gap-3 hover:bg-elevated/30"
                data-testid={`provider-row-${p.provider}`}
              >
                <ProviderBadge provider={p.provider} />
                <span className="text-sm text-gray-100 capitalize flex-1">{p.provider}</span>
                {p.config?.region && <Pill color="#A78BFA">region {p.config.region}</Pill>}
                {p.config?.endpoint && (
                  <Pill color="#A78BFA">{String(p.config.endpoint).replace(/^https?:\/\//, '').slice(0, 40)}</Pill>
                )}
                {p.config?.api_version && <Pill color="#A78BFA">v {p.config.api_version}</Pill>}
                <Pill color="#60A5FA">timeout {p.config?.request_timeout_seconds || 120}s</Pill>
                <Pill color="#34D399">key v{p.key_version}</Pill>
                <RowMenu
                  testId={`provider-menu-${p.provider}`}
                  items={[
                    {
                      label: 'Edit settings',
                      onSelect: () => openSettings(p),
                      testId: `provider-settings-${p.provider}`,
                    },
                    {
                      label: 'Rotate credentials',
                      onSelect: () => openRotate(p),
                      testId: `provider-rotate-${p.provider}`,
                    },
                    {
                      label: 'Delete',
                      danger: true,
                      onSelect: () => setConfirmDel(p),
                      testId: `provider-delete-${p.provider}`,
                    },
                  ]}
                />
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        title={editorMode === 'add' ? 'Add provider' : `Rotate credentials · ${editorState.provider}`}
        subtitle={`Workspace ${selected}. Credentials are encrypted at rest using GATEWAY_MASTER_KEY.`}
        size="lg"
        testId="provider-editor"
        footer={
          <>
            <button type="button" className="btn-ghost text-sm" onClick={() => setEditorOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary text-sm disabled:opacity-50"
              disabled={busy || editorState.testStatus !== 'pass'}
              onClick={save}
              data-testid="provider-save"
              title={editorState.testStatus !== 'pass' ? 'Test Connection must pass first' : undefined}
            >
              {busy ? <><Loader2 size={14} className="animate-spin" /> Saving…</> : 'Save'}
            </button>
          </>
        }
      >
        <ProviderEditor
          state={editorState}
          onChange={setEditorState}
          showAliasField={false}
          testIdPrefix="provider"
        />
      </Modal>

      {/* Config-only settings editor - edit region/endpoint/version/timeout
          without re-entering credentials (no Test gate). */}
      <Modal
        open={!!settingsRow}
        onClose={() => setSettingsRow(null)}
        title={settingsRow ? `Edit settings · ${settingsRow.provider}` : 'Edit settings'}
        subtitle={`Workspace ${selected}. Non-secret config - credentials are unchanged.`}
        size="md"
        testId="provider-settings-editor"
        footer={
          <>
            <button type="button" className="btn-ghost text-sm" onClick={() => setSettingsRow(null)} disabled={savingSettings}>
              Cancel
            </button>
            <button type="button" className="btn-primary text-sm disabled:opacity-50"
              disabled={savingSettings} onClick={saveSettings} data-testid="provider-settings-save">
              {savingSettings ? <><Loader2 size={14} className="animate-spin" /> Saving…</> : 'Save settings'}
            </button>
          </>
        }
      >
        {settingsRow && (
          <div className="space-y-4">
            {PROVIDER_SPECS[settingsRow.provider as ProviderType].fields
              .filter((f) => f.destination === 'config')
              .map((f) => (
                <div key={f.key}>
                  <div className="text-sm text-gray-200 font-medium mb-1.5">{f.label}</div>
                  {f.type === 'select' ? (
                    <select className="input text-xs" value={settingsConfig[f.key] || ''}
                      onChange={(e) => setSettingsConfig((c) => ({ ...c, [f.key]: e.target.value }))}
                      data-testid={`provider-settings-${f.key}`}>
                      <option value="">Select…</option>
                      {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : (
                    <input className="input mono text-xs" placeholder={f.placeholder}
                      value={settingsConfig[f.key] || ''}
                      onChange={(e) => setSettingsConfig((c) => ({ ...c, [f.key]: e.target.value }))}
                      data-testid={`provider-settings-${f.key}`} />
                  )}
                  {f.hint && <div className="text-[11px] text-muted mt-1">{f.hint}</div>}
                </div>
              ))}
          </div>
        )}
      </Modal>

      <ConfirmModal
        open={!!confirmDel}
        onCancel={() => setConfirmDel(null)}
        title={`Delete provider '${confirmDel?.provider}'?`}
        message={
          <>
            The encrypted credential is removed from the vault.
            <span className="text-warn">Any chat alias still pointing at this provider will start failing.</span>
          </>
        }
        identifier={confirmDel ? `provider '${confirmDel.provider}' in workspace '${selected}'` : null}
        confirmLabel="Delete provider"
        danger
        onConfirm={async () => {
          if (!confirmDel || !selected) return
          await withToast(async () => {
            await admin.deleteProvider(selected, confirmDel.provider)
            await load(selected)
          })
          setConfirmDel(null)
        }}
        testId="provider-confirm-delete"
      />
    </div>
  )
}
