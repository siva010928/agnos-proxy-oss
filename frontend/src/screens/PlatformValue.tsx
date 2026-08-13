// Platform Value - executive dashboard (WAVE 25 TRACK 4 UI).
// Shows the ownership ROI in one screen: governed spend, margin, cache savings,
// security posture, engine independence, governance coverage - all in the
// admin-selected currency with live FX from frankfurter.dev.

import { useEffect, useState } from 'react'
import { BarChart3, DollarSign, Lock, Cpu, Shield, PieChart } from 'lucide-react'
import { api } from '../lib/api'
import { Card, SectionTitle, StatTile, Skeleton, Pill } from '../components/ui'
import { useCurrency } from '../lib/currency'



interface ValueData {
  currency: string
  rate_to_usd: number
  governed_spend: { usd: number; local: number; requests: number }
  profitability: { billed_local: number; margin_local: number; margin_pct: number }
  cache_savings: { hits: number; saved_local: number }
  security: { credentials_centralized: string; secrets_blocked: number; pii_redacted: number; audit_events: number }
  engine_independence: { status: string; detail: string; direct_requests: number; total_requests: number }
  governance_coverage: { fully_attributed_pct: number; clients: number; workspaces: number; components: number }
}

export function PlatformValue() {
  const [data, setData] = useState<ValueData | null>(null)
  const [loading, setLoading] = useState(true)
  // Use the single global currency picker (in the Shell header) - no duplicate.
  const { currency, symbol: sym } = useCurrency()

  useEffect(() => {
    setLoading(true)
    api(`/admin/platform-value?currency=${currency}`)
      .then((r: any) => setData(r))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [currency])

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Platform Value</h1>
          <p className="text-muted text-sm">
            Ownership ROI - why governing at the boundary pays off. All numbers are real, live, and in your selected currency.
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <span>FX: 1 USD = {data.rate_to_usd} {currency}</span>
            <span className="opacity-50">·</span>
            <span>via frankfurter.dev (ECB)</span>
          </div>
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-3 gap-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={120} />)}</div>
      ) : !data ? (
        <Card><div className="py-12 text-center text-muted">Failed to load. Check admin auth.</div></Card>
      ) : (
        <>
          {/* KPI strip */}
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
            <StatTile label="Governed spend" value={data.governed_spend.local}
                      prefix={sym} decimals={2} color="#6366F1" icon={<DollarSign size={14} />} />
            <StatTile label="Margin" value={data.profitability.margin_local}
                      prefix={sym} decimals={2} color="#34D399" icon={<BarChart3 size={14} />} />
            <StatTile label="Margin %" value={data.profitability.margin_pct}
                      suffix="%" decimals={1} color="#34D399" />
            <StatTile label="Cache savings" value={data.cache_savings.saved_local}
                      prefix={sym} decimals={2} color="#FBBF24" icon={<Cpu size={14} />} />
            <StatTile label="Owned-engine requests" value={data.engine_independence.direct_requests}
                      decimals={0} color="#A78BFA" icon={<PieChart size={14} />} />
            <StatTile label="Governance coverage" value={data.governance_coverage.fully_attributed_pct}
                      suffix="%" decimals={1} color="#60A5FA" icon={<Shield size={14} />} />
          </div>

          {/* Detail cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            <Card className="p-4">
              <SectionTitle>Profitability</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Raw cost" value={`${sym}${data.governed_spend.local.toFixed(2)}`} />
                <Row label="Billed (with markup)" value={`${sym}${data.profitability.billed_local.toFixed(2)}`} />
                <Row label="Margin" value={`${sym}${data.profitability.margin_local.toFixed(2)} (${data.profitability.margin_pct}%)`} accent />
                <Row label="Requests governed" value={String(data.governed_spend.requests)} />
              </div>
            </Card>

            <Card className="p-4">
              <SectionTitle>Security posture</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Credentials" value={data.security.credentials_centralized} />
                <Row label="Secrets blocked" value={String(data.security.secrets_blocked)} />
                <Row label="PII redacted" value={String(data.security.pii_redacted)} />
                <Row label="Audit events" value={String(data.security.audit_events)} />
              </div>
            </Card>

            <Card className="p-4">
              <SectionTitle>Engine independence</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Status" value={data.engine_independence.status} accent />
                <Row label="Detail" value={data.engine_independence.detail} />
                <Row label="Direct requests" value={String(data.engine_independence.direct_requests)} />
                <div className="text-[10px] text-muted mt-2">
                  Flip any workspace to our owned engine with one admin PATCH - zero component change, identical governance.
                </div>
              </div>
            </Card>

            <Card className="p-4">
              <SectionTitle>Cache savings</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Cache hits" value={String(data.cache_savings.hits)} />
                <Row label="Estimated saved" value={`${sym}${data.cache_savings.saved_local.toFixed(2)}`} accent />
                <div className="text-[10px] text-muted mt-2">
                  Each cache hit returns the response at $0 cost - tokens that would have been billed are saved.
                </div>
              </div>
            </Card>

            <Card className="p-4">
              <SectionTitle>Governance coverage</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Fully attributed" value={`${data.governance_coverage.fully_attributed_pct}%`} accent />
                <Row label="Clients" value={String(data.governance_coverage.clients)} />
                <Row label="Workspaces" value={String(data.governance_coverage.workspaces)} />
                <Row label="Components (auto-registered)" value={String(data.governance_coverage.components)} />
              </div>
            </Card>

            <Card className="p-4">
              <SectionTitle>Exchange rate</SectionTitle>
              <div className="space-y-2 text-sm">
                <Row label="Source" value="frankfurter.dev (ECB)" />
                <Row label="Rate" value={`1 USD = ${data.rate_to_usd} ${currency}`} />
                <Row label="Conversion" value="Time-accurate (per row date)" />
                <div className="text-[10px] text-muted mt-2">
                  Historical rows use their date's FX rate so past reports don't shift when rates move.
                </div>
              </div>
            </Card>
          </div>

          {/* Projection at scale */}
          {(data as any).projection && (
            <Card className="border-accent/30 bg-accent/5">
              <SectionTitle>Projected at scale</SectionTitle>
              <div className="text-[11px] text-muted mb-3">
                {(data as any).projection.label}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="text-center">
                  <div className="text-2xl font-bold text-white tabular-nums">
                    {sym}{((data as any).projection.governed_spend_local / 100000).toFixed(1)}L
                  </div>
                  <div className="text-xs text-muted">Governed spend / month</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-accent tabular-nums">
                    {sym}{((data as any).projection.margin_local / 100000).toFixed(1)}L
                  </div>
                  <div className="text-xs text-muted">Margin / month ({data.profitability.margin_pct}%)</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-warn tabular-nums">
                    {sym}{((data as any).projection.cache_savings_local / 1000).toFixed(1)}k
                  </div>
                  <div className="text-xs text-muted">Cache savings / month</div>
                </div>
              </div>
              <div className="mt-3 text-[10px] text-muted">
                Unit economics: {sym}{((data as any).unit_economics?.cost_per_req_usd * data.rate_to_usd).toFixed(4)}/req cost,
                {' '}{sym}{((data as any).unit_economics?.margin_per_req_usd * data.rate_to_usd).toFixed(4)}/req margin,
                {' '}{(data as any).unit_economics?.tokens_per_req?.toFixed(0)} tokens/req,
                {' '}{(data as any).unit_economics?.cache_hit_rate_pct}% cache hit rate.
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  )
}

function Row({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{label}</span>
      <span className={`tabular-nums ${accent ? 'text-accent font-semibold' : 'text-gray-200'}`}>{value}</span>
    </div>
  )
}
