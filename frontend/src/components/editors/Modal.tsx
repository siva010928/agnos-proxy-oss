// Accessible modal: role=dialog, aria-modal, focus trap, Esc-to-close,
// returns focus to the trigger on close, scroll-locked body. Replaces the 5
// inline modal scaffolds across admin screens.
import { X } from 'lucide-react'
import React, { useCallback, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  size = 'md',
  footer,
  children,
  testId,
}: {
  open: boolean
  onClose: () => void
  title: React.ReactNode
  subtitle?: React.ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
  footer?: React.ReactNode
  children: React.ReactNode
  testId?: string
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<Element | null>(null)
  // Stable ref to onClose so the body-scroll/Esc effect below doesn't
  // re-subscribe on every parent render (parent typically re-creates the
  // onClose closure on each keystroke; without this ref the cleanup would
  // fire after every char and return focus to the trigger - the same class
  // of bug that bit ProviderForm before).
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  // Capture the focused element BEFORE the modal opens so we can return focus
  useEffect(() => {
    if (open) {
      triggerRef.current = document.activeElement as Element | null
    }
  }, [open])

  // Focus the first focusable child once the modal is mounted
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      const node = dialogRef.current
      if (!node) return
      const first = node.querySelector<HTMLElement>(
        'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
      first?.focus()
    }, 50)
    return () => clearTimeout(t)
  }, [open])

  // Body scroll lock + Esc handler
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
      }
      // Simple focus trap
      if (e.key === 'Tab' && dialogRef.current) {
        const focusables = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(
            'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )
        ).filter((n) => !n.hasAttribute('aria-hidden'))
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
      // Return focus to the trigger
      ;(triggerRef.current as HTMLElement | null)?.focus?.()
    }
  }, [open])

  const onBackdrop = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onCloseRef.current()
    },
    []
  )

  if (!open) return null

  const sizeClass =
    size === 'sm' ? 'max-w-md'
    : size === 'lg' ? 'max-w-3xl'
    : size === 'xl' ? 'max-w-5xl'
    : 'max-w-xl'

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onMouseDown={onBackdrop}
      data-testid={testId ? `${testId}-backdrop` : undefined}
    >
      <motion.div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        initial={{ y: 12, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 12, opacity: 0 }}
        transition={{ duration: 0.16 }}
        className={`bg-elevated rounded-2xl border border-border shadow-2xl w-full ${sizeClass} max-h-[90vh] flex flex-col overflow-hidden`}
        data-testid={testId}
      >
        <header className="flex items-start justify-between px-6 py-4 border-b border-border shrink-0">
          <div className="min-w-0">
            <div className="text-base font-semibold text-white truncate">{title}</div>
            {subtitle && (
              <div className="text-[12px] text-muted mt-0.5">{subtitle}</div>
            )}
          </div>
          <button
            type="button"
            className="text-muted hover:text-white p-1 -mr-1"
            onClick={onClose}
            aria-label="Close"
            data-testid={testId ? `${testId}-close` : undefined}
          >
            <X size={18} />
          </button>
        </header>
        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">{children}</div>
        {footer && (
          <footer className="px-6 py-4 border-t border-border bg-elevated shrink-0 flex items-center justify-end gap-2">
            {footer}
          </footer>
        )}
      </motion.div>
    </div>
  )
}
