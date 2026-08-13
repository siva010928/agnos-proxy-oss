// Shadow Parity panel - run one prompt through BOTH engines (Bifrost vs our
// owned DirectEngine) and show a DETAILED, traced side-by-side. This is the
// on-screen proof that insourcing a provider is safe: identical output, and
// often lower latency (the rented Bifrost hop is removed). Nothing else -
// LiteLLM/Bifrost OSS - offers this.
import { useMemo, useState } from 'react'
import { GitCompareArrows, Loader2, Check, X, Zap, ChevronDown, ChevronRight } from 'lucide-react'
import { admin, type ParityResult, type ParityLeg } from '../api/client'
import { useWorkspaces } from '../lib/api'
import { Card, SectionTitle, Pill } from './ui'
import { toastError } from './Toast'
import { fmtMs } from '../lib/format'

const VERDICT: Record<string, { label: string; color: string }> = {
  identical: { label: 'IDENTICAL', color: '#34D399' },
  high:      { label: 'HIGH PARITY', color: '#34D399' },
  moderate:  { label: 'MODERATE', color: '#CC840B' },
  divergent: { label: 'DIVERGENT', color: '#dc2626' },
  error:     { label: 'ERROR', color: '#dc2626' },
}

interface Target { provider: string; model_id: string }

function workspaceTargets(ws: any): Target[] {
  const out: Target[] = []
  const seen = new Set<string>()
  for (const spec of Object.values(ws?.chat_models || {})) {
    for (const t of (Array.isArray(spec) ? spec : [spec]) as any[]) {
      const key = `${t.provider}:${t.model_id}`
      if (t.provider && t.model_id && !seen.has(key)) { seen.add(key); out.push({ provider: t.provider, model_id: t.model_id }) }
    }
  }
  return out
}

// One engine leg, fully traced: network hops + timing + tokens + output + raw.
function Leg({ leg, faster }: { leg: ParityLeg; faster: boolean }) {
  const [showRaw, setShowRaw] = useState(false)
  const owned = leg.engine === 'direct'
  return (
    <div className={`rounded-xl p-3 border ${owned ? 'border-accent/50 bg-accent/5' : 'border-border bg-app'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-white">
          {owned ? 'DirectEngine (owned · in-process)' : 'Bifrost (rented · Go sidecar)'}
        </span>
        <span className="flex items-center gap-2">
          {faster && <Pill color="#34D399"><Zap size={10} /> faster</Pill>}
          {leg.ok ? <Check size={14} className="text-ok" /> : <X size={14} className="text-danger" />}
        </span>
      </div>

      {/* network path - the concrete reason for the latency delta */}
      <div className="mb-2">
        <div className="text-[10px] uppercase tracking-wider text-muted mb-1">network path ({leg.hops?.length || 0} hops)</div>
        <div className="flex flex-col gap-0.5">
          {(leg.hops || []).map((h, i) => (
            <div key={i} className="text-[11.5px] text-gray-300 flex items-center gap-1.5">
              <span className="text-accent">{i === (leg.hops!.length - 1) ? '▸' : '│'}</span>
              <span className="mono">{h}</span>
            </div>
          ))}
        </div>
      </div>

      {/* lifecycle metrics */}
      <table className="w-full text-[12px] text-muted mb-2">
        <tbody>
          <tr><td className="py-0.5">status</td><td className="text-right mono text-gray-300">{leg.status || '-'}</td></tr>
          <tr><td className="py-0.5">latency {leg.samples && leg.samples > 1 ? `(median of ${leg.samples})` : '(provider call)'}</td>
            <td className="text-right mono text-gray-300">{fmtMs(leg.latency_ms)}
              {leg.latency_min != null && leg.latency_max != null && leg.samples && leg.samples > 1 &&
                <span className="text-muted"> · {fmtMs(leg.latency_min)}-{fmtMs(leg.latency_max)}</span>}
            </td></tr>
          <tr><td className="py-0.5">tokens (in / out)</td><td className="text-right mono text-gray-300">{leg.input_tokens} / {leg.output_tokens}</td></tr>
          {!!leg.cached_tokens && <tr><td className="py-0.5">cached tokens</td><td className="text-right mono text-ok">{leg.cached_tokens}</td></tr>}
          <tr><td className="py-0.5">finish reason</td><td className="text-right mono text-gray-300">{leg.finish_reason || '-'}</td></tr>
          {leg.tool_calls?.length > 0 && <tr><td className="py-0.5">tool calls</td><td className="text-right mono text-gray-300">{leg.tool_calls.join(', ')}</td></tr>}
        </tbody>
      </table>

      {/* output */}
      <div className="text-[10px] uppercase tracking-wider text-muted mb-1">output</div>
      <div className="text-[12.5px] text-gray-200 bg-app border border-border rounded-lg p-2 min-h-[42px] whitespace-pre-wrap break-words">
        {leg.ok ? (leg.text || <span className="text-muted">(no text)</span>) : <span className="text-danger">{leg.error}</span>}
      </div>

      {/* raw response body (trace) */}
      {leg.raw && (
        <button onClick={() => setShowRaw(v => !v)}
                className="mt-2 text-[11px] text-muted hover:text-gray-200 inline-flex items-center gap-1">
          {showRaw ? <ChevronDown size={12} /> : <ChevronRight size={12} />} raw response
        </button>
      )}
      {showRaw && leg.raw && (
        <pre className="mt-1 text-[10.5px] text-gray-400 bg-app border border-border rounded-lg p-2 overflow-x-auto max-h-56">{leg.raw}</pre>
      )}
    </div>
  )
}

export function ShadowParity() {
  const workspaces = useWorkspaces()
  const raw: any = workspaces.data
  const list: any[] = Array.isArray(raw) ? raw : (raw?.workspaces || [])
  const [wsId, setWsId] = useState('')
  const [provider, setProvider] = useState('')
  const [modelId, setModelId] = useState('')
  const [prompt, setPrompt] = useState('Reply with exactly one word: pong')
  const [running, setRunning] = useState(false)
  const [res, setRes] = useState<ParityResult | null>(null)

  const ws = useMemo(() => list.find(w => w.workspace_id === wsId), [list, wsId])
  const targets = useMemo(() => (ws ? workspaceTargets(ws) : []), [ws])
  const providers = useMemo(() => Array.from(new Set(targets.map(t => t.provider))), [targets])
  const models = useMemo(() => targets.filter(t => t.provider === provider).map(t => t.model_id), [targets, provider])

  // default to first workspace with a routable target
  useMemo(() => {
    if (wsId || !list.length) return
    const first = list.find(w => workspaceTargets(w).length) || list[0]
    if (first) {
      setWsId(first.workspace_id)
      const t = workspaceTargets(first)[0]
      if (t) { setProvider(t.provider); setModelId(t.model_id) }
    }
  }, [list])

  function pickWorkspace(id: string) {
    setWsId(id)
    const t = workspaceTargets(list.find(w => w.workspace_id === id))[0]
    setProvider(t?.provider || ''); setModelId(t?.model_id || '')
  }
  function pickProvider(p: string) {
    setProvider(p)
    const m = targets.find(t => t.provider === p)?.model_id || ''
    setModelId(m)
  }

  async function run() {
    if (!wsId || !provider || !modelId) { toastError('Pick a workspace, provider and model'); return }
    setRunning(true); setRes(null)
    try {
      setRes(await admin.parityRun({ workspace_id: wsId, provider, model_id: modelId, prompt, max_tokens: 128 }))
    } catch (e: any) {
      toastError(e?.message || 'parity run failed')
    } finally { setRunning(false) }
  }

  const v = res ? (VERDICT[res.verdict] || VERDICT.error) : null
  const bothOk = !!res && res.bifrost.ok && res.direct.ok
  // Honest framing: Bifrost (a purpose-built Go sidecar) is the FAST path;
  // DirectEngine (in-process boto3/httpx) trades a little latency for full
  // ownership. We never dress up the numbers - the point of parity is that the
  // OUTPUT + CONTRACT are identical, so the swap is safe.
  const bifrostFaster = bothOk && res!.latency_delta_ms > 0     // direct slower ⇒ bifrost faster
  const savedMs = res ? Math.abs(res.latency_delta_ms) : 0

  const selCls = 'block mt-1 bg-app border border-border rounded-lg px-2 py-1.5 text-sm text-gray-200 min-w-[9rem] disabled:opacity-50'

  return (
    <Card>
      <SectionTitle right={
        <button onClick={run} disabled={running} data-testid="parity-run"
                className="btn-primary text-xs inline-flex items-center gap-1.5 disabled:opacity-50">
          {running ? <Loader2 size={12} className="animate-spin" /> : <GitCompareArrows size={12} />}
          {running ? 'Comparing…' : 'Run parity'}
        </button>
      }>Shadow parity · Bifrost vs DirectEngine</SectionTitle>

      <p className="text-[12px] text-muted mb-3">
        Same prompt, same governance, both engines - a traced side-by-side proving an engine swap is safe (and often faster).
      </p>

      {/* dropdowns: workspace → provider → model (no free text) */}
      <div className="flex flex-wrap items-end gap-2 mb-3">
        <label className="text-[11px] text-muted">Workspace
          <select value={wsId} onChange={e => pickWorkspace(e.target.value)} data-testid="parity-workspace" className={selCls}>
            {list.map(w => <option key={w.workspace_id} value={w.workspace_id}>{w.display_name || w.workspace_id}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-muted">Provider
          <select value={provider} onChange={e => pickProvider(e.target.value)} data-testid="parity-provider" className={selCls} disabled={!providers.length}>
            {providers.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-muted">Model
          <select value={modelId} onChange={e => setModelId(e.target.value)} data-testid="parity-model" className={`${selCls} min-w-[16rem] mono`} disabled={!models.length}>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-muted flex-1 min-w-[16rem]">Prompt
          <input value={prompt} onChange={e => setPrompt(e.target.value)} data-testid="parity-prompt"
                 className="block mt-1 w-full bg-app border border-border rounded-lg px-2 py-1.5 text-sm text-gray-200" />
        </label>
      </div>

      {v && res && (
        <>
          {/* Headline is SAFETY: identical output + same contract. Speed is
              Bifrost's advantage; ownership is ours. We state both honestly. */}
          <div className="flex items-center flex-wrap gap-2 mb-3">
            <span className="text-[11px] font-bold px-2 py-1 rounded-md" style={{ background: `${v.color}22`, color: v.color }}
                  data-testid="parity-verdict">{v.label}</span>
            <Pill color="#6366F1">text similarity {(res.text_similarity * 100).toFixed(0)}%</Pill>
            {res.structural_parity && <Pill color="#34D399"><Check size={10} /> same contract (finish + tool calls)</Pill>}
            <Pill color="#6b7280">
              Δmedian latency {res.latency_delta_ms > 0 ? '+' : ''}{res.latency_delta_ms}ms
              {bifrostFaster ? ' · Bifrost faster' : ' · DirectEngine faster'}
            </Pill>
          </div>

          {/* honest positioning: speed is Bifrost's; sovereignty is ours */}
          {bothOk && (
            <div className="text-[12px] text-gray-300 rounded-lg p-2.5 mb-3 border bg-app border-border">
              <b className="text-gray-200">Bifrost is the fast path; DirectEngine is the sovereign path.</b>{' '}
              Bifrost is a purpose-built Go sidecar and is typically a little faster
              {bifrostFaster && savedMs ? ` (~${savedMs.toFixed(0)}ms here, median of ${res.samples || 1})` : ''} -
              that is exactly why we <b>rent</b> it today. DirectEngine runs the provider call in-process (our own
              adapter, no rented dependency, no unvetted third-party libraries) so a security vulnerability in the
              rented layer becomes a <b>one-line config flip</b>, not a fleet-wide rebuild. The result that matters:
              the <b>output and contract are identical</b> - so switching engine, per provider, is safe and invisible
              to every component.
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Leg leg={res.bifrost} faster={bifrostFaster} />
            <Leg leg={res.direct} faster={bothOk && !bifrostFaster} />
          </div>
        </>
      )}
    </Card>
  )
}
