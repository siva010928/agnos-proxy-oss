/** Agnos Proxy - Design System
 *  Brand palette + light/dark mode via CSS vars. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // All core colors reference CSS vars - toggled by .dark / .light class on <html>
        app: 'var(--color-app)',
        surface: 'var(--color-surface)',
        'surface-2': 'var(--color-surface-2)',
        elevated: 'var(--color-elevated)',
        border: 'var(--color-border)',
        'border-strong': 'var(--color-border-strong)',
        accent: { DEFAULT: 'var(--color-accent)', light: 'var(--color-accent-light)' },
        ok: 'var(--color-ok)',
        warn: 'var(--color-warn)',
        danger: 'var(--color-danger)',
        info: 'var(--color-info)',
        muted: 'var(--color-muted)',
        insight: 'var(--color-insight)',
        // Provider badges - consistent across modes
        prov: {
          anthropic: '#D97706',
          bedrock: '#0EA5E9',
          gemini: '#8B5CF6',
          openai: '#10B981',
          azure: '#0EA5E9',
        },
        // Event kinds
        kind: {
          completion: 'var(--color-ok)',
          guardrail: 'var(--color-danger)',
          fallback: 'var(--color-warn)',
          rate: '#FB923C',
          cache: 'var(--color-insight)',
          error: 'var(--color-danger)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '8px',
        lg: '12px',
        xl: '16px',
        '2xl': '20px',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        'card-hover': 'var(--shadow-card-hover)',
      },
      keyframes: {
        slidein: { '0%': { opacity: '0', transform: 'translateY(-6px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        pulseLive: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
      },
      animation: { slidein: 'slidein .2s ease-out', pulseLive: 'pulseLive 1.5s ease-in-out infinite' },
    },
  },
  plugins: [],
}
