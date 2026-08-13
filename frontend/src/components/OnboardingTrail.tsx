// Guided-onboarding trail shown across the Workspaces -> Providers -> Routing
// flow. Renders the 3-step breadcrumb and a "Next" CTA that advances the admin
// to the next screen, carrying ?onboarding=1 so the trail persists.
import { Link } from 'react-router-dom'
import { ArrowRight, Check } from 'lucide-react'

const STEPS = ['Workspace', 'Providers', 'Routing'] as const

export function OnboardingTrail({
  step,
  workspace,
  next,
  nextEnabled = true,
  nextHint,
}: {
  step: 1 | 2 | 3                 // 1-based current step
  workspace?: string | null
  next?: { label: string; to: string }
  nextEnabled?: boolean
  nextHint?: string
}) {
  return (
    <div className="rounded-xl border p-3 flex items-center justify-between gap-3 flex-wrap"
         style={{ borderColor: 'var(--color-accent)', background: 'var(--color-accent-soft, rgba(99,102,241,0.07))' }}
         data-testid="onboarding-trail">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: 'var(--color-accent)' }}>
          Onboarding{workspace ? <span className="text-muted normal-case"> · {workspace}</span> : null}
        </span>
        <div className="flex items-center gap-1.5">
          {STEPS.map((s, i) => {
            const n = i + 1
            const done = n < step
            const current = n === step
            return (
              <span key={s} className="inline-flex items-center gap-1.5">
                <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border"
                  style={current
                    ? { background: 'var(--color-accent)', color: '#0b0d12', borderColor: 'var(--color-accent)' }
                    : done
                      ? { color: 'var(--color-ok)', borderColor: 'var(--color-ok)' }
                      : { color: 'var(--color-muted)', borderColor: 'var(--color-border)' }}>
                  {done ? <Check size={10} /> : <span>{n}.</span>} {s}
                </span>
                {i < STEPS.length - 1 && <span className="text-muted text-[11px]">→</span>}
              </span>
            )
          })}
        </div>
      </div>
      {next && (
        <div className="flex items-center gap-2">
          {nextHint && <span className="text-[11px] text-muted">{nextHint}</span>}
          {nextEnabled ? (
            <Link to={next.to} data-testid="onboarding-next"
              className="btn-primary text-sm inline-flex items-center gap-1.5">
              {next.label} <ArrowRight size={14} />
            </Link>
          ) : (
            <span data-testid="onboarding-next-disabled"
              className="btn-primary text-sm inline-flex items-center gap-1.5 opacity-50 cursor-not-allowed pointer-events-none">
              {next.label} <ArrowRight size={14} />
            </span>
          )}
        </div>
      )}
    </div>
  )
}
