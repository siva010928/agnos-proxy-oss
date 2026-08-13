import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Server, Database, Radio, Boxes, CircuitBoard, Check, RefreshCw, Loader2 } from 'lucide-react'
import { useProviders, useHealth, api } from '../lib/api'
import { admin, type EngineName } from '../api/client'
import { withToast, toastOk, toastError } from '../components/Toast'
import { Card, SectionTitle, Pill, ProviderBadge } from '../components/ui'
import { fmtMs } from '../lib/format'

export function EngineHealth() {
  const providers = useProviders()
  const health = useHealth()
  const [busy, setBusy] = useState(false)
  const [reprobing, setReprobing] = useState(false)
  const engine = providers.data?.engine || '…'
  const provMap: Record<string, any> = providers.data?.providers || {}
  const breakers: Record<string, any> = providers.data?.breakers || {}

  // Engines are discovered from the gateway: only those actually AVAILABLE (in-process,
  // or a reachable sidecar) are offered - a single-engine deploy shows just its engine.
  const catalog = useQuery<any>({ queryKey: ['engine-catalog'], queryFn: () => api('/admin/engine/catalog'), refetchInterval: 15000 })
  const activeEngine: string = catalog.data?.current_engine || engine
  const engineList = Object.entries(catalog.data?.engines || {})
    .map(([id, m]: any) => ({ id, ...m }))
    .filter((e: any) => e.available || e.id === activeEngine)

  async function swapTo(name: EngineName) {
    if (name === activeEngine || busy) return
    setBusy(true)
    try {
      await withToast(() => admin.setEngine(name), `Engine swapped to ${name}`)
      await Promise.all([catalog.refetch(), providers.refetch()])
    } finally { setBusy(false) }
  }

  // Re-probe MUST bypass the 60s server-side probe cache, otherwise clicking it
  // just re-reads the stale snapshot (the bug). force=true runs a fresh live
  // probe; we then refetch the polling query so the UI reflects the new result.
  async function reprobe() {
    setReprobing(true)
    try {
      await api('/health/providers?force=true')
      await providers.refetch()
      toastOk('Providers re-probed')
    } catch (e: any) {
      toastError(e?.message || 're-probe failed')
    } finally {
      setReprobing(false)
    }
  }

  // EchoEngine is a $0 in-process upstream used ONLY by the integration test
  // suite (BVT). It must never be the operator-facing default; if the gateway
  // is currently on echo (e.g. from a test run), surface a clear banner so the
  // operator knows to swap back to bifrost / direct.
  const onTestEngine = engine === 'echo'

  // Friendly engine label for the card header
  const engineLabel = engine === 'bifrost' ? 'Bifrost (Go sidecar)'
                    : engine === 'direct'  ? 'DirectEngine (boto3, in-process Bedrock)'
                    : engine === 'echo'    ? 'EchoEngine (test-only - $0 in-process upstream)'
                    : engine

  const engineName = engine === 'bifrost' ? 'Bifrost engine'
                   : engine === 'direct'  ? 'Direct engine'
                   : engine === 'echo'    ? 'Echo engine (test)'
                   : `${engine} engine`
  const kafka = health.data?.kafka
  const redis = health.data?.redis
  const boolState = (ok: any) => ok ? 'up' : 'down'

  const infra = [
    { name: 'Postgres', icon: Database, state: boolState(providers.data?.db_healthy), note: providers.data?.db_healthy ? 'connected' : 'unreachable' },
    { name: engineName, icon: Server, state: boolState(providers.data?.engine_healthy), note: providers.data?.engine_healthy ? 'healthy' : 'unreachable' },
    { name: 'Kafka', icon: Radio, state: kafka?.state || 'disabled', note: kafka?.topic || kafka?.detail || 'agnos-proxy.governance.v1' },
    { name: 'Redis', icon: Boxes, state: redis?.state || 'disabled', note: redis?.detail || 'rate-limit/cache' },
  ]

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Engine & Health</h1>
        <p className="text-muted text-sm">Swap the BackendEngine live - governance, dashboard and contract stay identical</p>
      </div>

      {onTestEngine && (
        <Card className="border-warn/40 bg-warn/5">
          <div className="flex items-start gap-3">
            <CircuitBoard size={18} className="text-warn shrink-0 mt-0.5" />
            <div>
              <div className="text-sm font-semibold text-white">Test engine active</div>
              <div className="text-[12px] text-muted">
                The gateway is currently running on <span className="mono text-warn">EchoEngine</span> -
                a deterministic, $0 in-process upstream used only by the BVT integration suite.
                Real provider calls will not happen until you swap to <span className="mono">bifrost</span> (default)
                or <span className="mono">direct</span> below.
              </div>
              <button className="btn-primary text-xs mt-3"
                      onClick={() => swapTo('bifrost')}
                      disabled={busy}
                      data-testid="engine-restore-bifrost">
                Switch to Bifrost (default)
              </button>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="lg:col-span-1">
          <SectionTitle>Backend engine</SectionTitle>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-accent to-violet-500 flex items-center justify-center text-white">
              {busy ? <Loader2 className="animate-spin" size={20} /> : <CircuitBoard size={22} />}
            </div>
            <div>
              <div className="text-lg font-semibold text-white capitalize" data-testid="engine-name">{engine}</div>
              <div className="text-[11px] text-muted">{engineLabel}</div>
            </div>
          </div>
          <div className="space-y-2" data-testid="engine-swap-row">
            <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Swap the backend engine</div>
            {engineList.map((e: any) => {
              const active = activeEngine === e.id
              const tag = `${e.stateful ? 'stateful' : 'stateless'}${e.runtime ? ' · ' + e.runtime : ''}`
              return (
                <button key={e.id} onClick={() => swapTo(e.id as EngineName)} disabled={busy || active}
                  data-testid={`engine-opt-${e.id}`}
                  className="w-full text-left rounded-xl p-2.5 border transition-colors disabled:cursor-default"
                  style={{
                    background: active ? 'var(--color-accent-soft, rgba(139,124,246,0.12))' : 'var(--color-app)',
                    borderColor: active ? 'var(--color-accent)' : 'var(--color-border)',
                  }}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium" style={{ color: active ? 'var(--color-accent)' : 'var(--color-text)' }}>
                      {e.label}{active ? ' · active' : ''}
                    </span>
                    <span className="text-[9.5px] uppercase tracking-wider text-muted">{tag}</span>
                  </div>
                  <div className="text-[10.5px] text-muted mt-0.5">{e.tagline || e.capability}</div>
                </button>
              )
            })}
            {!engineList.length && <div className="text-[11px] text-muted py-2">probing engines…</div>}
          </div>
          <Pill color="#34D399"><Check size={11} /> governance unaffected by swap</Pill>
        </Card>

        <Card className="lg:col-span-2">
          <SectionTitle right={
            <button onClick={reprobe} disabled={reprobing}
                    className="text-[11px] inline-flex items-center gap-1 px-2 py-1 rounded-md border border-border hover:bg-elevated disabled:opacity-50"
                    style={{ color: 'var(--color-accent)' }} data-testid="provider-reprobe">
              <RefreshCw size={11} className={reprobing ? 'animate-spin' : ''} />
              {reprobing ? 'Probing…' : 'Re-probe'}
            </button>
          }>Provider health</SectionTitle>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(provMap).map(([p, info]: any) => (
              <div key={p} className="rounded-xl p-3 border"
                   style={{
                     background: 'var(--color-app)',
                     borderColor: info.reachable ? 'var(--color-border)' : 'var(--color-danger)',
                     borderLeftWidth: info.reachable ? 1 : 3,
                   }}>
                <div className="flex items-center justify-between mb-2">
                  <ProviderBadge provider={p} />
                  {info.reachable
                    ? <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: 'var(--color-ok)' }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} /> reachable
                      </span>
                    : <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: 'var(--color-danger)' }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-danger)' }} /> down
                      </span>}
                </div>
                {info.reachable ? (
                  <div className="text-[11px] text-muted">responded in {fmtMs(info.latency_ms || 0)}</div>
                ) : (
                  <div className="rounded-md px-2.5 py-2"
                       style={{ background: 'var(--color-danger-soft, rgba(248,113,113,0.07))', borderLeft: '2px solid var(--color-danger)' }}>
                    <div className="text-[9px] uppercase tracking-wider text-danger mb-1">connection error</div>
                    <div className="mono text-[10.5px] leading-relaxed break-words whitespace-pre-wrap"
                         style={{ color: 'var(--color-text-secondary)' }}>
                      {info.error || 'unreachable'}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {!Object.keys(provMap).length && (
              <div className="text-muted text-sm col-span-3 py-6 text-center inline-flex items-center justify-center gap-2">
                <Loader2 size={14} className="animate-spin" /> probing providers…
              </div>
            )}
          </div>

          {/* Circuit breakers - compact, scannable, with a calm healthy default */}
          <div className="mt-4 pt-3 border-t border-border">
            <div className="flex items-center gap-2 mb-2">
              <CircuitBoard size={13} className="text-muted" />
              <span className="text-[11px] uppercase tracking-wider text-muted">Circuit breakers</span>
              {Object.keys(breakers).length > 0 && (
                <span className="text-[10px] text-muted">
                  · {Object.values(breakers).filter((b: any) => b.open).length} open / {Object.keys(breakers).length} total
                </span>
              )}
            </div>
            {Object.keys(breakers).length === 0 ? (
              <div className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--color-ok)' }}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} />
                All closed - every provider path healthy
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {Object.entries(breakers).map(([k, b]: any) => (
                  <span key={k}
                        className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-md border"
                        style={b.open
                          ? { borderColor: 'var(--color-danger)', color: 'var(--color-danger)', background: 'var(--color-danger-soft, rgba(248,113,113,0.07))' }
                          : { borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
                        title={b.open ? 'Open - failing fast, not routing here' : `Closed - ${b.fails} recent fails`}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: b.open ? 'var(--color-danger)' : 'var(--color-ok)' }} />
                    {k} · {b.open ? 'open' : 'closed'}
                    {!b.open && b.fails > 0 && <span className="text-muted">({b.fails})</span>}
                  </span>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <SectionTitle>Infrastructure</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {infra.map((s) => {
            const color = s.state === 'up' ? 'text-ok' : s.state === 'down' ? 'text-danger' : 'text-muted'
            const statusText = s.state === 'up' ? (s.note || 'healthy')
                             : s.state === 'down' ? 'down'
                             : `not configured · ${s.note}`
            return (
              <div key={s.name} className="bg-app rounded-xl p-3 border border-border flex items-center gap-3">
                <s.icon size={18} className={color} />
                <div className="min-w-0">
                  <div className="text-sm text-gray-200">{s.name}</div>
                  <div className="text-[11px] text-muted truncate" title={statusText}>{statusText}</div>
                </div>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}
