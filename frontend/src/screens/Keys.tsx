// Admin → API Keys screen (rebuilt for WAVE 16-UX-2).
// - Row ⋯ menu uses the portal RowMenu so it's never clipped by overflow-x-auto.
// - Issue dialog uses the shared KeyIssueForm: label + roles + real <input
//   type="date"> + explicit "no expiry" checkbox. expires_at is rejected
//   server-side if unparseable or in the past (no more silent forever-keys).
// - Rotate / Disable use the shared ConfirmModal with a key prefix identifier.
// - Plaintext-shown-once dialog stays; Copy uses the Clipboard API.

import { motion, AnimatePresence } from 'framer-motion'
import {
  AlertTriangle, ClipboardCopy, KeyRound, Loader2, Plus, RotateCw, ShieldOff,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { admin } from '../api/client'
import { useWorkspaces } from '../lib/api'
import {
  ConfirmModal, KeyIssueForm, KeyIssueValue, Modal, RowMenu, emptyKeyIssue,
  keyIssueValid,
} from '../components/editors'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Pill, Skeleton } from '../components/ui'
import { ClientWorkspacePicker } from '../components/ClientWorkspacePicker'
import { WorkspaceBrowser } from '../components/WorkspaceBrowser'

interface ApiKey {
  id: number
  workspace_id: string
  prefix: string
  disabled: boolean
  status: string
  roles: string[]
  expires_at: string | null
  created_at: string | null
}

const STATUS_COLOR: Record<string, string> = {
  active: '#34D399', disabled: '#6B7280', expired: '#F87171',
}

export function Keys() {
  const ws = useWorkspaces()
  const [searchParams] = useSearchParams()
  // Deep-link support: /app/admin/keys?workspace=ws-x pre-selects that workspace.
  const urlWorkspace = searchParams.get('workspace')
  const [selected, setSelected] = useState<string | null>(() => urlWorkspace)
  const [selectedClient, setSelectedClient] = useState<string | null>(null)
  // Only narrow the client filter for an explicit deep-link; a plain visit
  // should keep "All clients" so every workspace stays visible.
  const [clientSeeded, setClientSeeded] = useState(false)
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(false)

  // Issue flow state
  const [issuing, setIssuing] = useState(false)
  const [issueValue, setIssueValue] = useState<KeyIssueValue>(emptyKeyIssue())
  const [issueBusy, setIssueBusy] = useState(false)

  // Plaintext-shown-once modal
  const [plaintext, setPlaintext] = useState<{ value: string; label: string } | null>(null)

  // Confirm flows
  const [rotateRow, setRotateRow] = useState<ApiKey | null>(null)
  const [disableRow, setDisableRow] = useState<ApiKey | null>(null)

  // NO force-select: the default is "All clients → All workspaces" (selected=null),
  // which shows the cross-workspace overview below. Picking a workspace drills in.
  // (Only a URL deep-link narrows the client filter to match the workspace.)
  useEffect(() => {
    const list = ws.data?.workspaces || []
    if (urlWorkspace && !clientSeeded && selected && list.length) {
      const match = list.find((w: any) => w.workspace_id === selected)
      if (match?.client_id) setSelectedClient(match.client_id)
      setClientSeeded(true)
    }
  }, [ws.data, selected, urlWorkspace, clientSeeded])

  // ── All-workspaces overview: browse keys across every in-scope workspace
  //    (scoped by the selected client) when no specific workspace is chosen. ──
  const scopeWs = useMemo(() => {
    const all = ws.data?.workspaces || []
    return selectedClient ? all.filter((w: any) => w.client_id === selectedClient) : all
  }, [ws.data, selectedClient])
  const [overview, setOverview] = useState<{ id: string; display: string; clientId?: string | null; keys: ApiKey[] }[]>([])
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
        const r = await admin.listKeys(w.workspace_id)
        return { ...base, keys: (r.keys || []) as ApiKey[] }
      } catch {
        return { ...base, keys: [] as ApiKey[] }
      }
    })).then((rows) => { if (!cancelled) setOverview(rows) })
      .finally(() => { if (!cancelled) setOvLoading(false) })
    return () => { cancelled = true }
  }, [selected, scopeWs])

  const load = async (id: string) => {
    setLoading(true)
    try {
      const r = await admin.listKeys(id)
      setKeys((r.keys || []) as ApiKey[])
    } catch (e: any) {
      toastError(e?.message || 'failed to load keys')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { if (selected) load(selected) }, [selected])

  const wsObj = (ws.data?.workspaces || []).find((w: any) => w.workspace_id === selected)

  function openIssue() {
    setIssueValue(emptyKeyIssue())
    setIssuing(true)
  }

  async function submitIssue() {
    if (!selected) return
    if (!keyIssueValid(issueValue)) return
    setIssueBusy(true)
    try {
      const r = await admin.issueKey(selected, {
        roles: issueValue.roles,
        expires_at: issueValue.expires_at || null,
      })
      setIssuing(false)
      setPlaintext({ value: r.api_key, label: issueValue.label })
      toastOk('Key issued')
      await load(selected)
    } catch (e: any) {
      toastError(e?.message || 'issue failed')
    } finally {
      setIssueBusy(false)
    }
  }

  async function doRotate(row: ApiKey) {
    if (!selected) return
    try {
      const r = await admin.rotateKey(selected, row.id)
      setPlaintext({ value: r.api_key, label: `rotated · ${row.prefix}` })
      toastOk('Key rotated; old key invalidated')
      await load(selected)
    } catch (e: any) {
      toastError(e?.message || 'rotate failed')
    } finally {
      setRotateRow(null)
    }
  }

  async function doDisable(row: ApiKey) {
    if (!selected) return
    try {
      await admin.disableKey(selected, row.id)
      toastOk('Key disabled')
      await load(selected)
    } catch (e: any) {
      toastError(e?.message || 'disable failed')
    } finally {
      setDisableRow(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Admin · API Keys</h1>
          <p className="text-muted text-sm">
            Workspace-scoped bearer keys. Issue surfaces the plaintext value
            <span className="text-warn"> exactly once</span> - copy it and store it somewhere safe.
            Use Rotate to replace, Disable to invalidate immediately.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ClientWorkspacePicker selectedClient={selectedClient} selectedWorkspace={selected} onClientChange={setSelectedClient} onWorkspaceChange={setSelected} allowAll />
          <button
            data-testid="key-issue"
            className="btn-primary"
            disabled={!selected}
            onClick={openIssue}
          >
            <Plus size={16} /> Issue key
          </button>
        </div>
      </div>

      <Card className="p-0">
        {loading ? (
          <div className="p-6"><Skeleton h={120} /></div>
        ) : !selected ? (
          // All-workspaces browse: one row per in-scope workspace with its key
          // count; click to drill in and manage. This is the default now.
          <div className="p-3" data-testid="key-overview">
            <div className="text-[11px] text-muted px-2 pb-1">
              {selectedClient ? 'Workspaces for this client' : 'All workspaces, grouped by client'} · star favorites, search, click to manage keys.
            </div>
            {ovLoading ? (
              <div className="p-6"><Skeleton h={120} /></div>
            ) : (
              <WorkspaceBrowser
                testIdPrefix="key-wsb"
                clientNames={clientNames}
                onPick={(id) => setSelected(id)}
                items={overview.map((o) => {
                  const active = o.keys.filter((k) => (k as any).enabled !== false).length
                  return {
                    id: o.id, display: o.display, clientId: o.clientId,
                    right: (
                      <span className="text-[11px] text-gray-300 whitespace-nowrap">
                        {o.keys.length === 0 ? <span className="text-muted italic">no keys</span>
                          : <>{o.keys.length} key{o.keys.length > 1 ? 's' : ''}{active !== o.keys.length ? ` · ${active} active` : ''}</>}
                      </span>
                    ),
                  }
                })}
              />
            )}
          </div>
        ) : keys.length === 0 ? (
          <div className="p-10">
            <EmptyState
              icon={<KeyRound size={32} />}
              title={`No keys for ${wsObj?.display_name || selected}`}
              hint={"Issue a key for each consumer (CI pipeline, dev laptop, internal service). Each key carries roles and an optional expiry - past dates and unparseable values are rejected."}
              cta={
                <button className="btn-primary" onClick={openIssue}
                        data-testid="key-issue-empty">
                  <Plus size={14} /> Issue your first key
                </button>
              }
            />
          </div>
        ) : (
          <div className="divide-y divide-border" data-testid="key-list">
            {keys.map((k) => {
              return (
                <div
                  key={k.id}
                  className="px-5 py-3 flex items-center gap-3 hover:bg-elevated/30"
                  data-testid={`key-row-${k.id}`}
                >
                  <KeyRound size={14} className="text-muted shrink-0" />
                  <span className="mono text-xs text-gray-100 flex-1 truncate">{k.prefix}</span>
                  <Pill color={STATUS_COLOR[k.status] || '#6B7280'}>{k.status}</Pill>
                  <div className="flex items-center gap-1">
                    {k.roles.map((r) => (
                      <Pill key={r} color={r === 'admin' ? '#FBBF24' : '#A78BFA'}>{r}</Pill>
                    ))}
                  </div>
                  <span className="text-[10.5px] text-muted hidden sm:inline">
                    {k.expires_at ? `exp ${k.expires_at.slice(0, 10)}` : 'no expiry'}
                  </span>
                  <RowMenu
                    testId={`key-menu-${k.id}`}
                    items={[
                      {
                        label: 'Rotate',
                        icon: <RotateCw size={13} />,
                        onSelect: () => setRotateRow(k),
                        disabled: k.disabled,
                        testId: `key-rotate-${k.id}`,
                        hint: k.disabled ? 'Disabled keys cannot be rotated' : undefined,
                      },
                      {
                        label: 'Disable',
                        icon: <ShieldOff size={13} />,
                        onSelect: () => setDisableRow(k),
                        danger: true,
                        disabled: k.disabled,
                        testId: `key-disable-${k.id}`,
                      },
                    ]}
                  />
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* Issue modal */}
      <Modal
        open={issuing}
        onClose={() => setIssuing(false)}
        title="Issue API key"
        subtitle={`Workspace ${selected}`}
        size="md"
        testId="key-issue-modal"
        footer={
          <>
            <button className="btn-ghost text-sm" onClick={() => setIssuing(false)} disabled={issueBusy}>
              Cancel
            </button>
            <button
              className="btn-primary text-sm disabled:opacity-50"
              disabled={issueBusy || !keyIssueValid(issueValue)}
              onClick={submitIssue}
              data-testid="key-issue-confirm"
            >
              {issueBusy ? <><Loader2 size={14} className="animate-spin" /> Issuing…</> : 'Issue key'}
            </button>
          </>
        }
      >
        <KeyIssueForm value={issueValue} onChange={setIssueValue} testIdPrefix="key" />
      </Modal>

      {/* Plaintext shown-once modal */}
      <Modal
        open={!!plaintext}
        onClose={() => setPlaintext(null)}
        title="API key issued"
        size="md"
        testId="plaintext-modal"
        footer={
          <button
            className="btn-primary text-sm"
            onClick={() => setPlaintext(null)}
            data-testid="plaintext-close"
          >
            I've saved it
          </button>
        }
      >
        <div className="space-y-3">
          <div className="bg-warn/10 border border-warn/40 rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle size={14} className="text-warn shrink-0 mt-0.5" />
            <div className="text-[12px] text-gray-200">
              <div className="font-semibold">This is the only time this key is visible.</div>
              <div className="text-muted">Only the SHA-256 is stored. If you lose it, rotate or issue a new one.</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <code
              className="flex-1 mono text-xs bg-app border border-border rounded-lg px-3 py-2 break-all text-gray-100"
              data-testid="plaintext-value"
            >
              {plaintext?.value}
            </code>
            <button
              type="button"
              className="btn-primary text-xs"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(plaintext?.value || '')
                  toastOk('Copied')
                } catch {
                  toastError('Copy failed - select and copy manually')
                }
              }}
              data-testid="plaintext-copy"
            >
              <ClipboardCopy size={12} /> Copy
            </button>
          </div>
          {plaintext?.label && (
            <div className="text-[11px] text-muted">Label: {plaintext.label}</div>
          )}
        </div>
      </Modal>

      <ConfirmModal
        open={!!rotateRow}
        onCancel={() => setRotateRow(null)}
        title="Rotate API key?"
        message={
          <>
            A new plaintext key will be generated and shown once.
            The current key will stop working <span className="text-danger">immediately</span> - make sure
            consumers can be updated.
          </>
        }
        identifier={rotateRow ? `prefix ${rotateRow.prefix} · roles [${rotateRow.roles.join(', ')}]` : null}
        confirmLabel="Rotate key"
        danger
        onConfirm={() => rotateRow && doRotate(rotateRow)}
        testId="confirm-rotate"
      />

      <ConfirmModal
        open={!!disableRow}
        onCancel={() => setDisableRow(null)}
        title="Disable API key?"
        message={
          <>
            The key will stop authenticating immediately. This is reversible only by issuing a new key - there is no "re-enable".
          </>
        }
        identifier={disableRow ? `prefix ${disableRow.prefix} · roles [${disableRow.roles.join(', ')}]` : null}
        confirmLabel="Disable key"
        danger
        onConfirm={() => disableRow && doDisable(disableRow)}
        testId="confirm-disable"
      />
    </div>
  )
}
