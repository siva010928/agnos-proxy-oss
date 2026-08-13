import React, { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { HelpCircle, Search } from 'lucide-react'
import { Sparkline } from './Charts'
import { clsx } from '../lib/format'
import { provColor, pillText, useTheme } from '../lib/theme'

// Shared search/filter input - one consistent shape (height, icon, rounding,
// contrast) for every operational view, so filters stop looking different on
// each page. Pair with native <select className="input h-9 ..."> for facets.
export function SearchInput({ value, onChange, placeholder = 'Search…', className, testId }:
  { value: string; onChange: (v: string) => void; placeholder?: string; className?: string; testId?: string }) {
  return (
    <div className={clsx('relative', className)}>
      <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
      <input
        className="input pl-8 h-9 text-sm w-full"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      />
    </div>
  )
}

// Hover help - a small info icon that reveals a verbose explanation on hover.
// Used to teach operators what each control does without cluttering the UI.
export function Help({ text, size = 12, side = 'top', className }:
  { text: React.ReactNode; size?: number; side?: 'top' | 'bottom'; className?: string }) {
  return (
    <span className={clsx('relative inline-flex group/help align-middle', className)}>
      <HelpCircle size={size} className="text-muted hover:text-accent cursor-help transition-colors" />
      <span
        role="tooltip"
        className={clsx(
          'pointer-events-none absolute left-1/2 -translate-x-1/2 z-50 hidden group-hover/help:block',
          'w-64 rounded-lg p-2.5 text-[11px] leading-relaxed shadow-xl normal-case tracking-normal font-normal text-left',
          side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
        )}
        style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
      >
        {text}
      </span>
    </span>
  )
}

export function Card({ className, children, ...rest }: { className?: string; children: React.ReactNode; [key: string]: any }) {
  return <div className={clsx('card p-5', className)} {...rest}>{children}</div>
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="text-[11px] uppercase tracking-wider text-muted font-medium">{children}</div>
      {right}
    </div>
  )
}

export function Pill({ children, color, ...rest }: { children: React.ReactNode; color?: string; [key: string]: any }) {
  const { theme } = useTheme()
  const c = color || '#6B7280'
  return <span {...rest} className="pill" style={{ background: c + '1f', color: pillText(c, theme), borderColor: c + '55' }}>{children}</span>
}

export function ProviderBadge({ provider }: { provider?: string }) {
  const { theme } = useTheme()
  if (!provider) return null
  const c = provColor(provider)
  return (
    <span className="pill" style={{ background: c + '1f', color: pillText(c, theme), borderColor: c + '55' }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: c }} /> {provider}
    </span>
  )
}

export function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
}

export function CountUp({ value, decimals = 0, prefix = '', suffix = '', dur = 600 }:
  { value: number; decimals?: number; prefix?: string; suffix?: string; dur?: number }) {
  const [v, setV] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const a = from.current, b = value || 0
    let raf = 0
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / dur)
      const e = 1 - Math.pow(1 - p, 3)
      setV(a + (b - a) * e)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = b
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value, dur])
  return <>{prefix}{v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}{suffix}</>
}

export function StatTile({ label, value, decimals = 0, prefix = '', suffix = '', spark, delta, color = '#E5E7EB', icon, text }:
  { label: string; value: number; decimals?: number; prefix?: string; suffix?: string;
    spark?: number[]; delta?: number; color?: string; icon?: React.ReactNode; text?: string }) {
  return (
    <motion.div className="card p-4 flex flex-col gap-2" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
      <div className="flex items-center justify-between text-muted text-xs">
        <span className="uppercase tracking-wider">{label}</span>
        {icon}
      </div>
      <div className="text-2xl font-semibold tabular-nums" style={{ color, fontVariantNumeric: 'tabular-nums' }}>
        {text !== undefined
          ? text
          : <CountUp value={value} decimals={decimals} prefix={prefix} suffix={suffix} />}
      </div>
      <div className="flex items-center justify-between h-8">
        {spark && spark.length > 1 ? <Sparkline data={spark} color={color} /> : <span />}
        {delta != null && (
          <span className="text-[11px]" style={{ color: delta >= 0 ? '#34D399' : '#F87171' }}>
            {delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(0)}%
          </span>
        )}
      </div>
    </motion.div>
  )
}

export function Skeleton({ h = 16, w = '100%', className }: { h?: number; w?: number | string; className?: string }) {
  return <div className={clsx('animate-pulse rounded-md bg-elevated', className)} style={{ height: h, width: w }} />
}

export function EmptyState({ icon, title, hint, cta }:
  { icon?: React.ReactNode; title: React.ReactNode; hint?: React.ReactNode; cta?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6 gap-2">
      <div className="text-muted opacity-60">{icon}</div>
      <div className="text-gray-200 font-medium">{title}</div>
      {hint && <div className="text-muted text-sm max-w-md">{hint}</div>}
      {cta}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-10 gap-2">
      <div className="text-danger font-medium">Something went wrong</div>
      <div className="text-muted text-sm">{message}</div>
      {onRetry && <button className="btn-ghost mt-1" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <button type="button" onClick={() => onChange(!checked)}
        className="w-9 h-5 rounded-full transition-colors relative"
        style={{ background: checked ? '#6366F1' : '#242836' }}>
        <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
          style={{ left: checked ? 18 : 2 }} />
      </button>
      {label && <span className="text-sm text-gray-300">{label}</span>}
    </label>
  )
}

export function LiveDot({ on }: { on: boolean }) {
  const { theme } = useTheme()
  const txt = on ? pillText('#34D399', theme) : '#6B7280'
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: txt }}>
      <span className={clsx('w-2 h-2 rounded-full', on && 'animate-pulseLive')} style={{ background: on ? '#34D399' : '#6B7280' }} />
      {on ? 'LIVE' : 'OFFLINE'}
    </span>
  )
}
