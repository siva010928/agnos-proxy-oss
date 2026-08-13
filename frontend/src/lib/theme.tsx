import { createContext, useContext, useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

// ─── Color maps for providers + event kinds ───
export const PROV: Record<string, string> = {
  anthropic: '#D97706', bedrock: '#0EA5E9', gemini: '#8B5CF6', openai: '#10B981', azure: '#0EA5E9',
  google_genai: '#8B5CF6', vertex_ai: '#4285F4', litellm_proxy: '#6366F1', ollama: '#64748B', hosted_vllm: '#64748B',
}
export const KIND: Record<string, string> = {
  completion: '#6B9D78', RequestSuccess: '#6B9D78',
  guardrail_block: '#F1617A', GuardrailDecision: '#F1617A',
  fallback: '#CC840B', Fallback: '#CC840B',
  rate_limited: '#FB923C', RateLimited: '#FB923C',
  cache_hit: '#624F7D',
  error: '#F1617A', RequestError: '#F1617A',
  RequestStart: '#6E7890',
}
export const STATUS_COLOR: Record<string, string> = {
  success: '#6B9D78', error: '#F1617A', blocked: '#F1617A', rate_limited: '#FB923C',
}
export function provColor(p?: string) { return PROV[p || ''] || '#6E7890' }
export function kindColor(k?: string) { return KIND[k || ''] || '#6E7890' }

// ─── Light-mode pill text readability ───
// Pills render colored text on a ~12%-alpha tint of the same color. In light
// mode that tint sits over a near-white surface, so bright colors (mint green
// #34D399, amber #FBBF24) become unreadable (contrast < 2.0). This darkens such
// colors - preserving hue - so light-mode badge text stays legible. Dark mode
// and already-dark colors are returned unchanged.
function hexToHsl(hex: string): [number, number, number] | null {
  const m = hex.replace('#', '')
  if (m.length !== 6) return null
  const r = parseInt(m.slice(0, 2), 16) / 255
  const g = parseInt(m.slice(2, 4), 16) / 255
  const b = parseInt(m.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  let h = 0, s = 0
  const l = (max + min) / 2
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h /= 6
  }
  return [h * 360, s * 100, l * 100]
}
export function pillText(hex: string | undefined, theme: 'dark' | 'light'): string {
  const c = hex || '#6B7280'
  if (theme !== 'light') return c
  const hsl = hexToHsl(c)
  if (!hsl) return c
  let [h, s, l] = hsl
  if (l <= 42) return c                  // already dark enough → leave it
  l = 36                                  // clamp lightness for readability on light tint
  s = Math.min(100, s + 8)                // nudge saturation so hue stays vivid
  return `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, ${l}%)`
}

// ─── Theme toggle (dark/light) ───
type Theme = 'dark' | 'light'

const ThemeCtx = createContext<{ theme: Theme; toggle: () => void }>({
  theme: 'dark', toggle: () => {},
})

export const useTheme = () => useContext(ThemeCtx)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === 'undefined') return 'dark'
    return (localStorage.getItem('agnos_theme') as Theme) || 'dark'
  })

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('dark', 'light')
    root.classList.add(theme)
    localStorage.setItem('agnos_theme', theme)
  }, [theme])

  const toggle = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return (
    <ThemeCtx.Provider value={{ theme, toggle }}>
      {children}
    </ThemeCtx.Provider>
  )
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  return (
    <button
      onClick={toggle}
      className="p-1.5 rounded-lg transition-colors hover:bg-border"
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      data-testid="theme-toggle"
    >
      {theme === 'dark' ? <Sun size={15} style={{ color: 'var(--color-muted)' }} /> : <Moon size={15} style={{ color: 'var(--color-muted)' }} />}
    </button>
  )
}
