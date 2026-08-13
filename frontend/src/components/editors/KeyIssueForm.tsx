// KeyIssueForm - used by both the standalone Keys screen's "Issue key" modal
// and the wizard's final step. Adds a label, a real <input type="date">, and
// a "no expiry" checkbox so user intent is explicit (and never silently
// nulled like the old free-text expires_at field).

import React from 'react'
import { Field } from './Field'

export interface KeyIssueValue {
  label: string                 // human label / description (NEW)
  roles: string[]
  expires_at: string | null     // ISO date or null
}

const ROLES: { value: string; label: string; hint: string }[] = [
  { value: 'member', label: 'member', hint: 'Standard member: can call /v1/* with this key.' },
  { value: 'admin',  label: 'admin',  hint: 'WARNING: grants admin-CRUD access to ALL workspaces. Avoid unless this key represents a platform operator.' },
]

export function emptyKeyIssue(): KeyIssueValue {
  return { label: '', roles: ['member'], expires_at: null }
}

export function KeyIssueForm({
  value,
  onChange,
  testIdPrefix = 'key-issue',
}: {
  value: KeyIssueValue
  onChange: (next: KeyIssueValue) => void
  testIdPrefix?: string
}) {
  const today = new Date().toISOString().slice(0, 10)
  const [noExpiry, setNoExpiry] = React.useState<boolean>(value.expires_at === null)

  const toggleRole = (r: string) => {
    onChange({
      ...value,
      roles: value.roles.includes(r) ? value.roles.filter((x) => x !== r) : [...value.roles, r],
    })
  }

  return (
    <div className="space-y-4">
      <Field
        label="Key label"
        hint="Short human description so this key can be identified later (you cannot recover the secret value). E.g. 'CI prod', 'dev-laptop-alex'."
        required
      >
        <input
          className="input text-xs"
          value={value.label}
          onChange={(e) => onChange({ ...value, label: e.target.value })}
          placeholder="CI prod"
          data-testid={`${testIdPrefix}-label`}
        />
      </Field>

      <Field
        label="Roles"
        hint="Roles attached to this key. At least one is required."
        required
      >
        <div className="space-y-2">
          {ROLES.map((r) => (
            <label key={r.value} className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                className="accent-accent mt-1"
                checked={value.roles.includes(r.value)}
                onChange={() => toggleRole(r.value)}
                data-testid={`${testIdPrefix}-role-${r.value}`}
              />
              <span>
                <span className={`text-gray-100 ${r.value === 'admin' ? 'text-warn' : ''}`}>{r.label}</span>
                <div className="text-[11px] text-muted">{r.hint}</div>
              </span>
            </label>
          ))}
        </div>
      </Field>

      <Field
        label="Expiry"
        hint="The key stops working after this date. Past dates are rejected; unparseable values are rejected (no silent forever-keys)."
      >
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              className="accent-accent"
              checked={noExpiry}
              onChange={(e) => {
                setNoExpiry(e.target.checked)
                onChange({ ...value, expires_at: e.target.checked ? null : today })
              }}
              data-testid={`${testIdPrefix}-no-expiry`}
            />
            <span className="text-gray-100">No expiry</span>
            <span className="text-[11px] text-warn ml-1">- not recommended for production</span>
          </label>
          {!noExpiry && (
            <input
              type="date"
              className="input text-xs w-44"
              min={today}
              value={value.expires_at || ''}
              onChange={(e) => onChange({ ...value, expires_at: e.target.value || null })}
              data-testid={`${testIdPrefix}-date`}
              required
            />
          )}
        </div>
      </Field>
    </div>
  )
}

export function keyIssueValid(v: KeyIssueValue): boolean {
  if (!v.label.trim()) return false
  if (!v.roles.length) return false
  if (v.expires_at !== null && !/^\d{4}-\d{2}-\d{2}$/.test(v.expires_at)) return false
  if (v.expires_at) {
    const d = new Date(v.expires_at + 'T00:00:00')
    if (Number.isNaN(d.getTime())) return false
    if (d.getTime() <= Date.now()) return false
  }
  return true
}
