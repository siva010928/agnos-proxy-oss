// PricingOverrideForm - used by the standalone Custom Pricing screen.
// Adds a model picker (live count of matching models from /admin/models),
// step 0.000001, collision warning, and the same field/help patterns.

import { Info } from 'lucide-react'
import React, { useMemo, useState } from 'react'
import { useModels } from '../../lib/api'
import { Field } from './Field'

export interface PricingOverrideValue {
  model_substr: string
  input_per_1k: number
  output_per_1k: number
  note: string
}

export function emptyPricingOverride(): PricingOverrideValue {
  return { model_substr: '', input_per_1k: 0, output_per_1k: 0, note: '' }
}

export function PricingOverrideForm({
  value,
  onChange,
  testIdPrefix = 'pricing',
  isEdit = false,
}: {
  value: PricingOverrideValue
  onChange: (next: PricingOverrideValue) => void
  testIdPrefix?: string
  isEdit?: boolean
}) {
  const models = useModels()
  const list: { provider: string; model_id: string }[] = (models.data?.models as any) || []
  const [provider, setProvider] = useState('')
  const providers = useMemo(() => Array.from(new Set(list.map((m) => m.provider))).sort(), [list])
  // MODEL SUGGESTIONS are scoped to the chosen provider - you must pick a provider
  // first, so we NEVER dump all 190+ models into the datalist at once.
  const scoped = useMemo(() => (provider ? list.filter((m) => m.provider === provider) : []), [list, provider])
  // The breadth warning stays GLOBAL (the saved override matches by substring
  // across every provider), so an over-broad substring is still caught.
  const matches = useMemo(() => {
    const s = value.model_substr.trim().toLowerCase()
    if (!s || s.length < 3) return []
    return list.filter((m) => m.model_id.toLowerCase().includes(s)).slice(0, 50)
  }, [value.model_substr, list])

  const tooBroad = matches.length > 30

  return (
    <div className="space-y-4">
      {!isEdit && (
        <Field label="Provider" required hint="Pick a provider first - the model list below is scoped to it (we never list all 190+ models at once). The saved override still matches by substring.">
          <select className="input text-xs" value={provider} onChange={(e) => setProvider(e.target.value)}
                  data-testid={`${testIdPrefix}-provider`}>
            <option value="">- select a provider -</option>
            {providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
      )}
      <Field
        label="Model (full id or substring)"
        hint={
          <>
            {provider ? <>Pick a full <b>{provider}</b> model id from the list, or type any substring. </> : null}
            The override applies to every model id <em>containing</em> this text (e.g. <span className="mono">claude-sonnet-4-5</span> matches
            both <span className="mono">claude-sonnet-4-5-20250929</span> and <span className="mono">us.anthropic.claude-sonnet-4-5-20250929-v1:0</span>).
            Minimum 3 characters; rejected if it would match every model.
          </>
        }
        required
      >
        <input
          className="input mono text-xs"
          value={value.model_substr}
          onChange={(e) => onChange({ ...value, model_substr: e.target.value.toLowerCase() })}
          placeholder={isEdit ? '' : provider ? `e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0` : 'pick a provider above first…'}
          disabled={isEdit || (!isEdit && !provider)}
          list={`${testIdPrefix}-models`}
          data-testid={`${testIdPrefix}-substr`}
        />
        {/* list ALL scoped models (bedrock has 340+, and full ids like us.anthropic.*
            sort late - the old 300 cap cut them off, so they were unfindable). */}
        <datalist id={`${testIdPrefix}-models`}>
          {scoped.map((m) => (
            <option key={`${m.provider}/${m.model_id}`} value={m.model_id} label={`${m.provider}`} />
          ))}
        </datalist>
        {provider && !isEdit && (
          <div className="text-[11px] mt-1 text-muted">{scoped.length} {provider} models available in the dropdown.</div>
        )}
        {!isEdit && !provider && (
          <div className="text-[11px] mt-1 flex items-center gap-1 text-muted">
            <Info size={10} />
            <span>Select a provider above to list its models.</span>
          </div>
        )}
        {value.model_substr.length >= 3 && (
          <div className={`text-[11px] mt-1 flex items-center gap-1 ${
            tooBroad ? 'text-warn' : matches.length === 0 ? 'text-danger' : 'text-muted'
          }`}>
            <Info size={10} />
            <span>
              Matches {matches.length}{tooBroad ? '+ models - consider narrowing' : ' model(s)'}
              {matches.length > 0 && (
                <>
                  {' · '}
                  <span className="mono">{matches.slice(0, 3).map((m) => m.model_id).join(', ')}{matches.length > 3 ? '…' : ''}</span>
                </>
              )}
            </span>
          </div>
        )}
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Input price (USD per 1k tokens)"
          hint="Cost charged for prompt tokens. Step 0.000001 supports realistic per-token prices."
          required
        >
          <input
            className="input text-xs mono"
            type="number"
            step="0.000001"
            min={0}
            value={value.input_per_1k}
            onChange={(e) => onChange({ ...value, input_per_1k: parseFloat(e.target.value) || 0 })}
            data-testid={`${testIdPrefix}-input`}
          />
        </Field>
        <Field
          label="Output price (USD per 1k tokens)"
          hint="Cost charged for completion tokens."
          required
        >
          <input
            className="input text-xs mono"
            type="number"
            step="0.000001"
            min={0}
            value={value.output_per_1k}
            onChange={(e) => onChange({ ...value, output_per_1k: parseFloat(e.target.value) || 0 })}
            data-testid={`${testIdPrefix}-output`}
          />
        </Field>
      </div>

      <Field
        label="Note (optional)"
        hint="Why this override exists; surfaced under 'source' on the catalog. E.g. 'enterprise discount Q4'."
      >
        <input
          className="input text-xs"
          value={value.note}
          onChange={(e) => onChange({ ...value, note: e.target.value })}
          placeholder="enterprise discount"
          data-testid={`${testIdPrefix}-note`}
        />
      </Field>
    </div>
  )
}

export function pricingOverrideValid(v: PricingOverrideValue): { ok: boolean; errors: string[] } {
  const errors: string[] = []
  const substr = v.model_substr.trim()
  if (!substr) errors.push('Model substring is required.')
  else if (substr.length < 3) errors.push('Model substring must be at least 3 characters.')
  if (!Number.isFinite(v.input_per_1k) || v.input_per_1k < 0) errors.push('Input price must be ≥ 0.')
  if (!Number.isFinite(v.output_per_1k) || v.output_per_1k < 0) errors.push('Output price must be ≥ 0.')
  if (v.input_per_1k === 0 && v.output_per_1k === 0) {
    errors.push('At least one of input or output price must be > 0 (otherwise the override is a no-op).')
  }
  return { ok: errors.length === 0, errors }
}
