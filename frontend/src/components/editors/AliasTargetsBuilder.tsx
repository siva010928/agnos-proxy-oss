// AliasTargetsBuilder - visual editor for {alias: [{provider, model_id, weight, ...}]}.
// Replaces the raw-JSON textareas on Components and the bespoke editor on
// Routing. Same module-scope rules apply.
//
// Behaviors:
//   - Provider dropdown sourced from THIS workspace's configured providers
//     (useWorkspaceProviders). Selecting a provider not configured here is
//     impossible, so saving cannot create dead-config.
//   - Model dropdown sourced from /admin/models filtered to the chosen
//     provider, with a "type custom" escape hatch (datalist) for new ids.
//   - Two EXPLICIT routing strategies, surfaced as a toggle so users never have
//     to reverse-engineer behavior from raw weight numbers:
//       • Failover chain  → all weights = 1. targets[0] is primary; the rest are
//         an ordered, sequential backup chain (0% traffic until the primary fails).
//       • Split traffic   → percentage weights that sum to 100. Each request picks
//         a target by weighted random; the others remain failover on error.
//     This mirrors registry.resolve_chat_targets(): weighted LB only engages when
//     len(targets) > 1 AND some weight != 1; otherwise it's pure ordered failover.
//   - Up/Down reorder buttons (failover); delete row; Add target.
//   - Built-in validation: ≥1 target; every target has provider+model_id;
//     no duplicate (provider, model_id); weight ≥ 0.

import { AlertTriangle, ArrowDown, ArrowUp, Plus, Scale, Trash2, Workflow } from 'lucide-react'
import React, { useMemo, useState } from 'react'
import { useWorkspaceModels } from './useWorkspaceModels'
import type { ProviderType } from '../../api/types'

export interface AliasTarget {
  provider: ProviderType
  model_id: string
  weight?: number
  context_window?: number
  region?: string
  base_url?: string
  api_version?: string
}

export type AliasMap = Record<string, AliasTarget[]>

export function targetsValid(targets: AliasTarget[]): { ok: boolean; errors: string[] } {
  const errors: string[] = []
  if (!targets.length) {
    errors.push('Add at least one target.')
    return { ok: false, errors }
  }
  const seen = new Set<string>()
  for (let i = 0; i < targets.length; i++) {
    const t = targets[i]
    if (!t.provider) errors.push(`Target ${i + 1}: provider is required.`)
    if (!t.model_id?.trim()) errors.push(`Target ${i + 1}: model_id is required.`)
    if (typeof t.weight === 'number' && t.weight < 0) errors.push(`Target ${i + 1}: weight must be ≥ 0.`)
    const key = `${t.provider}::${t.model_id}`
    if (t.provider && t.model_id) {
      if (seen.has(key)) errors.push(`Target ${i + 1}: duplicate ${t.provider}/${t.model_id}.`)
      seen.add(key)
    }
  }
  return { ok: errors.length === 0, errors }
}

export function aliasMapValid(aliases: AliasMap): { ok: boolean; errors: Record<string, string[]> } {
  const errors: Record<string, string[]> = {}
  for (const [name, targets] of Object.entries(aliases || {})) {
    const r = targetsValid(targets)
    if (!r.ok) errors[name] = r.errors
  }
  return { ok: Object.keys(errors).length === 0, errors }
}

export function AliasTargetsBuilder({
  workspaceId,
  targets,
  onChange,
  testIdPrefix = 'targets',
  showHelp = true,
  showErrors = true,
}: {
  workspaceId: string | null
  targets: AliasTarget[]
  onChange: (next: AliasTarget[]) => void
  testIdPrefix?: string
  showHelp?: boolean
  showErrors?: boolean
}) {
  const ws = useWorkspaceModels(workspaceId)
  const validation = useMemo(() => targetsValid(targets), [targets])

  // ── Routing strategy ─────────────────────────────────────────────────────
  // The persisted shape is a single AliasTarget[] with optional weights; the
  // backend (registry.resolve_chat_targets) reads it two ways:
  //   • all weights == 1 → ordered FAILOVER (targets[0] primary, rest sequential)
  //   • any weight != 1  → weighted LOAD-BALANCING across all targets
  // We expose that as an explicit toggle and keep the data in the canonical form
  // for each mode so the user never juggles raw weight numbers.
  type RoutingMode = 'failover' | 'loadbalance'
  const derivedMode: RoutingMode =
    targets.length > 1 && targets.some((t) => (t.weight ?? 1) !== 1) ? 'loadbalance' : 'failover'
  const [mode, setMode] = useState<RoutingMode>(derivedMode)

  const noProviders = !ws.isLoading && ws.configuredProviders.length === 0
  const lbTotal = targets.reduce((s, t) => s + (t.weight ?? 0), 0)

  // Safe default detection: in split mode, "100% on primary, 0% on the rest" means
  // the user switched strategy but has NOT opted into active load-balancing yet.
  // We surface a warning so nobody accidentally turns a cold standby into a live
  // 50/50 production split.
  const isSafeDefault =
    mode === 'loadbalance' &&
    targets.length > 1 &&
    (targets[0].weight ?? 0) === 100 &&
    targets.slice(1).every((t) => (t.weight ?? 0) === 0)

  const switchMode = (next: RoutingMode) => {
    if (next === mode) return
    setMode(next)
    if (!targets.length) return
    if (next === 'failover') {
      onChange(targets.map((t) => ({ ...t, weight: 1 })))
    } else {
      // SAFE conversion: keep 100% on the existing primary; every backup starts at
      // 0%. Failover and split have OPPOSITE risk profiles - silently guessing an
      // even split would route live production traffic to a backup that was only
      // ever meant as an emergency standby. The engineer must opt in explicitly.
      onChange(targets.map((t, i) => ({ ...t, weight: i === 0 ? 100 : 0 })))
    }
  }

  const addTarget = () => {
    const provider: ProviderType = (ws.configuredProviders[0] as ProviderType) || 'bedrock'
    const firstModel = ws.byProvider[provider]?.[0]?.model_id || ''
    if (mode === 'loadbalance') {
      // First target owns 100%; additional targets start at 0% (explicit opt-in).
      const weight = targets.length === 0 ? 100 : 0
      onChange([...targets, { provider, model_id: firstModel, weight }])
    } else {
      onChange([...targets, { provider, model_id: firstModel, weight: 1 }])
    }
  }
  const updateTarget = (i: number, patch: Partial<AliasTarget>) => {
    const next = targets.slice()
    next[i] = { ...next[i], ...patch }
    onChange(next)
  }
  const removeTarget = (i: number) => {
    const next = targets.filter((_, j) => j !== i)
    if (mode === 'loadbalance' && next.length) {
      // Re-home any freed traffic onto the primary so the split still sums to 100,
      // without silently rebalancing the OTHER targets the user already set.
      const total = next.reduce((s, t) => s + (t.weight ?? 0), 0)
      if (total !== 100) {
        const fixed = next.map((t) => ({ ...t }))
        fixed[0].weight = Math.max(0, Math.min(100, (fixed[0].weight ?? 0) + (100 - total)))
        onChange(fixed)
        return
      }
    }
    onChange(next)
  }
  const move = (i: number, delta: number) => {
    const j = i + delta
    if (j < 0 || j >= targets.length) return
    const next = targets.slice()
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }

  // Load-balance: set one row's %, redistribute the remainder across the others
  // proportionally to their current share - always re-summing to exactly 100.
  const setPercent = (i: number, raw: number) => {
    const v = Math.max(0, Math.min(100, Math.round(Number.isFinite(raw) ? raw : 0)))
    if (targets.length === 1) {
      onChange([{ ...targets[0], weight: 100 }])
      return
    }
    const otherIdx = targets.map((_, j) => j).filter((j) => j !== i)
    const prev = otherIdx.map((j) => targets[j].weight ?? 0)
    const prevSum = prev.reduce((s, x) => s + x, 0)
    const remaining = 100 - v
    const shares = prev.map((p, k) =>
      prevSum > 0 ? Math.round((p / prevSum) * remaining) : Math.round(remaining / otherIdx.length))
    // Push rounding drift onto the largest "other" so the grid totals exactly 100.
    const drift = remaining - shares.reduce((s, x) => s + x, 0)
    if (shares.length) {
      let big = 0
      for (let k = 1; k < shares.length; k++) if (shares[k] > shares[big]) big = k
      shares[big] = Math.max(0, shares[big] + drift)
    }
    const out = targets.map((t) => ({ ...t }))
    out[i].weight = v
    otherIdx.forEach((j, k) => { out[j].weight = shares[k] })
    onChange(out)
  }

  const percentOf = (i: number) => {
    if (lbTotal <= 0) return Math.round(100 / Math.max(1, targets.length))
    return Math.round(((targets[i].weight ?? 0) / lbTotal) * 100)
  }

  // Shared provider + model cells (2 grid children), used by both layouts.
  const fieldCells = (t: AliasTarget, i: number) => {
    const modelOpts = ws.byProvider[t.provider] || []
    const datalistId = `${testIdPrefix}-models-${i}`
    return (
      <>
        <select
          className="input py-1.5 text-xs"
          value={t.provider}
          onChange={(e) => {
            const newProvider = e.target.value as ProviderType
            const firstModel = ws.byProvider[newProvider]?.[0]?.model_id || ''
            updateTarget(i, { provider: newProvider, model_id: firstModel })
          }}
          data-testid={`${testIdPrefix}-${i}-provider`}
        >
          {ws.configuredProviders.length === 0 ? (
            <option value="">(add providers first)</option>
          ) : (
            ws.configuredProviders.map((p) => <option key={p} value={p}>{p}</option>)
          )}
        </select>
        <div className="relative">
          <input
            className="input mono text-xs"
            value={t.model_id}
            onChange={(e) => updateTarget(i, { model_id: e.target.value })}
            placeholder={modelOpts[0]?.model_id || 'provider model id'}
            list={datalistId}
            data-testid={`${testIdPrefix}-${i}-model`}
            spellCheck={false}
          />
          <datalist id={datalistId}>
            {modelOpts.map((m) => <option key={m.model_id} value={m.model_id} label={m.label} />)}
          </datalist>
          {ws.liveProviders.has(t.provider) && (
            <div className="text-[10px] text-ok mt-0.5" data-testid={`${testIdPrefix}-${i}-live`}>
              ✓ {modelOpts.length} models this {t.provider} account can actually reach
            </div>
          )}
        </div>
      </>
    )
  }

  return (
    <div className="space-y-3">
      {/* Strategy selector - gives an immediate mental model of what's being configured. */}
      <div className="flex items-center gap-1 p-1 bg-app/60 border border-border rounded-lg"
           role="tablist" aria-label="Routing strategy">
        <button
          type="button" role="tab" aria-selected={mode === 'failover'}
          onClick={() => switchMode('failover')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
            mode === 'failover' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-white'
          }`}
          data-testid={`${testIdPrefix}-mode-failover`}
        >
          <Workflow size={13} /> Failover chain
        </button>
        <button
          type="button" role="tab" aria-selected={mode === 'loadbalance'}
          onClick={() => switchMode('loadbalance')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
            mode === 'loadbalance' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-white'
          }`}
          data-testid={`${testIdPrefix}-mode-loadbalance`}
        >
          <Scale size={13} /> Split traffic
        </button>
      </div>

      {showHelp && (
        <p className="text-[11.5px] text-muted px-0.5" data-testid={`${testIdPrefix}-mode-hint`}>
          {mode === 'failover'
            ? 'Sequential backup: 100% of traffic goes to the top target. If it errors (timeout, rate-limit, upstream 5xx), the gateway falls down the chain. Backups get 0% traffic while the primary is healthy.'
            : 'Concurrent load-balancing: each request is routed to a target weighted by its percentage share, so multiple models serve live traffic at once. The others still act as failover if the chosen one errors.'}
        </p>
      )}

      {showHelp && (
        <p className="text-[11px] text-muted px-0.5">
          You only pick <b className="text-gray-300">provider + model</b> here. Connection details -
          <span className="mono"> AWS region</span>, <span className="mono">base_url</span>, <span className="mono">api_version</span> -
          are inherited automatically from that provider's config (set once in <b className="text-gray-300">Admin → Providers</b>),
          so the same alias works no matter which region/endpoint the provider uses.
        </p>
      )}

      {isSafeDefault && (
        <div className="flex items-start gap-2 bg-warn/10 border border-warn/40 rounded-lg p-2.5 text-[11.5px] text-warn"
             data-testid={`${testIdPrefix}-safe-default-notice`}>
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>
            <strong>Traffic still flows 100% to your primary.</strong> Switching to split traffic does not start
            load-balancing on its own - assign a percentage to a secondary target below to deliberately begin
            concurrent routing.
          </span>
        </div>
      )}

      {noProviders && (
        <div className="bg-warn/10 border border-warn/40 rounded-lg p-3 text-[12px] text-warn">
          This workspace has no providers configured yet. Add one under <span className="font-semibold">Admin → Providers</span> first; the dropdowns below will then populate with usable models.
        </div>
      )}

      <div className="space-y-2" data-testid={`${testIdPrefix}-list`}>
        {targets.map((t, i) => {
          const modelOpts = ws.byProvider[t.provider] || []
          const noModelHint = modelOpts.length === 0 && t.provider && !noProviders

          if (mode === 'failover') {
            return (
              <div key={i} data-testid={`${testIdPrefix}-row-${i}`}>
                {i > 0 && (
                  <div className="flex items-center gap-1.5 pl-1 py-1 text-[10px] uppercase tracking-wider text-muted">
                    <ArrowDown size={12} /> if step {i} fails (timeout · rate-limit · upstream error)
                  </div>
                )}
                <div className="bg-app border border-border rounded-xl p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-[10px] uppercase tracking-wider font-semibold ${
                      i === 0 ? 'text-success' : 'text-warn'
                    }`}>
                      {i === 0 ? '▶ step 1 · primary' : `step ${i + 1} · fallback`}
                    </span>
                    <div className="ml-auto flex items-center gap-1">
                      <button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up"
                              className="p-1 text-muted hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
                              data-testid={`${testIdPrefix}-${i}-up`}>
                        <ArrowUp size={13} />
                      </button>
                      <button type="button" disabled={i === targets.length - 1} onClick={() => move(i, 1)} aria-label="Move down"
                              className="p-1 text-muted hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
                              data-testid={`${testIdPrefix}-${i}-down`}>
                        <ArrowDown size={13} />
                      </button>
                      <button type="button" onClick={() => removeTarget(i)} aria-label="Remove target"
                              className="p-1 text-danger hover:bg-danger/10 rounded"
                              data-testid={`${testIdPrefix}-${i}-delete`}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-2">
                    {fieldCells(t, i)}
                  </div>
                  {noModelHint && (
                    <div className="text-[10.5px] text-muted mt-1">
                      No catalog models found for {t.provider}; type a model id manually.
                    </div>
                  )}
                </div>
              </div>
            )
          }

          // Load-balance layout: percentage + visual bar, order is irrelevant.
          const p = percentOf(i)
          return (
            <div key={i} className="bg-app border border-border rounded-xl p-3"
                 data-testid={`${testIdPrefix}-row-${i}`}>
              <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr_auto] gap-2 items-center">
                {fieldCells(t, i)}
                <div className="flex items-center gap-1">
                  <input
                    className="input py-1.5 text-xs w-[58px] text-right"
                    type="number" min={0} max={100} step={1}
                    value={t.weight ?? 0}
                    onChange={(e) => setPercent(i, parseFloat(e.target.value))}
                    aria-label="Traffic percentage"
                    data-testid={`${testIdPrefix}-${i}-weight`}
                  />
                  <span className="text-xs text-muted">%</span>
                  <button type="button" onClick={() => removeTarget(i)} aria-label="Remove target"
                          className="p-1 text-danger hover:bg-danger/10 rounded ml-1"
                          data-testid={`${testIdPrefix}-${i}-delete`}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-border/60 overflow-hidden" aria-hidden>
                <div className="h-full bg-accent rounded-full transition-all" style={{ width: `${p}%` }} />
              </div>
              {noModelHint && (
                <div className="text-[10.5px] text-muted mt-1">
                  No catalog models found for {t.provider}; type a model id manually.
                </div>
              )}
            </div>
          )
        })}
      </div>

      {mode === 'loadbalance' && targets.length > 0 && (
        <div className="flex items-center justify-end gap-1.5 text-[11px] px-0.5"
             data-testid={`${testIdPrefix}-total`}>
          <span className="text-muted">Total</span>
          <span className={lbTotal === 100 ? 'text-success font-semibold' : 'text-warn font-semibold'}>
            {lbTotal}%
          </span>
        </div>
      )}

      <button
        type="button"
        className="btn-ghost text-xs"
        onClick={addTarget}
        disabled={noProviders}
        data-testid={`${testIdPrefix}-add`}
      >
        <Plus size={12} /> {targets.length === 0
          ? 'Add primary target'
          : mode === 'failover' ? 'Add fallback step' : 'Add model target'}
      </button>

      {showErrors && !validation.ok && (
        <div className="text-[11px] text-danger space-y-0.5" role="alert">
          {validation.errors.map((e, i) => <div key={i}>• {e}</div>)}
        </div>
      )}
    </div>
  )
}
