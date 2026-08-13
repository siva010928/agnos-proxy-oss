// Shared form Field - label + helper hint + inline error + a slot for the
// input/builder. Module-scope (must NEVER be defined inside a parent's render)
// so that React doesn't remount the children every keystroke (we already paid
// for that lesson once with ProviderForm).
import React from 'react'

export function Field({
  label,
  hint,
  error,
  required,
  children,
  htmlFor,
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  required?: boolean
  children: React.ReactNode
  htmlFor?: string
}) {
  return (
    <label className="block space-y-1" htmlFor={htmlFor}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-100">
          {label}
          {required && <span className="text-danger ml-1" aria-label="required">*</span>}
        </span>
      </div>
      {children}
      {error ? (
        <div className="text-[11px] text-danger" role="alert">{error}</div>
      ) : hint ? (
        <div className="text-[11px] text-muted">{hint}</div>
      ) : null}
    </label>
  )
}
