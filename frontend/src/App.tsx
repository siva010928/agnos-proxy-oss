import React, { createContext, useContext, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { Toaster } from './components/Toast'
import { useSSE, GovEvent } from './lib/sse'
import { api } from './lib/api'
import { CurrencyProvider } from './lib/currency'
import { Overview } from './screens/Overview'
import { LiveRequests } from './screens/LiveRequests'
import { RoutingMap } from './screens/RoutingMap'
import { CostAnalytics } from './screens/CostAnalytics'
import { GuardrailsRules } from './screens/GuardrailsRules'
import { GuardrailsProviders } from './screens/GuardrailsProviders'
import { Workspaces } from './screens/Workspaces'
import { Admin } from './screens/Admin'
import { Clients } from './screens/Clients'
import { PlatformValue } from './screens/PlatformValue'
import { Observability } from './screens/Observability'
import { RequestLogs } from './screens/RequestLogs'
import { EngineHealth } from './screens/EngineHealth'
import { CustomPricing } from './screens/CustomPricing'
import { Keys } from './screens/Keys'
import { Providers } from './screens/Providers'
import { Routing } from './screens/Routing'
import { Playground } from './screens/Playground'
import { Docs } from './screens/Docs'

interface SSECtx { events: GovEvent[]; connected: boolean; setPaused: (v: boolean) => void }
const Ctx = createContext<SSECtx>({ events: [], connected: false, setPaused: () => {} })
export const useFeed = () => useContext(Ctx)


// Login gate is disabled in PREVIEW_MODE: instead of gating the dashboard behind a
// sign-in form, we transparently establish a preview admin session once on mount, then
// render the app. Anyone with the link sees the live dashboard. The session is issued
// server-side by /auth/preview (PREVIEW_MODE) - no password ever reaches the browser, so
// this works identically on localhost and prod. Self-hosters can disable PREVIEW_MODE and
// authenticate via /auth/login with credentials instead.
function RequireAuth({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const me: any = await api('/auth/me')
        if (me && me.authenticated) { if (alive) setReady(true); return }
      } catch { /* fall through to preview sign-in */ }
      try {
        await api('/auth/preview', {
          method: 'POST',
          body: JSON.stringify({ preview_name: 'preview' }),
        })
      } catch { /* best effort - render anyway so there is never a login wall */ }
      if (alive) setReady(true)
    })()
    return () => { alive = false }
  }, [])

  if (!ready) return null
  return <>{children}</>
}

export default function App() {
  const sse = useSSE(150)

  // WAVE 20 E2: pause SSE state updates while any form input is focused.
  // This prevents live-feed re-renders from causing layout shift / focus loss
  // while the operator is typing into a field or modal.
  useEffect(() => {
    const FORM_INPUTS = 'input, select, textarea, [role="dialog"] [contenteditable]'
    const onFocus = (e: FocusEvent) => {
      if ((e.target as HTMLElement)?.matches?.(FORM_INPUTS)) {
        sse.setPaused(true)
      }
    }
    const onBlur = (e: FocusEvent) => {
      if ((e.target as HTMLElement)?.matches?.(FORM_INPUTS)) {
        // Small delay so a focus→blur→focus within the same modal (e.g. tab
        // between fields) doesn't flash-unpause mid-typing.
        setTimeout(() => {
          if (!document.activeElement?.matches(FORM_INPUTS)) {
            sse.setPaused(false)
          }
        }, 100)
      }
    }
    document.addEventListener('focusin', onFocus, true)
    document.addEventListener('focusout', onBlur, true)
    return () => {
      document.removeEventListener('focusin', onFocus, true)
      document.removeEventListener('focusout', onBlur, true)
    }
  }, [sse.setPaused])

  return (
    <Ctx.Provider value={{ events: sse.events, connected: sse.connected, setPaused: sse.setPaused }}>
      <CurrencyProvider>
      <Toaster />
      <Routes>
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="/welcome" element={<Navigate to="/" replace />} />
        <Route element={<RequireAuth><Shell connected={sse.connected} /></RequireAuth>}>
          <Route index element={<Overview />} />
          <Route path="live" element={<LiveRequests />} />
          <Route path="routing" element={<RoutingMap />} />
          <Route path="cost" element={<CostAnalytics />} />
          <Route path="guardrails" element={<Navigate to="/guardrails/rules" replace />} />
          <Route path="guardrails/rules" element={<GuardrailsRules />} />
          <Route path="guardrails/providers" element={<GuardrailsProviders />} />
          <Route path="workspaces" element={<Workspaces />} />
          <Route path="admin" element={<Admin />} />
          <Route path="admin/clients" element={<Clients />} />
          <Route path="admin/pricing" element={<CustomPricing />} />
          <Route path="value" element={<PlatformValue />} />
          <Route path="admin/keys" element={<Keys />} />
          <Route path="admin/providers" element={<Providers />} />
          <Route path="admin/routing" element={<Routing />} />
          <Route path="observability" element={<Observability />} />
          <Route path="logs" element={<RequestLogs />} />
          <Route path="engine" element={<EngineHealth />} />
          <Route path="playground" element={<Playground />} />
          <Route path="docs" element={<Docs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      </CurrencyProvider>
    </Ctx.Provider>
  )
}
