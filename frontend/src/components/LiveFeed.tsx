import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import { GovEvent } from '../lib/sse'
import { kindColor, provColor } from '../lib/theme'
import { fmtMs, timeAgo, clsx } from '../lib/format'
import { Dot, Pill, ProviderBadge } from './ui'
import { useCurrency } from '../lib/currency'

const LABEL: Record<string, string> = {
  RequestSuccess: 'completion', RequestStart: 'start', GuardrailDecision: 'guardrail',
  Fallback: 'fallback', RateLimited: 'rate-limit', RequestError: 'error',
}

function kindOf(e: GovEvent): string {
  if (e.event_kind === 'GuardrailDecision') return e.action || 'guardrail'
  return LABEL[e.event_kind] || e.event_kind
}

export function FeedRow({ e, onClick }: { e: GovEvent; onClick: () => void }) {
  const { format: fmtMoney } = useCurrency()
  const color = kindColor(e.event_kind === 'GuardrailDecision' ? 'guardrail_block' : e.event_kind)
  return (
    <motion.button
      layout initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }} onClick={onClick}
      className="w-full text-left grid grid-cols-[auto_1fr_auto] items-center gap-3 px-3 py-2 rounded-lg hover:bg-elevated border-b border-border/50">
      <Dot color={color} />
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-200 truncate">{e.workspace_id}</span>
          {e.provider && <ProviderBadge provider={e.provider} />}
          {e.model_alias && <span className="text-muted text-xs mono truncate">{e.model_alias}</span>}
        </div>
        <div className="text-[11px] text-muted truncate">
          {kindOf(e)}{e.use_case ? ` · ${e.use_case}` : ''}{e.user_id ? ` · ${e.user_id}` : ''}
          {e.event_kind === 'Fallback' ? ` · ${e.from_provider}→${e.to_provider}` : ''}
          {e.event_kind === 'GuardrailDecision' ? ` · ${e.detector}` : ''}
        </div>
      </div>
      <div className="text-right text-[11px] tabular-nums">
        {e.event_kind === 'RequestSuccess' ? (
          <>
            <div className="text-gray-300">{(e.input_tokens || 0)}/{(e.output_tokens || 0)} tok</div>
            <div className="text-muted">{fmtMoney(e.cost_usd || 0)} · {fmtMs(e.latency_ms || 0)}</div>
          </>
        ) : (
          <div className="mono" style={{ color }}>{kindOf(e)}</div>
        )}
      </div>
    </motion.button>
  )
}

// Skeleton placeholder that mirrors FeedRow's layout, so an idle feed previews
// the shape of incoming telemetry instead of showing a cold empty void.
function GhostRow({ dim }: { dim: number }) {
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-3 py-2 border-b border-border/40 animate-pulse"
         style={{ opacity: Math.max(0.25, 0.7 - dim * 0.11) }} aria-hidden>
      <span className="w-2 h-2 rounded-full bg-muted/40" />
      <div className="min-w-0 space-y-1.5">
        <div className="h-2.5 rounded bg-muted/25" style={{ width: `${55 - dim * 6}%` }} />
        <div className="h-2 rounded bg-muted/15" style={{ width: `${38 - dim * 4}%` }} />
      </div>
      <div className="space-y-1.5 text-right">
        <div className="h-2.5 rounded bg-muted/25 ml-auto" style={{ width: 54 }} />
        <div className="h-2 rounded bg-muted/15 ml-auto" style={{ width: 38 }} />
      </div>
    </div>
  )
}

export function LiveFeed({ events, height = '70vh', onSelect }:
  { events: GovEvent[]; height?: number | string; onSelect?: (e: GovEvent) => void }) {
  const [sel, setSel] = useState<GovEvent | null>(null)
  const handleClick = (e: GovEvent) => { setSel(e); onSelect?.(e) }
  return (
    <>
      <div className="overflow-y-auto pr-1" style={{ height }}>
        <AnimatePresence initial={false}>
          {events.map((e) => <FeedRow key={e._id} e={e} onClick={() => handleClick(e)} />)}
        </AnimatePresence>
        {events.length === 0 && (
          <div className="py-2" data-testid="livefeed-empty">
            <div className="text-muted text-[11px] text-center pb-2 flex items-center justify-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              Waiting for live traffic - events appear here in real time as requests hit the gateway.
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
              <GhostRow key={i} dim={i} />
            ))}
          </div>
        )}
      </div>
      <RequestDrawer e={sel} onClose={() => setSel(null)} />
    </>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between py-1.5 border-b border-border/50 text-sm">
    <span className="text-muted">{k}</span><span className="text-gray-200 mono">{v}</span></div>
}

export function RequestDrawer({ e, onClose }: { e: GovEvent | null; onClose: () => void }) {
  const { format: fmtMoney } = useCurrency()
  const stages = ['auth', 'routing', 'guardrails', 'engine', 'governance']
  return (
    <AnimatePresence>
      {e && (
        <>
          <motion.div className="fixed inset-0 bg-black/50 z-40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside className="fixed right-0 top-0 h-full w-[440px] bg-surface border-l border-border z-50 p-6 overflow-y-auto"
            initial={{ x: 440 }} animate={{ x: 0 }} exit={{ x: 440 }} transition={{ type: 'tween', duration: 0.25 }}>
            <div className="flex items-center justify-between mb-4">
              <div className="font-semibold text-white">Request detail</div>
              <button onClick={onClose} className="text-muted hover:text-white"><X size={18} /></button>
            </div>
            <div className="flex items-center gap-2 mb-4">
              <Pill color={kindColor(e.event_kind === 'GuardrailDecision' ? 'guardrail_block' : e.event_kind)}>{kindOf(e)}</Pill>
              {e.provider && <ProviderBadge provider={e.provider} />}
            </div>
            <Row k="request_id" v={e.request_id || '-'} />
            <Row k="workspace" v={e.workspace_id || '-'} />
            <Row k="use_case" v={e.use_case || '-'} />
            <Row k="user" v={e.user_id || '-'} />
            <Row k="model" v={e.provider_model_id || e.model_alias || '-'} />
            {e.event_kind === 'RequestSuccess' && <>
              <Row k="tokens (in/out)" v={`${e.input_tokens}/${e.output_tokens}`} />
              <Row k="cost" v={fmtMoney(e.cost_usd || 0)} />
              <Row k="latency" v={fmtMs(e.latency_ms || 0)} />
              <Row k="attempt" v={e.attempt || 1} />
            </>}
            {e.event_kind === 'GuardrailDecision' && <>
              <Row k="action" v={e.action || ''} /><Row k="detector" v={e.detector || ''} />
              <Row k="rule" v={e.rule || ''} /><Row k="excerpt" v={e.excerpt || ''} />
            </>}
            {e.event_kind === 'Fallback' && <Row k="transition" v={`${e.from_provider} → ${e.to_provider} (${e.reason})`} />}
            <div className="mt-5 text-[11px] uppercase tracking-wider text-muted mb-2">Pipeline (OTel span tree)</div>
            <div className="flex flex-col gap-1">
              {stages.map((s, i) => (
                <div key={s} className={clsx('flex items-center gap-2 text-xs px-2 py-1.5 rounded-lg',
                  e.event_kind === 'GuardrailDecision' && i > 2 ? 'opacity-30' : '')}
                  style={{ background: '#1A1D27', borderLeft: `2px solid ${provColor(e.provider)}` }}>
                  <span className="text-muted w-20">{s}</span>
                  <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
                    <div className="h-full" style={{ width: `${[18, 6, 14, 56, 6][i]}%`, background: '#6366F1' }} />
                  </div>
                </div>
              ))}
            </div>
            <div className="text-[11px] text-muted mt-3">{e.ts_ms ? timeAgo(e.ts_ms) : ''}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

export { kindOf }
