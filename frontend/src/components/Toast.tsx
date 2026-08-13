import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertCircle, CheckCircle2, X } from 'lucide-react'

export interface Toast { id: number; kind: 'error' | 'success'; msg: string }

let _id = 0
const _listeners = new Set<(t: Toast) => void>()

export function toast(kind: 'error' | 'success', msg: string) {
  const t = { id: ++_id, kind, msg }
  _listeners.forEach((l) => l(t))
}
export const toastError = (msg: string) => toast('error', msg)
export const toastOk = (msg: string) => toast('success', msg)

/** Wrap an async action: on throw, show the real error message in a toast. */
export async function withToast<T>(fn: () => Promise<T>, okMsg?: string): Promise<T | undefined> {
  try {
    const r = await fn()
    if (okMsg) toastOk(okMsg)
    return r
  } catch (e: any) {
    toastError(e?.message || 'Request failed')
    return undefined
  }
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([])
  useEffect(() => {
    const add = (t: Toast) => {
      setToasts((p) => [...p, t])
      setTimeout(() => setToasts((p) => p.filter((x) => x.id !== t.id)), 6000)
    }
    _listeners.add(add)
    return () => { _listeners.delete(add) }
  }, [])
  return (
    <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 w-[360px]" data-testid="toaster">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} initial={{ opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 40 }}
            data-testid={`toast-${t.kind}`}
            className="card p-3 flex items-start gap-2 border"
            style={{ borderColor: t.kind === 'error' ? '#F8717155' : '#34D39955' }}>
            {t.kind === 'error' ? <AlertCircle size={16} className="text-danger shrink-0 mt-0.5" />
              : <CheckCircle2 size={16} className="text-ok shrink-0 mt-0.5" />}
            <div className="text-sm text-gray-200 flex-1 break-words">{t.msg}</div>
            <button className="text-muted" onClick={() => setToasts((p) => p.filter((x) => x.id !== t.id))}><X size={14} /></button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
