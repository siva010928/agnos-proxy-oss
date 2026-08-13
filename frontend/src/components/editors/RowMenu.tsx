// Portal-rendered row-menu popover. Renders into <body> with FIXED positioning
// anchored to the trigger via getBoundingClientRect(), so no `overflow-x-auto`
// or sticky/main scroll ancestor can clip it. This is the structural fix for
// the bug where the ⋯ menu on Keys/Components was invisible on lists with one
// row (clipped by the table's overflow-x-auto wrapper, z-index can't escape).
//
// Usage:
//   <RowMenu testId={`key-menu-${id}`}
//     items={[{ label: 'Rotate', onSelect: rotate, testId: `key-rotate-${id}` },
//             { label: 'Disable', onSelect: disable, danger: true, testId: `key-disable-${id}` }]} />
import { MoreVertical } from 'lucide-react'
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface RowMenuItem {
  label: React.ReactNode
  icon?: React.ReactNode
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
  testId?: string
  hint?: string
}

export function RowMenu({
  items,
  testId,
  ariaLabel = 'Row actions',
}: {
  items: RowMenuItem[]
  testId?: string
  ariaLabel?: string
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  // Position the menu below-right of the trigger; if it'd overflow the
  // viewport we flip above. Also re-position on scroll/resize.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const place = () => {
      const t = triggerRef.current!.getBoundingClientRect()
      const menuH = menuRef.current?.offsetHeight || 120
      const menuW = menuRef.current?.offsetWidth || 180
      const vh = window.innerHeight
      const vw = window.innerWidth
      // Default: just below, right-aligned
      let top = t.bottom + 4
      let left = t.right - menuW
      if (top + menuH > vh - 8) top = t.top - menuH - 4   // flip above
      if (left < 8) left = 8                                // clamp left
      if (left + menuW > vw - 8) left = vw - menuW - 8      // clamp right
      setPos({ top, left })
    }
    place()
    const ro = new ResizeObserver(place)
    if (menuRef.current) ro.observe(menuRef.current)
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      ro.disconnect()
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  // Outside click + Esc
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (menuRef.current?.contains(t) || triggerRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        className="text-muted hover:text-white p-1.5 rounded-md hover:bg-app"
        onClick={() => setOpen((o) => !o)}
        data-testid={testId}
      >
        <MoreVertical size={16} />
      </button>
      {open && pos &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            aria-label={ariaLabel}
            className="fixed z-[100] min-w-[180px] rounded-lg shadow-2xl py-1"
            style={{ top: pos.top, left: pos.left,
                     background: 'var(--color-surface)',
                     border: '1px solid var(--color-border-strong)',
                     boxShadow: '0 12px 32px -8px rgba(0,0,0,0.45), 0 0 0 1px var(--color-border-strong)' }}
            data-testid={testId ? `${testId}-popover` : undefined}
          >
            {items.map((item, i) => (
              <button
                key={i}
                role="menuitem"
                type="button"
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false)
                  if (!item.disabled) item.onSelect()
                }}
                className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors ${
                  item.disabled
                    ? 'text-muted cursor-not-allowed'
                    : item.danger
                      ? 'text-danger hover:bg-danger/10'
                      : 'text-gray-100 hover:bg-app'
                }`}
                data-testid={item.testId}
                title={item.hint}
              >
                {item.icon && <span className="shrink-0">{item.icon}</span>}
                <span className="flex-1">{item.label}</span>
              </button>
            ))}
          </div>,
          document.body
        )
      }
    </>
  )
}
