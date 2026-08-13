// ProviderEditor - module-scope, no hooks-in-render bug. Used by both the
// standalone /admin/providers screen and the Onboarding wizard's Step 1.
//
// Behaviors locked in by the audit:
//   - Test Connection is DISABLED until all required creds are non-blank
//     (whitespace-only counts as blank).
//   - On Save, if Test hasn't passed, Save is BLOCKED with a clear message
//     ("Run Test Connection first; saving with un-tested creds is not
//     allowed"). The user must Test → green before they can Save.
//   - All hard-coded data (provider list, required fields, AWS regions,
//     defaults) lives in PROVIDER_SPEC.ts - single source of truth.
//   - Real isolated-creds Test: the gateway never falls back to env/IMDS
//     credentials (proven in WAVE 16-FIX-2 backend).

import { CheckCircle2, Eye, EyeOff, Info, Loader2, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { admin } from '../../api/client'
import type { ProviderType } from '../../api/types'
import { Field } from './Field'
import { fieldVisible, isCredsValid, PROVIDER_SPECS } from './PROVIDER_SPEC'

export type ProviderEditorState = {
  provider: ProviderType
  credentials: Record<string, string>
  config: Record<string, string>
  alias: string
  modelId: string
  testStatus: 'untested' | 'testing' | 'pass' | 'fail'
  testDetail: string
  testLatency?: number
}

export const emptyProviderState = (provider: ProviderType = 'bedrock'): ProviderEditorState => ({
  provider,
  // Bedrock defaults to the static-keys auth mode (the conditional fields react to it).
  credentials: provider === 'bedrock' ? { auth_type: 'static' } : {},
  config: provider === 'bedrock' ? { region: 'us-east-1' } : {},
  alias: '',
  modelId: PROVIDER_SPECS[provider].defaultModelId,
  testStatus: 'untested',
  testDetail: '',
})

export function ProviderEditor({
  state,
  onChange,
  showAliasField = true,
  testIdPrefix = 'provider',
}: {
  state: ProviderEditorState
  onChange: (next: ProviderEditorState) => void
  showAliasField?: boolean
  testIdPrefix?: string
}) {
  const spec = PROVIDER_SPECS[state.provider]
  const [reveal, setReveal] = useState<Record<string, boolean>>({})

  const credsReady = useMemo(
    () => isCredsValid(state.provider, state.credentials, state.config),
    [state.provider, state.credentials, state.config]
  )

  function setProvider(p: ProviderType) {
    onChange({
      ...emptyProviderState(p),
      alias: state.alias,   // preserve user-entered alias
    })
  }

  function setField(f: { key: string; destination?: 'credentials' | 'config' }, value: string) {
    if (f.destination === 'config') {
      onChange({
        ...state,
        config: { ...state.config, [f.key]: value },
        testStatus: 'untested',
        testDetail: '',
      })
    } else {
      onChange({
        ...state,
        credentials: { ...state.credentials, [f.key]: value },
        testStatus: 'untested',
        testDetail: '',
      })
    }
  }

  async function runTest() {
    onChange({ ...state, testStatus: 'testing', testDetail: '' })
    try {
      const r = await admin.testProvider({
        provider: state.provider,
        credentials: state.credentials as any,
        config: state.config,
        model_id: state.modelId || undefined,
      })
      if (r.ok) {
        onChange({
          ...state,
          testStatus: 'pass',
          testDetail: '',
          testLatency: r.latency_ms,
        })
      } else {
        const msg = r.error || r.detail?.error || 'unknown error'
        onChange({
          ...state,
          testStatus: 'fail',
          testDetail: typeof msg === 'string' ? msg : JSON.stringify(msg),
        })
      }
    } catch (e: any) {
      onChange({ ...state, testStatus: 'fail', testDetail: e?.message || 'request failed' })
    }
  }

  return (
    <div className="space-y-4">
      <Field
        label="Provider"
        hint="The upstream LLM service that will serve this credential. Each provider has different required fields below."
        required
      >
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-1.5">
          {(Object.values(PROVIDER_SPECS) as typeof PROVIDER_SPECS[keyof typeof PROVIDER_SPECS][]).map((s) => (
            <button
              key={s.id}
              type="button"
              data-testid={`${testIdPrefix}-option-${s.id}`}
              onClick={() => setProvider(s.id)}
              className={`px-2.5 py-1.5 rounded-lg border text-xs transition ${
                state.provider === s.id
                  ? 'border-accent text-white bg-accent/10'
                  : 'border-border text-gray-300 hover:bg-app'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="text-[11px] text-muted mt-1.5 flex items-start gap-1.5">
          <Info size={11} className="mt-0.5 shrink-0" />
          <span>{spec.blurb}</span>
        </div>
      </Field>

      {spec.fields.map((f) => {
        // conditional fields (e.g. bedrock auth-mode-specific creds) only render
        // when their showIf condition is met.
        if (!fieldVisible(f, state.credentials, state.config)) return null
        const bag = f.destination === 'config' ? state.config : state.credentials
        const value = bag[f.key] || ''
        const isPassword = f.type === 'password'
        const tid = `${testIdPrefix}-field-${f.key}`
        if (f.type === 'select') {
          return (
            <Field key={f.key} label={f.label} hint={f.hint} required={f.required}>
              <select
                className="input text-xs"
                value={value}
                onChange={(e) => setField(f, e.target.value)}
                data-testid={tid}
              >
                <option value="">Select…</option>
                {f.options?.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
          )
        }
        return (
          <Field key={f.key} label={f.label} hint={f.hint} required={f.required}>
            <div className="relative">
              <input
                className="input mono text-xs pr-9"
                type={isPassword && !reveal[f.key] ? 'password' : 'text'}
                placeholder={f.placeholder}
                value={value}
                onChange={(e) => setField(f, e.target.value)}
                data-testid={tid}
                autoComplete="off"
                spellCheck={false}
              />
              {isPassword && (
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white"
                  onClick={() => setReveal((r) => ({ ...r, [f.key]: !r[f.key] }))}
                  aria-label={reveal[f.key] ? 'Hide value' : 'Show value'}
                  tabIndex={-1}
                  data-testid={`${tid}-reveal`}
                >
                  {reveal[f.key] ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              )}
            </div>
          </Field>
        )
      })}

      {showAliasField && (
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Alias"
            hint="Short name used in chat requests (e.g. claude-sonnet-4-5). Lowercase, hyphens."
            required
          >
            <input
              className="input mono text-xs"
              value={state.alias}
              onChange={(e) => onChange({ ...state, alias: e.target.value.toLowerCase() })}
              data-testid={`${testIdPrefix}-alias`}
              placeholder="claude-sonnet-4-5"
            />
          </Field>
          <Field
            label="Provider model ID"
            hint="The exact identifier this provider expects (e.g. for Bedrock: full ARN-style model id)."
            required
          >
            <input
              className="input mono text-xs"
              value={state.modelId}
              onChange={(e) => onChange({ ...state, modelId: e.target.value })}
              data-testid={`${testIdPrefix}-model-id`}
            />
          </Field>
        </div>
      )}

      <div className="border-t border-border pt-4 flex items-center gap-3">
        <button
          type="button"
          className="btn-primary text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={!credsReady || state.testStatus === 'testing'}
          onClick={runTest}
          data-testid={`${testIdPrefix}-test`}
        >
          {state.testStatus === 'testing' ? (
            <>
              <Loader2 size={14} className="animate-spin" /> Testing…
            </>
          ) : (
            'Test Connection'
          )}
        </button>
        {state.testStatus === 'pass' && (
          <div className="flex items-center gap-1.5 text-success text-xs"
               data-testid={`${testIdPrefix}-test-pass`}>
            <CheckCircle2 size={14} />
            <span>reachable {state.testLatency ? `· ${Math.round(state.testLatency)} ms` : ''}</span>
          </div>
        )}
        {state.testStatus === 'fail' && (
          <div className="flex items-start gap-1.5 text-danger text-xs flex-1"
               data-testid={`${testIdPrefix}-test-fail`}>
            <XCircle size={14} className="mt-0.5 shrink-0" />
            <span className="break-words">{state.testDetail || 'Test failed'}</span>
          </div>
        )}
        {state.testStatus === 'untested' && credsReady && (
          <div className="text-[11px] text-muted">
            Run Test Connection - Save is blocked until it passes.
          </div>
        )}
        {!credsReady && (
          <div className="text-[11px] text-muted">
            Fill all required fields (marked <span className="text-danger">*</span>) to enable Test.
          </div>
        )}
      </div>
    </div>
  )
}
