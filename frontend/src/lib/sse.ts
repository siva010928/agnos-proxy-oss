import { useCallback, useEffect, useRef, useState } from 'react'

export interface GovEvent {
  event_kind: string
  ts_ms: number
  request_id?: string
  workspace_id?: string
  user_id?: string | null
  use_case?: string | null
  provider?: string
  component?: string | null
  from_provider?: string
  to_provider?: string
  reason?: string
  model_alias?: string
  provider_model_id?: string
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  latency_ms?: number
  attempt?: number
  stream?: boolean
  has_tools?: boolean
  action?: string
  detector?: string
  rule?: string
  excerpt?: string
  stage?: string
  limit_type?: string
  error_type?: string
  _id?: string
}

let _seq = 0

/**
 * Live SSE feed of governance events (most-recent first), capped.
 *
 * WAVE 20 E2 CLS fix:
 *   - Events are BATCHED into ~500ms windows before updating React state,
 *     so the tree sees at most 2 state transitions per second (instead of
 *     one per SSE message, which can be dozens/sec under load).
 *   - `connected` is a separate state so screens that only care about
 *     connectivity (Shell's LiveDot) don't re-render when events arrive.
 *   - The caller can read `paused` + call `setPaused(true)` when a
 *     form/modal is focused; while paused, events accumulate in the buffer
 *     but React state is not updated - zero re-renders, zero layout shift.
 */
export function useSSE(cap = 120) {
  const [events, setEvents] = useState<GovEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const bufferRef = useRef<GovEvent[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pausedRef = useRef(paused)
  pausedRef.current = paused

  // Flush the buffer into React state (~500ms cadence)
  const flush = useCallback(() => {
    if (pausedRef.current) return        // form is focused; don't re-render
    if (bufferRef.current.length === 0) return
    const batch = bufferRef.current.splice(0)
    setEvents((prev) => [...batch, ...prev].slice(0, cap))
  }, [cap])

  useEffect(() => {
    const es = new EventSource('/events')
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data) as GovEvent
        d._id = `${d.ts_ms}-${_seq++}`
        bufferRef.current.push(d)
      } catch { /* ignore keepalive */ }
    }
    // Batch at 500ms - max 2 React state updates per second
    timerRef.current = setInterval(flush, 500)
    return () => {
      es.close()
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [cap, flush])

  // When unpaused after a pause, flush immediately so the UI catches up
  useEffect(() => {
    if (!paused) flush()
  }, [paused, flush])

  return { events, connected, paused, setPaused }
}
