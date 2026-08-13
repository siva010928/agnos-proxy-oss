import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './lib/theme'
import App from './App'
import './index.css'

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

// Establish the passwordless preview admin session BEFORE the app mounts, so providers
// that fetch on mount (currency, SSE) never race ahead of the session cookie - that race
// caused 403s on a cold load. Best-effort with a short timeout so a slow or preview-disabled
// backend never blocks the first paint.
async function bootstrapPreviewSession() {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), 2500)
  try {
    const me = await fetch('/auth/me', { credentials: 'include', signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null)).catch(() => null)
    if (!me?.authenticated) {
      await fetch('/auth/preview', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preview_name: 'preview' }),
        signal: ctrl.signal,
      }).catch(() => {})
    }
  } catch { /* best effort - render regardless */ }
  clearTimeout(t)
}

bootstrapPreviewSession().finally(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ThemeProvider>
      <QueryClientProvider client={qc}>
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      </QueryClientProvider>
      </ThemeProvider>
    </React.StrictMode>,
  )
})
