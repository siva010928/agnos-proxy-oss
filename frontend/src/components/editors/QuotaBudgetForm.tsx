// QuotaBudgetForm - RPM/TPM + workspace/user budget caps with units and helper
// text. Each input shows the inherited workspace value as the placeholder
// when blank ("inherit from workspace"), so the empty=inherit semantics is
// visible.

import React from 'react'
import { Field } from './Field'

export interface QuotaBudgetValue {
  rpm?: number | null
  tpm?: number | null
  workspace_usd?: number | null
  user_usd?: number | null
}

export function QuotaBudgetForm({
  value,
  onChange,
  inherited,
  testIdPrefix = 'qb',
}: {
  value: QuotaBudgetValue
  onChange: (next: QuotaBudgetValue) => void
  inherited?: QuotaBudgetValue
  testIdPrefix?: string
}) {
  const set = (k: keyof QuotaBudgetValue, v: string) => {
    if (v.trim() === '') {
      onChange({ ...value, [k]: null })
      return
    }
    const n = Number(v)
    if (!Number.isFinite(n) || n < 0) return
    onChange({ ...value, [k]: n })
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <Field
        label="Requests per minute (RPM)"
        hint={
          inherited?.rpm != null
            ? `Leave blank to inherit ${inherited.rpm.toLocaleString()} req/min from workspace.`
            : 'Hard ceiling on requests per minute. Leave blank for no limit.'
        }
      >
        <input
          className="input text-xs"
          type="number"
          min={0}
          value={value.rpm ?? ''}
          placeholder={inherited?.rpm != null ? String(inherited.rpm) : 'no limit'}
          onChange={(e) => set('rpm', e.target.value)}
          data-testid={`${testIdPrefix}-rpm`}
        />
      </Field>
      <Field
        label="Tokens per minute (TPM)"
        hint={
          inherited?.tpm != null
            ? `Leave blank to inherit ${inherited.tpm.toLocaleString()} tok/min from workspace.`
            : 'Hard ceiling on tokens per minute (input + output). Leave blank for no limit.'
        }
      >
        <input
          className="input text-xs"
          type="number"
          min={0}
          value={value.tpm ?? ''}
          placeholder={inherited?.tpm != null ? String(inherited.tpm) : 'no limit'}
          onChange={(e) => set('tpm', e.target.value)}
          data-testid={`${testIdPrefix}-tpm`}
        />
      </Field>
      <Field
        label="Workspace budget cap (USD/month)"
        hint={
          inherited?.workspace_usd != null
            ? `Leave blank to inherit $${inherited.workspace_usd.toFixed(2)}/mo.`
            : 'When monthly spend exceeds this, requests return 402 Budget Exceeded.'
        }
      >
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
          <input
            className="input text-xs pl-6"
            type="number"
            step={0.01}
            min={0}
            value={value.workspace_usd ?? ''}
            placeholder={inherited?.workspace_usd != null ? String(inherited.workspace_usd) : 'no cap'}
            onChange={(e) => set('workspace_usd', e.target.value)}
            data-testid={`${testIdPrefix}-ws-budget`}
          />
        </div>
      </Field>
      <Field
        label="Per-user budget cap (USD/month)"
        hint={
          inherited?.user_usd != null
            ? `Leave blank to inherit $${inherited.user_usd.toFixed(2)}/user/mo.`
            : 'Per-user monthly cap (resolved from JWT user_id or X-Gateway-User).'
        }
      >
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
          <input
            className="input text-xs pl-6"
            type="number"
            step={0.01}
            min={0}
            value={value.user_usd ?? ''}
            placeholder={inherited?.user_usd != null ? String(inherited.user_usd) : 'no cap'}
            onChange={(e) => set('user_usd', e.target.value)}
            data-testid={`${testIdPrefix}-user-budget`}
          />
        </div>
      </Field>
    </div>
  )
}
