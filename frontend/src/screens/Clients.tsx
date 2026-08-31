// Admin → Clients screen (WAVE 20 TRACK 5).
// Simple CRUD for the Client entity (the enterprise tenant root). Each Client
// has a name, a monthly cross-workspace budget cap, RPM/TPM ceiling, and a
// required_headers list. Workspaces are assigned to a Client via the workspace
// editor's client_id selector or the onboarding wizard.

import { useEffect, useState } from 'react'
import { Loader2, Plus, Building2 } from 'lucide-react'
import { api } from '../lib/api'
import { useWorkspaces } from '../lib/api'
import {
  ConfirmModal, Field, Modal, RowMenu,
} from '../components/editors'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Skeleton, Pill } from '../components/ui'



interface ClientRow {
  client_id: string
  name: string
  budgets: { client_usd?: number; user_usd?: number }
  rate_limits: { rpm?: number; tpm?: number }
  required_headers: string[]
  notes: string
}

export function Clients() {
  const [clients, setClients] = useState<ClientRow[]>([])
  const [loading, setLoading] = useState(true)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<ClientRow | null>(null)
  const [confirmDel, setConfirmDel] = useState<ClientRow | null>(null)
  const wsHook = useWorkspaces()
  const childWorkspaces = (clientId: string) =>
    (wsHook.data?.workspaces || []).filter((w: any) => w.client_id === clientId)

  const load = async () => {
    setLoading(true)
    try {
      const r = await api('/admin/clients')
      setClients(r.clients || [])
    } catch (e: any) {
      toastError(e?.message || 'failed to load clients')
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  function openNew() { setEditing(null); setEditorOpen(true) }
  function openEdit(c: ClientRow) { setEditing(c); setEditorOpen(true) }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Admin · Clients</h1>
          <p className="text-muted text-sm">
            A Client is an enterprise tenant (e.g. "NovaTech"). Workspaces belong to a Client;
            the Client owns the cross-workspace monthly budget cap and rate-limit ceiling.
          </p>
        </div>
        <button className="btn-primary" onClick={openNew} data-testid="client-new">
          <Plus size={16} /> New client
        </button>
      </div>

      <Card className="p-0">
        {loading ? (
          <div className="p-6"><Skeleton h={100} /></div>
        ) : clients.length === 0 ? (
          <div className="p-10">
            <EmptyState
              icon={<Building2 size={32} />}
              title="No clients yet"
              hint="Create a Client before onboarding workspaces. Each workspace must belong to exactly one Client."
              cta={<button className="btn-primary" onClick={openNew}><Plus size={14} /> Create client</button>}
            />
          </div>
        ) : (
          <div className="divide-y divide-border" data-testid="client-list">
            {clients.map((c) => (
              <div key={c.client_id}
                   className="px-5 py-3 flex items-center gap-3 hover:bg-elevated/30"
                   data-testid={`client-row-${c.client_id}`}>
                <Building2 size={16} className="text-accent shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-100 font-medium truncate">{c.name || c.client_id}</div>
                  <div className="text-[11px] text-muted mono">{c.client_id}</div>
                </div>
                {c.budgets?.client_usd != null && (
                  <Pill color="#FBBF24">${Number(c.budgets.client_usd).toLocaleString()}/mo cap</Pill>
                )}
                {c.rate_limits?.rpm && (
                  <Pill color="#A78BFA">{c.rate_limits.rpm} rpm</Pill>
                )}
                <RowMenu
                  testId={`client-menu-${c.client_id}`}
                  items={[
                    { label: 'Edit', onSelect: () => openEdit(c), testId: `client-edit-${c.client_id}` },
                    { label: 'Delete', danger: true, onSelect: () => setConfirmDel(c), testId: `client-delete-${c.client_id}` },
                  ]}
                />
              </div>
            ))}
          </div>
        )}
      </Card>

      {editorOpen && (
        <ClientEditor
          edit={editing}
          onClose={() => setEditorOpen(false)}
          onSaved={async () => { setEditorOpen(false); await load() }}
        />
      )}

      <ConfirmModal
        open={!!confirmDel}
        onCancel={() => setConfirmDel(null)}
        title={`Delete client '${confirmDel?.client_id}'?`}
        message={
          confirmDel && childWorkspaces(confirmDel.client_id).length > 0 ? (
            <>
              <span className="text-warn font-medium">This will also permanently delete {childWorkspaces(confirmDel.client_id).length} workspace(s)</span> and everything inside them - providers, API keys, components, guardrail rules/profiles, and their Bifrost keys:
              <ul className="mt-2 ml-4 list-disc text-[11.5px] text-muted max-h-32 overflow-y-auto">
                {childWorkspaces(confirmDel.client_id).map((w: any) => (
                  <li key={w.workspace_id}><span className="mono text-gray-300">{w.workspace_id}</span></li>
                ))}
              </ul>
              <div className="mt-2 text-[11px]">Request logs &amp; audit history are retained. This cannot be undone.</div>
            </>
          ) : wsHook.isLoading || !wsHook.data ? (
            <>
              <span className="text-warn font-medium">This also deletes every workspace under this client</span> and everything inside them (providers, keys, components, guardrails). Request logs &amp; audit history are retained. This cannot be undone.
            </>
          ) : (
            'No workspaces are attached - this removes only the client. Request logs & audit history are retained.'
          )
        }
        confirmLabel={confirmDel && childWorkspaces(confirmDel.client_id).length > 0
          ? `Delete client + ${childWorkspaces(confirmDel.client_id).length} workspace(s)` : 'Delete client'}
        danger
        onConfirm={async () => {
          if (!confirmDel) return
          await withToast(async () => {
            await api(`/admin/clients/${confirmDel.client_id}?cascade=true`, { method: 'DELETE' })
            await load()
            await wsHook.refetch?.()
          })
          setConfirmDel(null)
        }}
        testId="client-confirm-delete"
      />
    </div>
  )
}

function ClientEditor({ edit, onClose, onSaved }:
  { edit: ClientRow | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const isEdit = !!edit
  const [clientId, setClientId] = useState(edit?.client_id || '')
  const [name, setName] = useState(edit?.name || '')
  const [clientUsd, setClientUsd] = useState<number | ''>(edit?.budgets?.client_usd ?? '')
  const [userUsd, setUserUsd] = useState<number | ''>(edit?.budgets?.user_usd ?? '')
  const [rpm, setRpm] = useState<number | ''>(edit?.rate_limits?.rpm ?? '')
  const [tpm, setTpm] = useState<number | ''>(edit?.rate_limits?.tpm ?? '')
  const [requiredHeaders, setRequiredHeaders] = useState(
    (edit?.required_headers || []).join(', ')
  )
  const [notes, setNotes] = useState(edit?.notes || '')
  const [busy, setBusy] = useState(false)

  const valid = !!(isEdit || clientId.trim())

  async function save() {
    setBusy(true)
    try {
      const body: any = {
        client_id: clientId.trim(),
        name: name.trim() || clientId.trim(),
        budgets: {
          ...(clientUsd !== '' ? { client_usd: Number(clientUsd) } : {}),
          ...(userUsd !== '' ? { user_usd: Number(userUsd) } : {}),
        },
        rate_limits: {
          ...(rpm !== '' ? { rpm: Number(rpm) } : {}),
          ...(tpm !== '' ? { tpm: Number(tpm) } : {}),
        },
        required_headers: requiredHeaders.split(',').map((h) => h.trim()).filter(Boolean),
        notes,
      }
      if (isEdit) {
        await api(`/admin/clients/${edit!.client_id}`, {
          method: 'PATCH', 
          body: JSON.stringify(body),
        })
      } else {
        await api('/admin/clients', {
          method: 'POST', 
          body: JSON.stringify(body),
        })
      }
      toastOk(isEdit ? 'Client updated' : 'Client created')
      await onSaved()
    } catch (e: any) {
      toastError(e?.message || 'save failed')
    } finally { setBusy(false) }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={isEdit ? `Edit client · ${edit!.client_id}` : 'New client'}
      subtitle="The enterprise tenant root. Workspaces belong to a Client."
      size="md"
      testId="client-editor"
      footer={
        <>
          <button className="btn-ghost text-sm" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary text-sm" disabled={!valid || busy}
                  onClick={save} data-testid="client-save">
            {busy ? <><Loader2 size={14} className="animate-spin" /> Saving…</> : 'Save'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Client ID (slug)" hint="Lowercase letters/digits/hyphens. Cannot be changed after creation." required>
          <input className="input mono text-xs" value={clientId} disabled={isEdit}
                 onChange={(e) => setClientId(e.target.value.toLowerCase())}
                 placeholder="novatech" data-testid="client-id" />
        </Field>
        <Field label="Display name" hint="Human-friendly label shown in Analytics and the workspace editor.">
          <input className="input text-xs" value={name}
                 onChange={(e) => setName(e.target.value)}
                 placeholder="NovaTech Corporation" data-testid="client-name" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Monthly budget cap (USD)" hint="Cross-workspace ceiling. Requests past this cap return 402.">
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
              <input className="input text-xs pl-6" type="number" step="0.01" min={0}
                     value={clientUsd} onChange={(e) => setClientUsd(e.target.value === '' ? '' : Number(e.target.value))}
                     placeholder="5000" data-testid="client-budget" />
            </div>
          </Field>
          <Field label="Per-user default budget (USD/mo)" hint="Applied to all users unless workspace overrides.">
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
              <input className="input text-xs pl-6" type="number" step="0.01" min={0}
                     value={userUsd} onChange={(e) => setUserUsd(e.target.value === '' ? '' : Number(e.target.value))}
                     placeholder="1000" data-testid="client-user-budget" />
            </div>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Client-wide RPM ceiling" hint="Requests/min across all workspaces. First scope in the rate-limit hierarchy.">
            <input className="input text-xs" type="number" min={0}
                   value={rpm} onChange={(e) => setRpm(e.target.value === '' ? '' : Number(e.target.value))}
                   placeholder="5000" data-testid="client-rpm" />
          </Field>
          <Field label="Client-wide TPM ceiling" hint="Tokens/min across all workspaces.">
            <input className="input text-xs" type="number" min={0}
                   value={tpm} onChange={(e) => setTpm(e.target.value === '' ? '' : Number(e.target.value))}
                   placeholder="5000000" data-testid="client-tpm" />
          </Field>
        </div>
        <Field label="Required headers" hint="Comma-separated list of headers every request must carry (e.g. X-Gateway-Component). Enforced by the governance flow.">
          <input className="input mono text-xs" value={requiredHeaders}
                 onChange={(e) => setRequiredHeaders(e.target.value)}
                 placeholder="X-Gateway-Component" data-testid="client-required-headers" />
        </Field>
        <Field label="Notes" hint="Internal notes (not exposed to API consumers).">
          <input className="input text-xs" value={notes}
                 onChange={(e) => setNotes(e.target.value)}
                 placeholder="e.g. Payments team; onboarded 2026-Q1" data-testid="client-notes" />
        </Field>
      </div>
    </Modal>
  )
}
