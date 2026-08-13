// Confirmation modal - replaces native confirm() and the ad-hoc inline confirms
// across admin screens. Optional secondary identifier (e.g. "rotate key
// gw-…ABC, last used 5m ago") so users can verify they're acting on the right
// row. For high-stakes destructive ops (delete workspace), supports a
// typed-confirmation challenge.
import React, { useState } from 'react'
import { Modal } from './Modal'
import { AlertTriangle } from 'lucide-react'

export function ConfirmModal({
  open,
  onCancel,
  onConfirm,
  title,
  message,
  identifier,
  confirmLabel = 'Confirm',
  danger = false,
  typedChallenge,
  testId = 'confirm',
}: {
  open: boolean
  onCancel: () => void
  onConfirm: () => Promise<void> | void
  title: string
  message: React.ReactNode
  identifier?: React.ReactNode
  confirmLabel?: string
  danger?: boolean
  typedChallenge?: string   // if set, user must type this exactly to enable Confirm
  testId?: string
}) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const challengeMet = !typedChallenge || typed === typedChallenge

  async function go() {
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      setBusy(false)
      setTyped('')
    }
  }

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={
        <span className="flex items-center gap-2">
          {danger && <AlertTriangle size={16} className="text-danger" />}
          {title}
        </span>
      }
      size="sm"
      testId={testId}
      footer={
        <>
          <button
            type="button"
            className="btn-ghost text-sm"
            onClick={onCancel}
            disabled={busy}
            data-testid={`${testId}-cancel`}
          >
            Cancel
          </button>
          <button
            type="button"
            className={danger ? 'btn-danger text-sm' : 'btn-primary text-sm'}
            onClick={go}
            disabled={busy || !challengeMet}
            data-testid={`${testId}-go`}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-gray-200">
        <div>{message}</div>
        {identifier && (
          <div className="bg-app border border-border rounded-lg p-3 text-[12px] mono text-gray-300">
            {identifier}
          </div>
        )}
        {typedChallenge && (
          <div className="space-y-1">
            <div className="text-[11px] text-muted">
              Type <span className="mono text-warn">{typedChallenge}</span> to confirm:
            </div>
            <input
              className="input mono text-xs"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              data-testid={`${testId}-challenge`}
              autoFocus
            />
          </div>
        )}
      </div>
    </Modal>
  )
}
