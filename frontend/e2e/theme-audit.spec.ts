// Theme contrast audit - loads each major page in dark + light mode and
// programmatically flags any visible text whose color is too close to its own
// (alpha-composited) background - i.e. the "light-on-light / dark-on-dark" bug.
import { test, expect } from '@playwright/test'
import { gotoApp } from './_helpers'

const PAGES: Array<[string, string]> = [
  ['/', 'overview'],
  ['/playground', 'playground'],
  ['/cost', 'analytics'],
  ['/logs', 'logs'],
  ['/value', 'value'],
  ['/workspaces', 'workspaces'],
  ['/routing', 'routing'],
  ['/guardrails/rules', 'guardrails'],
  ['/admin/clients', 'clients'],
  ['/admin/providers', 'providers'],
  ['/admin/keys', 'keys'],
  ['/admin/pricing', 'pricing'],
]

// sRGB relative luminance per WCAG
function relLuminance([r, g, b]: number[]) {
  const f = (c: number) => {
    c /= 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
function contrast(fg: number[], bg: number[]) {
  const L1 = relLuminance(fg), L2 = relLuminance(bg)
  const [hi, lo] = L1 > L2 ? [L1, L2] : [L2, L1]
  return (hi + 0.05) / (lo + 0.05)
}
function parseRGB(s: string): number[] | null {
  const m = s.match(/rgba?\(([^)]+)\)/)
  if (!m) return null
  const parts = m[1].split(',').map(x => parseFloat(x.trim()))
  if (parts.length >= 4 && parts[3] === 0) return null // fully transparent → skip
  return [parts[0], parts[1], parts[2], parts.length >= 4 ? parts[3] : 1]
}
function compositeFg(fg: number[], bg: number[]): number[] {
  const a = fg[3] ?? 1
  if (a >= 1) return [fg[0], fg[1], fg[2]]
  return [
    fg[0] * a + bg[0] * (1 - a),
    fg[1] * a + bg[1] * (1 - a),
    fg[2] * a + bg[2] * (1 - a),
  ]
}

for (const theme of ['dark', 'light'] as const) {
  for (const [path, name] of PAGES) {
    test(`theme-audit ${theme} ${name}`, async ({ page }) => {
      // set theme BEFORE the app boots so the right class is applied on first paint
      await page.addInitScript((t) => { localStorage.setItem('agnos_theme', t) }, theme)
      await gotoApp(page, path)
      // dismiss playground tour if present
      const skip = page.getByTestId('tour-skip')
      if (await skip.count()) { await skip.click().catch(() => {}) }
      await page.waitForTimeout(1200)

      // confirm the theme class actually applied
      const cls = await page.evaluate(() => document.documentElement.className)
      expect(cls, `html should carry the ${theme} class`).toContain(theme)

      // Walk visible text nodes; compute fg/bg contrast. Flag < 2.0 (effectively invisible).
      const lowContrast = await page.evaluate(() => {
        // Composite the full ancestor chain (each may be semi-transparent) onto an
        // opaque base, so a 12%-tint badge resolves to its REAL rendered color.
        function parse(s: string): number[] | null {
          const m = s.match(/rgba?\(([^)]+)\)/)
          if (!m) return null
          const p = m[1].split(',').map(x => parseFloat(x.trim()))
          return [p[0], p[1], p[2], p.length >= 4 ? p[3] : 1]
        }
        function over(fg: number[], bg: number[]): number[] {
          const a = fg[3]
          return [
            fg[0] * a + bg[0] * (1 - a),
            fg[1] * a + bg[1] * (1 - a),
            fg[2] * a + bg[2] * (1 - a),
            1,
          ]
        }
        function effectiveBg(el: Element): string {
          // collect bg layers from element up to root
          const layers: number[][] = []
          let node: Element | null = el
          while (node) {
            const c = parse(getComputedStyle(node).backgroundColor)
            if (c && c[3] > 0) layers.push(c)
            node = node.parentElement
          }
          // base = opaque page background (body)
          const base = parse(getComputedStyle(document.body).backgroundColor) || [0, 0, 0, 1]
          // composite from bottom (root) up to the element's own bg
          let acc = base.slice(0, 3).concat([1])
          for (let i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc)
          return `rgb(${Math.round(acc[0])}, ${Math.round(acc[1])}, ${Math.round(acc[2])})`
        }
        const out: Array<{ text: string; color: string; bg: string }> = []
        const els = document.querySelectorAll('body *')
        els.forEach((el) => {
          const he = el as HTMLElement
          // only elements with their OWN direct text content
          const direct = Array.from(he.childNodes)
            .filter(n => n.nodeType === Node.TEXT_NODE)
            .map(n => n.textContent?.trim() || '')
            .join('')
          if (!direct || direct.length < 2) return
          const rect = he.getBoundingClientRect()
          if (rect.width === 0 || rect.height === 0) return
          const cs = getComputedStyle(he)
          if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return
          out.push({ text: direct.slice(0, 40), color: cs.color, bg: effectiveBg(he) })
        })
        return out
      })

      const bad: string[] = []
      for (const item of lowContrast) {
        const fgRaw = parseRGB(item.color)
        const bg = parseRGB(item.bg)
        if (!fgRaw || !bg) continue
        const fg = compositeFg(fgRaw, bg)
        const ratio = contrast(fg, bg)
        if (ratio < 2.0) {
          bad.push(`ratio=${ratio.toFixed(2)} "${item.text}" color=${item.color} bg=${item.bg}`)
        }
      }
      expect(bad, `low-contrast text on ${theme}/${name}:\n  ${bad.join('\n  ')}`).toEqual([])
    })
  }
}
