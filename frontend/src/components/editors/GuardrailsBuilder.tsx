// GuardrailsBuilder - visual replacement for the raw-JSON guardrails textarea.
// Same structure backend understands today (guardrails dict on workspace +
// component): {pii_detection?: bool, secrets_detection?: bool, mode?: string,
// auto_truncate?: bool, rule_ids?: number[]}.
//
// Plus a multiselect of stored RULES so admins can attach rules built in the
// Rule Builder to this workspace/component without touching JSON.

import { Shield, ExternalLink, RefreshCw } from 'lucide-react'
import React from 'react'
import { useGuardrails } from '../../lib/api'
import { Field } from './Field'

export interface GuardrailsValue {
  pii_detection?: boolean
  secrets_detection?: boolean
  auto_truncate?: boolean
  mode?: 'block' | 'redact' | 'audit'
  rule_ids?: number[]
}

const MODES: { value: 'block' | 'redact' | 'audit'; label: string; hint: string }[] = [
  { value: 'block', label: 'Block', hint: 'Reject the request with HTTP 422 and a guardrail_violation error.' },
  { value: 'redact', label: 'Redact', hint: 'Mask matching content (e.g. PII) and pass the redacted request through.' },
  { value: 'audit', label: 'Audit only', hint: 'Pass the request unchanged but emit a governance event for review.' },
]

export function GuardrailsBuilder({
  value,
  onChange,
  testIdPrefix = 'guardrails',
  workspaceId,
}: {
  value: GuardrailsValue
  onChange: (next: GuardrailsValue) => void
  testIdPrefix?: string
  // When set, only rules in scope for THIS workspace (global + its own) are
  // offered - never another workspace's workspace/component-scoped rules.
  workspaceId?: string
}) {
  const guardrails = useGuardrails()
  // /admin/guardrails returns recent violations + counts; rules live separately
  // via /admin/guardrails/rules. We fetch rules ad-hoc via api/client to avoid
  // leaking that into shared hooks.
  const [allRules, setAllRules] = React.useState<{ id: number; name: string; enabled: boolean; scope?: string; workspace_id?: string | null }[]>([])
  const loadRules = React.useCallback(() => {
    return import('../../api/client').then(({ admin }) =>
      admin.listRules(workspaceId).then((r: any) => setAllRules(r.rules || [])).catch(() => {})
    )
  }, [workspaceId])
  React.useEffect(() => {
    let alive = true
    import('../../api/client').then(({ admin }) =>
      admin.listRules(workspaceId).then((r: any) => {
        if (alive) setAllRules(r.rules || [])
      }).catch(() => {})
    )
    // Re-pull rules when the tab regains focus - so a rule created in the Rule
    // Builder (opened in a new tab) shows up here without losing onboarding state.
    const onFocus = () => loadRules()
    window.addEventListener('focus', onFocus)
    return () => { alive = false; window.removeEventListener('focus', onFocus) }
  }, [loadRules, workspaceId])

  const setMode = (m: 'block' | 'redact' | 'audit') => onChange({ ...value, mode: m })
  const toggle = (k: keyof GuardrailsValue) => onChange({ ...value, [k]: !value[k] })
  // (toggleRuleId / sel removed: rules in scope now apply automatically - the
  // earlier checkbox was misleading because workspace-scoped rules ALWAYS applied
  // at runtime regardless of tick state. `rule_ids` stays in the interface for
  // backward-compat with stored configs, but it's no longer surfaced in the UI.)

  return (
    <div className="space-y-4">
      <Field label="Enforcement mode" hint="The default action for built-in detectors (PII / secrets / auto-truncate) below. Custom rules from the Rule Builder use their OWN action - this mode does not downgrade them. Override per request via the X-Gateway-Guardrail-Mode header (operator-level ceiling)." required>
        <div className="flex flex-wrap gap-1.5">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => setMode(m.value)}
              className={`px-3 py-1.5 rounded-lg border text-xs ${
                value.mode === m.value
                  ? 'border-accent text-white bg-accent/10'
                  : 'border-border text-gray-300 hover:bg-app'
              }`}
              data-testid={`${testIdPrefix}-mode-${m.value}`}
              title={m.hint}
            >
              {m.label}
            </button>
          ))}
        </div>
        {value.mode === 'audit' && (
          <div className="mt-2 text-[11px] text-muted" data-testid={`${testIdPrefix}-mode-governor-note`}>
            Audit-only is the default for the built-in detectors below - they will only log. Custom rules with an explicit
            action (e.g. <span className="mono">block</span>) are unaffected by this setting and act as configured.
          </div>
        )}
        {value.mode === 'redact' && (
          <div className="mt-2 text-[11px] text-muted" data-testid={`${testIdPrefix}-mode-governor-note`}>
            Redact is the default for the built-in detectors below. Custom rules with an explicit action act as configured.
          </div>
        )}
      </Field>

      <Field
        label="Built-in detectors"
        hint="Convenience flags that turn on detector profiles globally for this scope."
      >
        <div className="space-y-2">
          {([
            ['pii_detection', 'PII detection', 'Detects emails, phone numbers, credit cards, SSNs, and similar.'],
            ['secrets_detection', 'Secrets detection', 'Detects AWS keys, GitHub tokens, JWTs, passwords.'],
            ['auto_truncate', 'Auto-truncate', 'Drop oldest messages to fit context window; keeps system message.'],
          ] as const).map(([k, label, hint]) => (
            <label key={k} className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                className="accent-accent"
                checked={!!value[k]}
                onChange={() => toggle(k)}
                data-testid={`${testIdPrefix}-${k}`}
              />
              <span className="text-gray-100">{label}</span>
              <span className="text-[11px] text-muted">- {hint}</span>
            </label>
          ))}
        </div>
      </Field>

      <Field
        label={
          <span className="inline-flex items-center gap-1">
            <Shield size={11} /> Custom rules
          </span> as any
        }
        hint={
          <>
            Rules created under <span className="text-gray-100">Guardrails → Rule Builder</span> in scope for this workspace
            (global rules + this workspace's own) <strong>apply automatically</strong> when enabled - you do not tick them on.
            They run in addition to the built-in detectors above and use their own configured action.
          </>
        }
      >
        <div className="flex items-center gap-3 mb-2">
          <a
            href="/app/guardrails/rules"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[11.5px] text-accent hover:underline"
            data-testid={`${testIdPrefix}-open-rule-builder`}
          >
            <ExternalLink size={11} /> Open Rule Builder (new tab)
          </a>
          <button
            type="button"
            onClick={() => loadRules()}
            className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-gray-200"
            data-testid={`${testIdPrefix}-refresh-rules`}
          >
            <RefreshCw size={10} /> Refresh
          </button>
          <span className="text-[10.5px] text-muted ml-auto">Opens in a new tab - your onboarding progress is kept.</span>
        </div>
        {allRules.length === 0 ? (
          <div className="text-[11.5px] text-muted">
            No custom rules in scope yet. Use <span className="text-gray-100">Open Rule Builder</span> above; rules you scope
            to <span className="mono">global</span> or to this workspace will appear here automatically.
          </div>
        ) : (
          <div className="bg-app border border-border rounded-lg max-h-44 overflow-y-auto divide-y divide-border/60"
               data-testid={`${testIdPrefix}-rules-list`}>
            {allRules.map((r) => (
              <div key={r.id} className="flex items-center gap-2 px-3 py-2"
                   data-testid={`${testIdPrefix}-rule-${r.id}`}>
                {/* No checkbox: rules in scope apply automatically. The earlier
                    checkbox was misleading - it had no runtime effect for
                    workspace-scoped rules (they always applied) but suggested an
                    opt-in. The badge below reports the actual runtime state. */}
                <span className="text-[10px]" style={{ color: r.enabled ? 'var(--color-ok)' : 'var(--color-muted)' }}
                      title={r.enabled ? 'applies on every request' : 'rule is disabled in the Rule Builder'}>
                  {r.enabled ? '✓' : '○'}
                </span>
                <span className="text-sm text-gray-100">{r.name}</span>
                <span
                  className={`text-[9.5px] uppercase tracking-wider px-1.5 py-0.5 rounded ${
                    r.scope === 'global'
                      ? 'bg-accent/15 text-accent'
                      : 'bg-elevated text-muted border border-border'
                  }`}
                  title={r.scope === 'global'
                    ? 'Global rule - applies to every workspace.'
                    : 'Scoped to this workspace.'}
                >
                  {r.scope === 'global' ? '🌐 global' : 'this workspace'}
                </span>
                <span className="text-[10px] ml-auto"
                      style={{ color: r.enabled ? 'var(--color-ok)' : 'var(--color-warn, #FBBF24)' }}>
                  {r.enabled ? 'applies' : 'disabled'}
                </span>
              </div>
            ))}
          </div>
        )}
      </Field>
    </div>
  )
}
