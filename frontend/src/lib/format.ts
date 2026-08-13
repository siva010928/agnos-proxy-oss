export const fmtInt = (n: number) => (n ?? 0).toLocaleString('en-US')
export const fmtNum = (n: number, d = 0) =>
  (n ?? 0).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

export function fmtUSD(n: number) {
  const v = n ?? 0
  if (v >= 1000) return '$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (v >= 100) return '$' + v.toFixed(1)
  if (v >= 1) return '$' + v.toFixed(2)
  if (v >= 0.001) return '$' + v.toFixed(4)
  if (v > 0) return '$' + v.toFixed(6)
  return '$0.00'
}

export function fmtTokens(n: number) {
  const v = n ?? 0
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(2) + 'M'
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'k'
  return String(v)
}

export const fmtMs = (n: number) => (n >= 1000 ? (n / 1000).toFixed(2) + 's' : Math.round(n) + 'ms')

// All server timestamps are UTC. We render them in IST (Asia/Kolkata) with an
// explicit label so the time shown is unambiguous regardless of the viewer's
// machine timezone (important for a demo viewed from anywhere).
const IST_OPTS: Intl.DateTimeFormatOptions = {
  timeZone: 'Asia/Kolkata',
  year: 'numeric', month: 'short', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
}
function asUTCDate(ts: string | number | Date): Date {
  if (ts instanceof Date) return ts
  if (typeof ts === 'number') return new Date(ts)
  // A bare ISO string with no timezone marker is UTC from our backend - append
  // 'Z' so the browser doesn't misparse it as local time.
  const hasTz = /([zZ]|[+-]\d{2}:?\d{2})$/.test(ts)
  return new Date(hasTz ? ts : ts + 'Z')
}
export function fmtDateTime(ts?: string | number | Date | null) {
  if (ts === null || ts === undefined || ts === '') return '-'
  const d = asUTCDate(ts)
  if (isNaN(d.getTime())) return '-'
  return d.toLocaleString('en-IN', IST_OPTS) + ' IST'
}
export function fmtTime(ts?: string | number | Date | null) {
  if (ts === null || ts === undefined || ts === '') return '-'
  const d = asUTCDate(ts)
  if (isNaN(d.getTime())) return '-'
  return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }) + ' IST'
}

export function timeAgo(ms: number) {
  const s = Math.max(0, (Date.now() - ms) / 1000)
  if (s < 60) return Math.floor(s) + 's ago'
  if (s < 3600) return Math.floor(s / 60) + 'm ago'
  return Math.floor(s / 3600) + 'h ago'
}

export const clsx = (...xs: (string | false | undefined | null)[]) => xs.filter(Boolean).join(' ')
