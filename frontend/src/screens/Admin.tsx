// Admin → Onboarding wizard (rebuilt for WAVE 16-UX-2).
// Guided 6-step flow that uses ONLY the shared editors - no diverging fields,
// no inline forms, no JSON. The same UI an admin uses on the standalone
// /admin/* pages, presented in order:
//
//   1. Workspace identity (id slug + display name)
//   2. First provider (ProviderEditor; Test must pass to advance)
//   3. Routing aliases (AliasMapEditor on the workspace's chat_models)
//   4. Guardrails (GuardrailsBuilder)
//   5. Limits & budget (QuotaBudgetForm)
//   6. Issue first key (KeyIssueForm)
//   7. Done (plaintext shown once + copy)
//
// Orchestration: createWorkspace → addProvider → patch chat_models → issueKey.
// On any partial failure we surface the FastAPI detail and stop - no half-state.

import { motion, AnimatePresence } from 'framer-motion'
import {
  AlertTriangle, Check, ClipboardCopy, KeyRound, Loader2, Plus,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { admin } from '../api/client'
import { api, useWorkspaces } from '../lib/api'
import {
  AliasMap, AliasMapEditor, Field, GuardrailsBuilder, GuardrailsValue,
  KeyIssueForm, KeyIssueValue, Modal, ProviderEditor, ProviderEditorState,
  QuotaBudgetForm, QuotaBudgetValue, emptyKeyIssue, emptyProviderState,
  keyIssueValid,
} from '../components/editors'
import { toastError, toastOk } from '../components/Toast'
import { Card, Pill, ProviderBadge, SectionTitle } from '../components/ui'

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/
const STEPS = [
  { key: 'identity',  label: 'Workspace' },
  { key: 'provider',  label: 'First provider' },
  { key: 'routing',   label: 'Routing' },
  { key: 'guard',     label: 'Guardrails' },
  { key: 'budget',    label: 'Limits & budget' },
  { key: 'key',       label: 'Issue key' },
] as const

export function Admin() {
  const ws = useWorkspaces()
  const [open, setOpen] = useState(false)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Admin · Onboarding</h1>
          <p className="text-muted text-sm">
            A guided pass through the same editors used elsewhere in the admin console.
            Each step validates before letting you advance - half-cooked workspaces are not creatable.
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={() => setOpen(true)}
          data-testid="open-wizard"
        >
          <Plus size={16} /> Onboard workspace
        </button>
      </div>

      <Card>
        <SectionTitle>Existing workspaces</SectionTitle>
        {(ws.data?.workspaces || []).length === 0 ? (
          <div className="py-6 text-center text-muted text-sm">
            No workspaces yet. Click <span className="text-gray-100">Onboard workspace</span> to get started.
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">workspace</th>
                <th className="th">providers</th>
                <th className="th">guardrails</th>
                <th className="th">budget</th>
              </tr>
            </thead>
            <tbody>
              {(ws.data?.workspaces || []).map((w: any) => {
                const provs = Array.from(
                  new Set(
                    Object.values(w.chat_models || {})
                      .flat()
                      .map((t: any) => t?.provider)
                      .filter(Boolean)
                  )
                )
                return (
                  <tr key={w.workspace_id} className="border-t border-border/60">
                    <td className="td">
                      {w.display_name || w.name || w.workspace_id}
                      <div className="text-[11px] text-muted mono">{w.workspace_id}</div>
                    </td>
                    <td className="td">
                      <div className="flex gap-1 flex-wrap">
                        {(provs as string[]).map((p) => <ProviderBadge key={p} provider={p} />)}
                      </div>
                    </td>
                    <td className="td">
                      {Object.keys(w.guardrails || {}).map((k) => (
                        <Pill key={k} color="#F87171">{k}</Pill>
                      ))}
                    </td>
                    <td className="td mono text-xs">
                      {w.budgets?.workspace_usd != null ? `$${w.budgets.workspace_usd}` : '-'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Card>

      <AnimatePresence>
        {open && (
          <Wizard
            onClose={() => { setOpen(false); ws.refetch() }}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

// ───────────────────────── The wizard ─────────────────────────

type StepIdx = 0 | 1 | 2 | 3 | 4 | 5 | 6   // 6 = success

function Wizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<StepIdx>(0)

  // Step 0 - identity
  const [wsId, setWsId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [clientId, setClientId] = useState('')

  // Load clients for the dropdown
  const [clientsList, setClientsList] = useState<{ client_id: string; name: string }[]>([])
  useEffect(() => {
    api('/admin/clients')
      .then((r: any) => setClientsList(r.clients || []))
      .catch(() => {})
  }, [])

  // Step 1 - first provider (uses ProviderEditor's full state)
  const [providerState, setProviderState] = useState<ProviderEditorState>(
    () => ({ ...emptyProviderState('bedrock'), alias: '' })
  )

  // Step 2 - routing (alias map on the workspace itself).
  // Pre-populated from Step 1 when entering step 2 the first time.
  const [chatModels, setChatModels] = useState<AliasMap>({})
  const [defaultAlias, setDefaultAlias] = useState<string>('')
  const [routingPrefilled, setRoutingPrefilled] = useState(false)

  // Step 3 - guardrails
  const [guardrailsEnabled, setGuardrailsEnabled] = useState(true)
  const [guardrails, setGuardrails] = useState<GuardrailsValue>({ mode: 'block', pii_detection: true })

  // Step 4 - quotas/budgets (workspace-level)
  const [qb, setQb] = useState<QuotaBudgetValue>({
    rpm: 120, tpm: 200_000, workspace_usd: 100, user_usd: 25,
  })

  // Step 5 - key issue
  const [keyValue, setKeyValue] = useState<KeyIssueValue>(emptyKeyIssue())

  // Result + busy
  const [busy, setBusy] = useState(false)
  const [issuedKey, setIssuedKey] = useState<string>('')
  const [err, setErr] = useState<string>('')

  // Pre-fill routing on entering step 2 once
  useEffect(() => {
    if (step !== 2 || routingPrefilled) return
    if (providerState.alias && providerState.modelId) {
      const a = providerState.alias.trim().toLowerCase()
      if (a && !chatModels[a]) {
        setChatModels({
          [a]: [{ provider: providerState.provider, model_id: providerState.modelId, weight: 1 }],
        })
        setDefaultAlias(a)
      }
    }
    setRoutingPrefilled(true)
  }, [step, routingPrefilled, providerState])

  // Keep the default alias sensible: whenever aliases exist but the chosen
  // default is empty or no longer one of them, snap it to the first alias. This
  // mirrors the backend's auto-set so the dashboard never shows a default of
  // "none" while aliases are defined (the confusing state an admin reported).
  useEffect(() => {
    const keys = Object.keys(chatModels)
    if (keys.length && (!defaultAlias || !keys.includes(defaultAlias))) {
      setDefaultAlias(keys[0])
    }
  }, [chatModels])  // eslint-disable-line react-hooks/exhaustive-deps

  // Step-by-step validation
  const wsIdValid = !!wsId.trim() && SLUG_RE.test(wsId.trim()) && !!clientId
  const aliasKeys = Object.keys(chatModels)
  const routingValid = aliasKeys.length > 0 && aliasKeys.every((a) => {
    const ts = chatModels[a]
    return ts.length > 0 && ts.every((t) => t.provider && t.model_id?.trim())
  }) && (!defaultAlias || aliasKeys.includes(defaultAlias))

  const stepBlocked: Record<number, { blocked: boolean; reason?: string }> = {
    0: { blocked: !wsIdValid, reason: 'Enter a valid workspace_id slug.' },
    1: {
      blocked: providerState.testStatus !== 'pass' || !providerState.alias.trim(),
      reason: providerState.testStatus !== 'pass'
        ? 'Run Test Connection - it must pass before advancing.'
        : 'Enter the alias used in chat requests.',
    },
    2: { blocked: !routingValid, reason: 'Define at least one alias with a complete target.' },
    3: { blocked: false },
    4: { blocked: false },
    5: { blocked: !keyIssueValid(keyValue), reason: 'Fill the label, roles and expiry (or check no-expiry).' },
  }

  async function finish() {
    setBusy(true); setErr('')
    try {
      const id = wsId.trim()

      // 1) Create the workspace skeleton - chat_models include the alias from
      //    Step 2 (resolvability check is skipped on create because providers
      //    haven't been attached yet; PATCH path enforces it).
      await admin.createWorkspace({
        workspace_id: id,
        client_id: clientId,
        name: displayName.trim() || id,
        chat_models: chatModels as any,
        default_chat_alias: defaultAlias || null,
        guardrails: (guardrailsEnabled ? guardrails : {}) as any,
        quotas: defaultAlias && (qb.rpm || qb.tpm)
          ? { [defaultAlias]: { rpm: qb.rpm ?? undefined, tpm: qb.tpm ?? undefined } }
          : {},
        budgets: {
          ...(qb.workspace_usd != null ? { workspace_usd: qb.workspace_usd } : {}),
          ...(qb.user_usd != null ? { user_usd: qb.user_usd } : {}),
        },
      } as any).then((res: any) => {
        // Surface the backend's auto-set notice (e.g. default alias chosen for you)
        if (res?.warning) toastOk(res.warning)
      })

      // 2) Attach the provider (uses the same Test-passed creds)
      const cfg: any = { ...providerState.config, aliases: { [providerState.alias]: providerState.modelId } }
      await admin.addProvider(id, {
        provider: providerState.provider,
        credentials: providerState.credentials as any,
        config: cfg,
      } as any)

      // 3) Issue the first key (server validates expires_at strictly now)
      const r = await admin.issueKey(id, {
        roles: keyValue.roles,
        expires_at: keyValue.expires_at || null,
      })
      setIssuedKey(r.api_key)

      setStep(6)
      toastOk(`Workspace ${id} onboarded`)
    } catch (e: any) {
      setErr(e?.message || 'failed to create workspace')
      toastError(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  const totalSteps = STEPS.length

  return (
    <Modal
      open
      onClose={onClose}
      title={step === 6 ? 'Workspace ready' : `Onboard workspace · Step ${step + 1} of ${totalSteps} - ${STEPS[step].label}`}
      subtitle={
        step === 6
          ? 'Save the API key below; it is only shown once.'
          : 'You can revisit any step before saving. Each step is validated before you advance.'
      }
      size="xl"
      testId="wizard"
      footer={
        step === 6 ? (
          <button className="btn-primary text-sm" onClick={onClose} data-testid="wizard-done">
            Done
          </button>
        ) : (
          <>
            <button
              className="btn-ghost text-sm"
              onClick={() => (step > 0 ? setStep((step - 1) as StepIdx) : onClose())}
              disabled={busy}
              data-testid="wizard-back"
            >
              {step > 0 ? 'Back' : 'Cancel'}
            </button>
            {step < totalSteps - 1 ? (
              <button
                className="btn-primary text-sm disabled:opacity-50"
                disabled={stepBlocked[step].blocked || busy}
                onClick={() => setStep((step + 1) as StepIdx)}
                data-testid="wizard-next"
                title={stepBlocked[step].blocked ? stepBlocked[step].reason : undefined}
              >
                Next →
              </button>
            ) : (
              <button
                className="btn-primary text-sm disabled:opacity-50"
                disabled={stepBlocked[step].blocked || busy}
                onClick={finish}
                data-testid="wizard-finish"
              >
                {busy ? <><Loader2 size={14} className="animate-spin" /> Creating…</>
                      : <><KeyRound size={14} /> Create & issue key</>}
              </button>
            )}
          </>
        )
      }
    >
      {/* Stepper */}
      {step !== 6 && (
        <div className="flex items-center gap-1 mb-5">
          {STEPS.map((s, i) => (
            <div key={s.key} className="flex items-center gap-1.5 flex-1">
              <div
                className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-semibold shrink-0 transition-colors"
                style={
                  i < step
                    ? { background: '#34D399', color: '#0A0B0F' }
                    : i === step
                      ? { background: 'var(--color-accent)', color: '#fff', boxShadow: '0 0 0 3px var(--color-accent-soft)' }
                      : { background: 'transparent', color: 'var(--color-text-secondary)', border: '1.5px solid var(--color-border-strong, #3A4150)' }
                }
                aria-current={i === step ? 'step' : undefined}
              >
                {i < step ? <Check size={12} /> : i + 1}
              </div>
              <span className={`text-[10.5px] truncate ${
                i === step ? 'text-gray-100 font-semibold'
                : i < step ? 'text-success' : 'text-gray-400'
              }`}>
                {s.label}
              </span>
              {i < STEPS.length - 1 && (
                <div className="h-px flex-1 ml-1"
                     style={{ background: i < step ? '#34D399' : 'var(--color-border)' }} />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Step bodies */}
      {step === 0 && (
        <div className="space-y-4">
          <Field
            label="Client"
            hint="The enterprise tenant this workspace belongs to. Create Clients under Admin → Clients first."
            required
          >
            <select
              className="input text-xs"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              data-testid="wizard-client-id"
            >
              <option value="">Select a client…</option>
              {clientsList.map((c) => (
                <option key={c.client_id} value={c.client_id}>
                  {c.name || c.client_id} ({c.client_id})
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Workspace ID"
            hint="Lowercase letters, digits, hyphens. Used as the primary identifier in routes (/admin/workspaces/{id}) and tokens. Cannot be changed later."
            required
          >
            <input
              className="input mono text-xs"
              value={wsId}
              onChange={(e) => setWsId(e.target.value.toLowerCase())}
              placeholder="novatech-payments"
              autoFocus
              data-testid="wizard-ws-id"
            />
          </Field>
          <Field
            label="Display name"
            hint="Human-friendly label shown in the dashboard. Defaults to the workspace ID."
          >
            <input
              className="input text-xs"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="NovaTech - Payments"
              data-testid="wizard-ws-name"
            />
          </Field>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-2">
          <div className="text-[11.5px] text-muted">
            Add a provider so the workspace can serve at least one model. You can add more later
            under <span className="text-gray-100">Admin → Providers</span>.
          </div>
          <ProviderEditor
            state={providerState}
            onChange={setProviderState}
            showAliasField
            testIdPrefix="wizard-provider"
          />
        </div>
      )}

      {step === 2 && (
        <div className="space-y-3">
          <div className="text-[11.5px] text-muted">
            We pre-populated a default alias from the provider you just added - you can add fallbacks here so a request to that alias
            can fail over to a second provider.
          </div>
          <AliasMapEditor
            workspaceId={null /* no workspace exists yet - providers list is empty by design */}
            value={chatModels}
            onChange={setChatModels}
            testIdPrefix="wizard-aliases"
            emptyHint="No aliases yet. Add at least one to define how requests resolve to providers."
          />
          <Field
            label="Default chat alias"
            hint="Resolved when a request omits the model field. Optional but recommended."
          >
            <select
              className="input mono text-xs"
              value={defaultAlias}
              onChange={(e) => setDefaultAlias(e.target.value)}
              data-testid="wizard-default-alias"
            >
              <option value="">- none -</option>
              {aliasKeys.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </Field>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-3">
          <label className="flex items-start gap-2.5 rounded-lg border border-border p-3 cursor-pointer bg-app">
            <input type="checkbox" className="accent-accent mt-0.5" checked={guardrailsEnabled}
              onChange={(e) => setGuardrailsEnabled(e.target.checked)} data-testid="wizard-guardrails-enabled" />
            <span>
              <span className="text-sm text-gray-100 font-medium">Enable guardrails for this workspace</span>
              <span className="block text-[11.5px] text-muted mt-0.5">
                Guardrails are <strong className="text-gray-200">optional</strong>. Leave this off for a
                governance-only workspace - you still get routing, budgets, rate-limits, cost attribution
                and full observability, just no content inspection/blocking.
              </span>
            </span>
          </label>
          {guardrailsEnabled ? (
            <>
              <div className="text-[11.5px] text-muted">
                Default action and detector flags. You can attach custom rules from the Rule Builder once at
                least one rule has been created.
              </div>
              <GuardrailsBuilder
                value={guardrails}
                onChange={setGuardrails}
                testIdPrefix="wizard-guardrails"
                workspaceId={wsId.trim() || undefined}
              />
            </>
          ) : (
            <div className="rounded-lg border border-border bg-app p-4 text-[12px] text-muted">
              No guardrails will be applied. Requests pass straight through the governance pipeline
              (auth → routing → budgets → provider) with no content inspection.
            </div>
          )}
        </div>
      )}

      {step === 4 && (
        <div className="space-y-3">
          <div className="text-[11.5px] text-muted">
            Workspace-level limits. Components inherit these (component is a runtime
            attribution dimension carried by the X-Gateway-Component header, not a
            separately-configured entity).
          </div>
          <QuotaBudgetForm
            value={qb}
            onChange={setQb}
            testIdPrefix="wizard-qb"
          />
        </div>
      )}

      {step === 5 && (
        <div className="space-y-3">
          <div className="text-[11.5px] text-muted">
            The first API key for this workspace. The plaintext is shown <span className="text-warn">exactly once</span> - copy it
            on the next screen.
          </div>
          <KeyIssueForm
            value={keyValue}
            onChange={setKeyValue}
            testIdPrefix="wizard-key"
          />
        </div>
      )}

      {step === 6 && issuedKey && (
        <div className="space-y-4">
          <div className="bg-success/10 border border-success/40 rounded-lg p-3 flex items-start gap-2">
            <Check size={14} className="text-success shrink-0 mt-0.5" />
            <div className="text-[12px] text-gray-200">
              <div className="font-semibold">Workspace {wsId} is ready.</div>
              <div className="text-muted">
                Provider attached, routing aliases saved, guardrails enabled, first key issued.
              </div>
            </div>
          </div>
          <div className="bg-warn/10 border border-warn/40 rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle size={14} className="text-warn shrink-0 mt-0.5" />
            <div className="text-[12px] text-gray-200">
              <div className="font-semibold">This is the only time the API key is visible.</div>
              <div className="text-muted">Only the SHA-256 hash is stored.</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <code
              className="flex-1 mono text-xs bg-app border border-border rounded-lg px-3 py-2 break-all text-gray-100"
              data-testid="wizard-issued-key"
            >
              {issuedKey}
            </code>
            <button
              type="button"
              className="btn-primary text-xs"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(issuedKey)
                  toastOk('Copied')
                } catch {
                  toastError('Copy failed - select and copy manually')
                }
              }}
              data-testid="wizard-copy-key"
            >
              <ClipboardCopy size={12} /> Copy
            </button>
          </div>
        </div>
      )}

      {err && step !== 6 && (
        <div className="mt-4 bg-danger/10 border border-danger/40 rounded-lg p-3 text-[11.5px] text-danger">
          {err}
        </div>
      )}
    </Modal>
  )
}
