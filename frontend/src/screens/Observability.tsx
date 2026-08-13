// Observability - the launcher page (WAVE 19 TRACK D4).
//
// We don't out-build Grafana or Jaeger. This page shows a few native KPIs
// (request rate, error rate, gateway overhead p50/p99, dropped governance
// events) and links to:
//   * Grafana   (metrics explorer + Agnos dashboard) at :3001
//   * Jaeger    (OTel trace span tree)               at :16686
//   * Kafka UI  (governance topic)                   at :8085
//   * Prometheus (raw scrape)                        at :9090
//
// Configurable via env (.env): JAEGER_UI_URL, GRAFANA_URL, KAFKA_UI_URL.
// We default to the docker-compose defaults so a fresh clone Just Works.

import { ExternalLink, ChartLine, Search, Network, Database, AlertTriangle } from 'lucide-react'
import { api, useMetricsText, parseMetrics } from '../lib/api'
import { Card, SectionTitle, StatTile, Skeleton } from '../components/ui'
import { fmtMs } from '../lib/format'
import { useEffect, useState } from 'react'

// In production (Docker + Caddy), observability tools are at sub-paths.
// In local dev, they're on separate ports. Detect which by checking if
// the current host is localhost (dev) or a real domain (production).
function getObsUrls() {
  if (typeof window === 'undefined') return { grafana: '/grafana', jaeger: '/jaeger', kafka: '/kafka-ui', prometheus: '/prometheus' }
  const host = window.location.hostname
  const isLocal = host === 'localhost' || host === '127.0.0.1'
  if (isLocal) {
    return {
      grafana: 'http://localhost:3001',
      jaeger: 'http://localhost:16686',
      kafka: 'http://localhost:8085',
      prometheus: 'http://localhost:9090',
    }
  }
  // Production: Caddy reverse-proxies sub-paths
  return {
    grafana: '/grafana',
    jaeger: '/jaeger',
    kafka: '/kafka-ui',
    prometheus: '/prometheus',
  }
}
const URLS = getObsUrls()

// Prometheus-style histogram quantile with linear interpolation INSIDE the
// bucket that contains the target rank. The previous version returned the
// bucket's upper boundary (`le`), so every percentile snapped to a bucket edge
// - e.g. all samples in the ≤0.25s bucket made both p50 and p99 read 250ms.
function quantile(samples: any[], name: string, filter: (l: Record<string, string>) => boolean, q: number) {
  const buckets = samples.filter((s) => s.name === name + '_bucket' && filter(s.labels))
    .map((s) => ({ le: s.labels.le === '+Inf' ? Infinity : Number(s.labels.le), cum: s.value }))
    .sort((a, b) => a.le - b.le)
  if (!buckets.length) return 0
  const total = buckets[buckets.length - 1].cum   // +Inf bucket holds the full count
  if (!total) return 0
  const rank = q * total
  let prevLe = 0
  let prevCum = 0
  for (const b of buckets) {
    if (b.cum >= rank) {
      // Can't interpolate into the open-ended +Inf bucket - return its lower edge.
      if (b.le === Infinity) return prevLe
      const inBucket = b.cum - prevCum
      if (inBucket <= 0) return b.le
      const frac = (rank - prevCum) / inBucket
      return prevLe + frac * (b.le - prevLe)
    }
    prevLe = b.le === Infinity ? prevLe : b.le
    prevCum = b.cum
  }
  return prevLe
}

export function Observability() {
  const m = useMetricsText()
  const samples = m.data ? parseMetrics(m.data) : []
  const [dbTotal, setDbTotal] = useState(0)

  // Pull the authoritative total from the DB (not Prometheus which resets on restart)
  useEffect(() => {
    api('/admin/request-logs?limit=1')
      .then((r: any) => setDbTotal(r.total || 0))
      .catch(() => {})
  }, [])

  const sumBy = (name: string, lf: (l: any) => boolean = () => true) =>
    samples.filter((s) => s.name === name && lf(s.labels)).reduce((a, s) => a + s.value, 0)

  const totalReq = sumBy('gateway_requests_total')
  const errReq = sumBy('gateway_requests_total', (l) => l.status === 'error')
  const dropped = sumBy('gateway_governance_events_dropped_total')
  const dlq = sumBy('gateway_kafka_dlq_total')
  const errRate = totalReq ? (errReq / totalReq) * 100 : 0
  const oh = (q: number) => quantile(samples, 'gateway_overhead_seconds', (l) => l.stage === 'total', q) * 1000
  // Exact mean from the histogram's _sum/_count (not bucket-limited) per stage.
  const ohAvg = (stage: string) => {
    const s = sumBy('gateway_overhead_seconds_sum', (l) => l.stage === stage)
    const c = sumBy('gateway_overhead_seconds_count', (l) => l.stage === stage)
    return c ? (s / c) * 1000 : 0
  }
  // Plumbing = auth + routing (the Bifrost-comparable proxy path). Policy =
  // guardrails/PII + budget + rate-limit (governance work Bifrost doesn't do).
  const proxyMs = ohAvg('proxy')                  // bare plumbing (identity + routing)
  const plumbingMs = proxyMs || (ohAvg('auth') + ohAvg('routing'))
  const policyMs = ohAvg('policy')

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Observability</h1>
        <p className="text-muted text-sm">
          Native KPIs from <span className="mono text-gray-200">/metrics</span>; deep
          analysis lives in Grafana (metrics) and Jaeger (traces).
        </p>
      </div>

      {/* Native KPI strip */}
      <Card>
        <SectionTitle>KPIs</SectionTitle>
        {!m.data ? <Skeleton h={80} /> : (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
            <StatTile label="Total requests (DB)" value={dbTotal} />
            <StatTile label="Since restart (Prometheus)" value={totalReq} />
            <StatTile label="Error rate (since restart)" value={errRate} decimals={2} suffix="%"
                      color={errRate >= 25 ? '#F87171' : errRate >= 5 ? '#FBBF24' : '#34D399'}
                      icon={errRate >= 25 ? <AlertTriangle size={13} className="text-danger" /> : undefined} />
            <StatTile label="Gateway overhead p50" value={oh(0.5)} decimals={1} suffix="ms" />
            <StatTile label="Gateway overhead p99" value={oh(0.99)} decimals={1} suffix="ms" />
            <StatTile label="Events dropped" value={dropped} />
          </div>
        )}
        <div className="text-[10.5px] text-muted mt-2">
          <span className="text-gray-300">"Since restart"</span> counters live in the gateway process and reset to zero whenever it restarts -
          which includes <strong className="text-gray-300">every deployment</strong> (a new build replaces the container). They are <em>not</em> affected by logins.
          <span className="text-gray-300"> "Total (DB)"</span> is the authoritative, persistent count from request_logs (Postgres survives deploys), so use it for true cumulative numbers.
          Overhead excludes the upstream provider call (it's end-to-end minus provider latency).
        </div>
      </Card>

      {/* Overhead breakdown - plumbing vs governance */}
      <Card>
        <SectionTitle>Gateway overhead breakdown (mean, excl. provider)</SectionTitle>
        {!m.data ? <Skeleton h={80} /> : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatTile label="Bare proxy (identity + routing)" value={plumbingMs} decimals={2} suffix="ms" color="#34D399" />
            <StatTile label="Policy (guardrails + budget)" value={policyMs} decimals={2} suffix="ms" color="#FBBF24" />
            <StatTile label="Post-call (emit + cost)" value={Math.max(0, ohAvg('total') - plumbingMs - policyMs)} decimals={2} suffix="ms" color="#60A5FA" />
            <StatTile label="Total overhead (mean)" value={ohAvg('total')} decimals={2} suffix="ms" color="#A78BFA" />
          </div>
        )}
        <div className="text-[10.5px] text-muted mt-2">
          <strong className="text-gray-300">Bare proxy</strong> is the pure plumbing - identity resolution + routing decision
          before any governance or the provider call - the honest analogue of a bare gateway's added latency.
          <strong className="text-gray-300"> Policy</strong> is the value-add a bare proxy doesn't do - guardrail/PII detection,
          budget + rate-limit enforcement - so it dominates when a workspace runs heavy detectors (e.g. Presidio PII).
          For a controlled headline number, run <span className="mono text-gray-300">scripts/bench_overhead.py</span> (echo engine, governance off).
        </div>
        <div className="text-[10.5px] text-muted mt-1">
          Reference: Bifrost reports ~11&micro;s bare overhead (Go, no governance in the hot path). Ours is Python and
          includes far more per request; the bare-proxy number is what's directly comparable.
        </div>
      </Card>

      {/* Launchers */}
      <Card>
        <SectionTitle>Open in dedicated tooling</SectionTitle>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <Launcher
            href={URLS.grafana}
            icon={<ChartLine size={18} />}
            label="Grafana"
            blurb="Metrics dashboard with pre-provisioned panels (per-client, per-workspace, per-component, per-model rollups). Provisioning at infra/grafana/."
            color="#FB923C"
            data-testid="launcher-grafana"
          />
          <Launcher
            href={URLS.jaeger}
            icon={<Search size={18} />}
            label="Jaeger"
            blurb="OTel trace UI. The full per-request span tree (auth → routing → guardrails → engine → governance) with client/workspace/user/component/cost attributes."
            color="#A78BFA"
            data-testid="launcher-jaeger"
          />
          <Launcher
            href={URLS.kafka}
            icon={<Database size={18} />}
            label="Kafka UI"
            blurb="Browse the agnos-proxy.governance.v1 topic. Inspect every envelope auto-emitted from the gateway pipeline + external ingest at POST /governance/events."
            color="#34D399"
            data-testid="launcher-kafka"
          />
          <Launcher
            href={URLS.prometheus}
            icon={<Network size={18} />}
            label="Prometheus"
            blurb="Raw /metrics scrape + recording rules + SLO alerts (infra/rules.yml). Use /admin/request-logs for searchable per-request log."
            color="#22D3EE"
            data-testid="launcher-prometheus"
          />
        </div>
      </Card>
    </div>
  )
}

function Launcher({ href, icon, label, blurb, color, ...rest }:
  { href: string; icon: React.ReactNode; label: string; blurb: string; color: string;
    [key: string]: any }) {
  return (
    <a href={href} target="_blank" rel="noreferrer"
       className="card p-4 flex flex-col gap-2 hover:border-accent/50 transition-colors"
       {...rest}>
      <div className="flex items-center justify-between">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center"
             style={{ background: color + '22', color }}>{icon}</div>
        <ExternalLink size={14} className="text-muted" />
      </div>
      <div className="text-base font-semibold text-white">{label}</div>
      <div className="text-[12px] text-muted leading-relaxed">{blurb}</div>
    </a>
  )
}
