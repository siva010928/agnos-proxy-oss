import { Link } from 'react-router-dom'
import { Activity, DollarSign, Cpu, ShieldAlert, Boxes, Timer } from 'lucide-react'
import { useFeed } from '../App'
import { useTimeseries, useWorkspaces, useCost } from '../lib/api'
import { LiveFeed } from '../components/LiveFeed'
import { Card, SectionTitle, StatTile, LiveDot, ProviderBadge, Skeleton } from '../components/ui'
import { Sparkline } from '../components/Charts'
import { fmtTokens } from '../lib/format'
import { useCurrency } from '../lib/currency'

export function Overview() {
  const { events, connected } = useFeed()
  const { format: fmtCost, symbol, convert, currency } = useCurrency()
  // Overview shows ALL-TIME totals (not the default rolling window) - pass a far-back `from`
  // so the cards reflect every governed request the gateway has ever seen.
  const ts = useTimeseries({ granularity: 'day', from: '2020-01-01' })
  const ws = useWorkspaces()
  const costByProv = useCost('provider')

  const points: any[] = ts.data?.points || []
  const sum = (k: string) => points.reduce((a, p) => a + (p[k] || 0), 0)
  const reqSpark = points.slice(-14).map((p) => p.requests || 0)
  // Sparkline values are converted to selected currency so y-axis matches the StatTile.
  const costSpark = points.slice(-14).map((p) => convert(p.cost_usd || 0))
  const tokSpark = points.slice(-14).map((p) => (p.input_tokens || 0) + (p.output_tokens || 0))
  const totalReq = sum('requests')
  const totalTok = sum('input_tokens') + sum('output_tokens')
  const totalCostUsd = sum('cost_usd')
  const totalCostLocal = convert(totalCostUsd)
  const totalErrors = sum('errors')
  const successRate = totalReq ? ((totalReq - totalErrors) / totalReq * 100) : 0
  const avgLat = points.length ? points.reduce((a, p) => a + (p.avg_latency_ms || 0), 0) / points.length : 0
  const workspaces = (ws.data?.workspaces || [])
  const empty = !ts.isLoading && points.length === 0

  // Cost decimals: small absolute values need more precision (e.g. ₹6.23 = $0.07).
  const costDecimals = totalCostLocal >= 1000 ? 0 : totalCostLocal >= 10 ? 2 : 4

  return (
    <div className="space-y-5">
      {/* Guided orientation banner */}
      <div className="bg-gradient-to-r from-accent/10 to-violet-500/10 border border-accent/20 rounded-lg p-4 flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-white">Welcome to Agnos Proxy</div>
          <div className="text-xs text-muted mt-0.5">
            OpenAI-compatible governance proxy. Any framework, one <code className="text-accent">base_url</code>, full attribution + guardrails + cost control.
          </div>
        </div>
        <Link to="/playground" className="px-4 py-2 bg-accent hover:bg-accent/90 text-black text-xs font-medium rounded-lg transition whitespace-nowrap">
          Open Playground →
        </Link>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Overview</h1>
          <p className="text-muted text-sm">Governed multi-provider LLM traffic - Client → Workspace → Component attribution</p>
        </div>
        <LiveDot on={connected} />
      </div>

      {ts.isLoading ? (
        <div className="grid grid-cols-6 gap-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={120} />)}</div>
      ) : empty ? (
        <Card>
          <div className="py-12 text-center text-muted text-sm">
            <div className="text-lg text-gray-200 mb-2">No traffic yet</div>
            <div>Fire your first request through the gateway - or <Link to="/playground" className="text-accent hover:underline">try the Playground</Link> to see governance in action instantly.</div>
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          <StatTile label="Requests" value={totalReq} spark={reqSpark} color="#6366F1" icon={<Activity size={15} />} />
          <StatTile label="Tokens" value={totalTok >= 1e6 ? totalTok / 1e6 : totalTok >= 1000 ? totalTok / 1000 : totalTok}
                   suffix={totalTok >= 1e6 ? 'M' : totalTok >= 1000 ? 'k' : ''}
                   decimals={totalTok >= 1e6 ? 2 : totalTok >= 1000 ? 1 : 0}
                   spark={tokSpark} color="#60A5FA" icon={<Cpu size={15} />} />
          <StatTile label={`Cost (${currency})`} value={totalCostLocal} prefix={symbol} decimals={costDecimals} spark={costSpark} color="#FBBF24" icon={<DollarSign size={15} />} />
          <StatTile label="Avg latency" value={avgLat} suffix="ms" color="#2DD4BF" icon={<Timer size={15} />} />
          <StatTile label="Success rate" value={successRate} suffix="%" decimals={1} color="#34D399" icon={<ShieldAlert size={15} />} />
          <StatTile label="Workspaces" value={workspaces.length} color="#A78BFA" icon={<Boxes size={15} />} />
        </div>
      )}

      {!empty && (
        <>
          <Card className="p-4">
            <SectionTitle right={<span className="text-[11px] text-muted tabular-nums">daily requests · all-time</span>}>Throughput</SectionTitle>
            <div className="h-10"><Sparkline data={reqSpark.length ? reqSpark : [0, 0]} color="#6366F1" /></div>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
            <Card className="lg:col-span-3 p-4">
              <SectionTitle right={<Link to="/live" className="text-[11px] text-accent hover:underline">view all →</Link>}>Live request feed</SectionTitle>
              <LiveFeed events={events} height="56vh" />
            </Card>
            <div className="lg:col-span-2 space-y-5">
              <Card className="p-4">
                <SectionTitle right={<Link to="/routing" className="text-[11px] text-accent hover:underline">routing map →</Link>}>Workspaces → Providers</SectionTitle>
                <div className="space-y-2.5">
                  {workspaces.map((w: any) => {
                    const alias = Object.keys(w.chat_models || {})[0]
                    const targets = (w.chat_models || {})[alias] || []
                    return (
                      <div key={w.workspace_id} className="flex items-center justify-between text-sm">
                        <span className="text-gray-200 truncate">{w.display_name}</span>
                        <div className="flex items-center gap-1 shrink-0">
                          {targets.map((t: any, i: number) => (
                            <span key={i} className="flex items-center gap-1">
                              {i > 0 && <span className="text-muted text-xs">→</span>}
                              <ProviderBadge provider={t.provider} />
                            </span>
                          ))}
                        </div>
                      </div>
                    )
                  })}
                  {!workspaces.length && <div className="text-muted text-sm py-4 text-center">No workspaces provisioned yet.</div>}
                </div>
              </Card>
              <Card className="p-4">
                <SectionTitle>Cost by provider</SectionTitle>
                <div className="space-y-2">
                  {(costByProv.data?.rows || []).filter((r: any) => r.key).sort((a: any, b: any) => b.cost_usd - a.cost_usd).map((r: any) => (
                    <div key={r.key} className="flex items-center justify-between text-sm">
                      <ProviderBadge provider={r.key} />
                      <span className="text-gray-300 mono tabular-nums">{fmtCost(r.cost_usd)} · {fmtTokens(r.input_tokens + r.output_tokens)} tok</span>
                    </div>
                  ))}
                  {!(costByProv.data?.rows || []).filter((r: any) => r.key).length && (
                    <div className="text-muted text-sm py-4 text-center">No cost data yet.</div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
