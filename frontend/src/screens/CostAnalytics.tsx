import { useMemo, useState } from 'react'
import { Activity, AlertCircle, Cpu, DollarSign, LineChart as IcLineChart, Timer, Zap } from 'lucide-react'
import { useFeed } from '../App'
import { useBreakdown, useCost, useTimeseries, useFacets, AnalyticsFilters } from '../lib/api'
import { HierarchicalFilters } from '../components/Filters'
import { Card, EmptyState, LiveDot, ProviderBadge, SectionTitle, Skeleton, StatTile } from '../components/ui'
import { AreaTime, MultiLine, fmtCount, fmtMsAxis, fmtUSDAxis } from '../components/Charts'
import { fmtInt, fmtMs, fmtTokens } from '../lib/format'
import { useCurrency } from '../lib/currency'

// ── time range presets ─────────────────────────────────────────
const RANGES: { id: string; label: string; granularity: 'hour' | 'day'; ms: number; all?: boolean }[] = [
  { id: '1h', label: '1h', granularity: 'hour', ms: 3600 * 1000 },
  { id: '24h', label: '24h', granularity: 'hour', ms: 24 * 3600 * 1000 },
  { id: '7d', label: '7d', granularity: 'day', ms: 7 * 86400 * 1000 },
  { id: '30d', label: '30d', granularity: 'day', ms: 30 * 86400 * 1000 },
  { id: '45d', label: '45d', granularity: 'day', ms: 45 * 86400 * 1000 },
  { id: '90d', label: '90d', granularity: 'day', ms: 90 * 86400 * 1000 },
  { id: 'all', label: 'All', granularity: 'day', ms: 0, all: true },
]

// distinct colors for model breakdown series
const SERIES_COLORS = ['#6366F1', '#2DD4BF', '#FBBF24', '#F87171', '#A78BFA', '#60A5FA', '#FB923C', '#34D399', '#EC4899']



export function CostAnalytics() {
  const { connected } = useFeed()
  const { format: fmtCost, symbol: curSymbol, convert: convertCost } = useCurrency()
  const [range, setRange] = useState(RANGES[4])
  const [filters, setFilters] = useState<AnalyticsFilters>({})
  // Cascading facets: options for every dropdown, scoped by the current selection.
  const facets = useFacets(filters).data

  // compute time window from range preset
  const window = useMemo(() => {
    const now = new Date()
    const from = range.all ? new Date('2020-01-01T00:00:00Z') : new Date(now.getTime() - range.ms)
    return { from: from.toISOString(), to: now.toISOString(), granularity: range.granularity }
  }, [range.ms, range.granularity, range.all])

  const allFilters: AnalyticsFilters = { ...filters, ...window }
  const ts = useTimeseries(allFilters)
  const breakdown = useBreakdown('model', allFilters)

  const points: any[] = ts.data?.points || []
  const fmtBucket = (b: string) => {
    if (!b) return ''
    return range.granularity === 'hour' ? b.slice(11, 16) : b.slice(5, 10)
  }
  const series = points.map((p) => ({ ...p, label: fmtBucket(p.bucket) }))

  // ── KPI roll-ups ──
  const sum = (k: string) => points.reduce((a, p) => a + (p[k] || 0), 0)
  const totalReq = sum('requests')
  const totalSuccess = sum('success')
  const totalErrors = sum('errors')
  const totalCacheHits = sum('cache_hits')
  const successRate = totalReq ? (totalSuccess / totalReq) * 100 : 0
  const cacheRate = totalReq ? (totalCacheHits / totalReq) * 100 : 0
  const totalTokens = sum('input_tokens') + sum('output_tokens')
  const totalCost = sum('cost_usd')
  const peakP99 = points.length ? Math.max(...points.map((p) => p.p99_latency_ms || 0)) : 0

  const empty = !ts.isLoading && points.length === 0

  return (
    <div className="space-y-5" data-testid="analytics-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Analytics</h1>
          <p className="text-muted text-sm">Request volume · cost · latency percentiles · token usage · rankings</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex bg-elevated rounded-xl p-1" data-testid="range-picker">
            {RANGES.map((r) => (
              <button key={r.id} data-testid={`range-${r.id}`} onClick={() => setRange(r)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${range.id === r.id ? 'bg-accent text-white' : 'text-muted hover:text-gray-200'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <LiveDot on={connected} />
        </div>
      </div>

      {/* ── hierarchical cascading filter bar (shared component) ── */}
      <Card className="p-3">
        <HierarchicalFilters value={filters} onChange={setFilters} facets={facets}
          fields={['client', 'workspace', 'component', 'provider', 'model', 'user', 'status', 'use_case']}
          includeSyntheticToggle />
      </Card>

      {/* ── KPI strip (count-up) ──────────────────────────────────── */}
      {ts.isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={120} />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4" data-testid="kpi-strip">
          <StatTile label="Requests" value={totalReq} color="#6366F1" icon={<Activity size={15} />}
            spark={points.slice(-14).map((p) => p.requests)} />
          <StatTile label="Success rate" value={totalReq === 0 ? 0 : successRate}
            text={totalReq === 0 ? 'N/A' : undefined}
            suffix={totalReq === 0 ? '' : '%'}
            decimals={totalReq === 0 ? 0 : 1}
            color={totalReq === 0 ? '#6B7280' : successRate >= 99 ? '#34D399' : successRate >= 95 ? '#FBBF24' : '#F87171'}
            icon={<Zap size={15} />} />
          <StatTile label="Tokens" value={totalTokens >= 1e6 ? totalTokens / 1e6 : totalTokens >= 1000 ? totalTokens / 1000 : totalTokens}
            suffix={totalTokens >= 1e6 ? 'M' : totalTokens >= 1000 ? 'k' : ''}
            decimals={totalTokens >= 1e6 ? 2 : totalTokens >= 1000 ? 1 : 0} color="#60A5FA"
            icon={<Cpu size={15} />} spark={points.slice(-14).map((p) => (p.input_tokens || 0) + (p.output_tokens || 0))} />
          <StatTile label="Cost" value={convertCost(totalCost)}
            prefix={curSymbol} decimals={convertCost(totalCost) >= 1 ? 2 : convertCost(totalCost) >= 0.01 ? 3 : 5} color="#FBBF24"
            icon={<DollarSign size={15} />} spark={points.slice(-14).map((p) => convertCost(p.cost_usd || 0))} />
          <StatTile label="p99 latency (max bucket)" value={peakP99} suffix="ms" decimals={0} color="#2DD4BF"
            icon={<Timer size={15} />} />
          <StatTile label="Cache hit rate" value={cacheRate} suffix="%" decimals={1} color="#A78BFA"
            icon={<IcLineChart size={15} />} />
        </div>
      )}

      {empty ? (
        <Card className="py-12">
          <EmptyState icon={<AlertCircle size={32} />} title="No data in this window"
            hint="Widen the time range, clear filters, or run scripts/live_traffic.py to generate live traffic." />
        </Card>
      ) : (
        <>
          {/* row 1 - request volume + latency percentiles */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Card>
              <SectionTitle right={
                totalErrors > 0
                  ? <LegendDot color="#34D399" label="succeeded" then={<LegendDot color="#F87171" label="failed" />} />
                  : <LegendDot color="#34D399" label="succeeded" />
              }>
                Request volume
              </SectionTitle>
              {ts.isLoading ? <Skeleton h={260} /> : (
                <AreaTime data={series}
                  keys={totalErrors > 0 ? ['success', 'errors'] : ['success']}
                  colors={totalErrors > 0 ? ['#34D399', '#F87171'] : ['#34D399']}
                  height={260} yFmt={fmtCount} />
              )}
            </Card>
            <Card>
              <SectionTitle right={<span className="text-[11px] text-muted">latency percentiles · ms</span>}>
                Latency p50 · p90 · p95 · p99
              </SectionTitle>
              {ts.isLoading ? <Skeleton h={260} /> : (
                <MultiLine data={series}
                  keys={['p50_latency_ms', 'p90_latency_ms', 'p95_latency_ms', 'p99_latency_ms']}
                  colors={['#A78BFA', '#6366F1', '#2DD4BF', '#F87171']} height={260}
                  yFmt={fmtMsAxis} yWidth={56} />
              )}
            </Card>
          </div>

          {/* row 2 - cost by model + token usage */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Card>
              <SectionTitle right={<span className="text-[11px] text-muted">stacked daily, top models</span>}>
                Cost over time by model
              </SectionTitle>
              {breakdown.isLoading ? <Skeleton h={260} /> : (
                <CostByModelChart data={breakdown.data} fmtBucket={fmtBucket} />
              )}
            </Card>
            <Card>
              <SectionTitle right={<LegendDot color="#60A5FA" label="input"
                then={<LegendDot color="#2DD4BF" label="output" then={<LegendDot color="#A78BFA" label="cached" />} />} />}>
                Token usage
              </SectionTitle>
              {ts.isLoading ? <Skeleton h={260} /> : (
                <AreaTime data={series}
                  keys={['input_tokens', 'output_tokens', 'cached_tokens']}
                  colors={['#60A5FA', '#2DD4BF', '#A78BFA']} height={260}
                  yFmt={fmtCount} yWidth={56} />
              )}
            </Card>
          </div>

          {/* row 3 - rankings */}
          <RankingsTable filters={allFilters} />
        </>
      )}
    </div>
  )
}

// ───────────────────────── helpers ─────────────────────────

function LegendDot({ color, label, then }: { color: string; label: string; then?: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-3 text-[11px] text-muted">
      <span className="inline-flex items-center gap-1">
        <span className="w-2 h-2 rounded-full" style={{ background: color }} />{label}
      </span>
      {then}
    </span>
  )
}

function CostByModelChart({ data, fmtBucket }: any) {
  const { format: fmtCost } = useCurrency()
  if (!data?.points?.length) return <EmptyState title="No cost data" />
  const series: string[] = data.series || []
  const points = data.points.map((p: any) => ({ ...p, label: fmtBucket(p.bucket) }))
  const colors = series.map((_, i) => SERIES_COLORS[i % SERIES_COLORS.length])
  return <AreaTime data={points} keys={series} colors={colors} height={260}
                   yFmt={(v: number) => fmtCost(v)} yWidth={60} />
}

const RANK_DIMS = [
  { id: 'client',   label: 'Client' },
  { id: 'workspace', label: 'Workspace' },
  { id: 'component', label: 'Component' },
  { id: 'user', label: 'User' },
  { id: 'provider', label: 'Provider' },
  { id: 'model', label: 'Model' },
  { id: 'use_case', label: 'Use case' },
]

function RankingsTable({ filters }: { filters: AnalyticsFilters }) {
  const { format: fmtCost } = useCurrency()
  const [dim, setDim] = useState('provider')
  const cost = useCost(dim, filters)
  const rows = (cost.data?.rows || []).filter((r: any) => r.key != null)
  return (
    <Card>
      <SectionTitle right={
        <div className="flex bg-elevated rounded-xl p-0.5" data-testid="rank-tabs">
          {RANK_DIMS.map((d) => (
            <button key={d.id} data-testid={`rank-${d.id}`} onClick={() => setDim(d.id)}
              className={`px-2.5 py-1 rounded-lg text-xs ${dim === d.id ? 'bg-accent text-white' : 'text-muted'}`}>{d.label}</button>
          ))}
        </div>
      }>Rankings</SectionTitle>
      {cost.isLoading ? <Skeleton h={220} /> : rows.length === 0 ? (
        <EmptyState title="No rows" hint="Widen the time range or change the filter set." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full" data-testid={`rank-table-${dim}`}>
            <thead><tr>
              <th className="th">{dim}</th>
              <th className="th text-right">requests</th>
              <th className="th text-right">tokens</th>
              <th className="th text-right">avg latency</th>
              <th className="th text-right">cost</th>
              <th className="th text-right">$/req</th>
            </tr></thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.key} className="border-t border-border/60 hover:bg-elevated/50">
                  <td className="td">
                    {dim === 'provider' ? <ProviderBadge provider={String(r.key)} /> : String(r.key)}
                  </td>
                  <td className="td text-right tabular-nums">{fmtInt(r.requests)}</td>
                  <td className="td text-right tabular-nums">{fmtTokens(r.input_tokens + r.output_tokens)}</td>
                  <td className="td text-right tabular-nums text-muted">{fmtMs(r.avg_latency_ms || 0)}</td>
                  <td className="td text-right text-warn tabular-nums">{fmtCost(r.cost_usd)}</td>
                  <td className="td text-right text-muted tabular-nums">{fmtCost(r.cost_usd / Math.max(1, r.requests))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
