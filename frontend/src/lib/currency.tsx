// Global currency context (WAVE 25).
// Every page that shows cost reads from this context. The currency picker
// lives in the Shell header so it's always accessible. The FX rate is fetched
// once from /admin/settings/currency and cached in React state.

import React, { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api'

interface CurrencyCtx {
  currency: string
  setCurrency: (c: string) => void
  rate: number            // 1 USD = N <currency>
  symbol: string
  convert: (usd: number) => number
  format: (usd: number) => string
  available: string[]     // every currency we can display (union of rates + symbols)
}

const SYMBOLS: Record<string, string> = {
  USD: '$', EUR: '€', GBP: '£', INR: '₹', JPY: '¥', CNY: '¥', AUD: 'A$', CAD: 'C$',
  NZD: 'NZ$', HKD: 'HK$', SGD: 'S$', KRW: '₩', BRL: 'R$', TRY: '₺', THB: '฿',
  ILS: '₪', PHP: '₱', PLN: 'zł', CHF: 'CHF ', SEK: 'kr ', NOK: 'kr ', DKK: 'kr ',
  ZAR: 'R', MXN: '$', CZK: 'Kč ', HUF: 'Ft ', IDR: 'Rp ', MYR: 'RM ', RON: 'lei ',
  BGN: 'лв ', ISK: 'kr ',
}

// Seed FX rates so switching currency converts INSTANTLY (no waiting on the
// network). The /admin/settings/currency fetch later refines these with live
// rates. Without this seed, a not-yet-loaded rate table made `rate` fall back to
// 1, so switching only changed the symbol until a refresh. (Values match the
// backend fallbacks in gateway/core/fx.py.)
const FALLBACK_RATES: Record<string, number> = {
  USD: 1, EUR: 0.92, GBP: 0.78, INR: 85.0, JPY: 157.0, AUD: 1.52, CAD: 1.37, CHF: 0.89,
  CNY: 7.25, HKD: 7.81, SGD: 1.35, SEK: 10.5, NOK: 10.8, DKK: 6.9, NZD: 1.65, ZAR: 18.5,
  BRL: 5.4, MXN: 18.0, KRW: 1370.0, PLN: 3.95, CZK: 23.0, HUF: 360.0, TRY: 32.0, THB: 36.0,
  IDR: 16200.0, MYR: 4.7, PHP: 58.0, RON: 4.6, BGN: 1.8, ISK: 138.0, ILS: 3.7,
}

// Best-effort "local currency from the user's location" WITHOUT a permission
// prompt. The IANA time zone is the most reliable signal of physical location
// (navigator.language is often en-GB/en-US regardless of where you actually
// are - that's why an India user was wrongly defaulting to GBP). We map the
// time zone first, then fall back to the locale region, then USD.
function currencyFromLocation(): string {
  const EUR_REGIONS = new Set(['DE', 'FR', 'ES', 'IT', 'NL', 'IE', 'PT', 'AT', 'BE', 'FI', 'GR', 'LU', 'SK', 'SI', 'EE', 'LV', 'LT', 'CY', 'MT', 'HR'])
  const EUR_ZONE_CITIES = new Set(['Paris', 'Berlin', 'Madrid', 'Rome', 'Amsterdam', 'Dublin', 'Lisbon', 'Vienna', 'Brussels', 'Helsinki', 'Athens', 'Luxembourg', 'Bratislava', 'Ljubljana', 'Tallinn', 'Riga', 'Vilnius', 'Nicosia', 'Malta', 'Zagreb'])
  try {
    // 1) Time zone → currency (reflects physical location).
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    if (tz) {
      if (tz === 'Asia/Kolkata' || tz === 'Asia/Calcutta') return 'INR'
      if (tz === 'Europe/London') return 'GBP'
      const city = tz.split('/').pop() || ''
      if (tz.startsWith('Europe/') && EUR_ZONE_CITIES.has(city)) return 'EUR'
      if (tz.startsWith('America/')) return 'USD'
    }
  } catch { /* ignore */ }
  try {
    // 2) Locale region → currency (fallback).
    const tags = [navigator.language, ...(navigator.languages || [])].filter(Boolean)
    for (const tag of tags) {
      let region = ''
      try { region = (new Intl.Locale(tag).region || '').toUpperCase() } catch { region = (tag.split('-')[1] || '').toUpperCase() }
      if (!region) continue
      if (region === 'IN') return 'INR'
      if (region === 'GB') return 'GBP'
      if (EUR_REGIONS.has(region)) return 'EUR'
      if (region === 'US') return 'USD'
    }
  } catch { /* ignore */ }
  return 'USD'
}


const Ctx = createContext<CurrencyCtx>({
  currency: 'USD', setCurrency: () => {}, rate: 1, symbol: '$',
  convert: (v) => v, format: (v) => `$${v.toFixed(4)}`, available: ['USD'],
})

export const useCurrency = () => useContext(Ctx)

export function CurrencyProvider({ children }: { children: React.ReactNode }) {
  // Persist the user's explicit choice; otherwise default to their local
  // currency (by locale), falling back to USD.
  const [currency, setCurrencyState] = useState<string>(() => {
    if (typeof window === 'undefined') return 'USD'
    return localStorage.getItem('agnos_currency') || currencyFromLocation()
  })
  // Seeded so conversion works immediately; refined by the live fetch below.
  const [rates, setRates] = useState<Record<string, number>>(FALLBACK_RATES)

  const setCurrency = (c: string) => {
    setCurrencyState(c)
    try { localStorage.setItem('agnos_currency', c) } catch {}
  }

  // Fetch the full live rate table once and MERGE it over the seeded fallbacks
  // (so a partial/failed response can never wipe a known rate back to 1).
  useEffect(() => {
    api('/admin/settings/currency')
      .then((r: any) => {
        if (r && r.rates) setRates((prev) => ({ ...prev, ...r.rates }))
        // Only adopt the backend default if the user hasn't chosen and we
        // couldn't infer a local currency.
        if (!localStorage.getItem('agnos_currency') && r && r.default_currency && currencyFromLocation() === 'USD') {
          setCurrencyState(r.default_currency)
        }
      })
      .catch(() => { /* seeded fallbacks keep conversion working */ })
  }, [])

  const rate = rates[currency] || FALLBACK_RATES[currency] || 1
  const symbol = SYMBOLS[currency] || currency + ' '
  // every currency we can display: whatever the backend published rates for,
  // unioned with our seeded/symbol set, USD first then alphabetical.
  const available = Array.from(new Set(['USD', ...Object.keys(rates), ...Object.keys(SYMBOLS)]))
    .sort((a, b) => (a === 'USD' ? -1 : b === 'USD' ? 1 : a.localeCompare(b)))
  const convert = (usd: number) => usd * rate
  const format = (usd: number) => {
    const v = usd * rate
    if (v >= 1000) return `${symbol}${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    if (v >= 1) return `${symbol}${v.toFixed(2)}`
    if (v >= 0.001) return `${symbol}${v.toFixed(4)}`
    if (v > 0) return `${symbol}${v.toFixed(6)}`
    return `${symbol}0.00`
  }

  return (
    <Ctx.Provider value={{ currency, setCurrency, rate, symbol, convert, format, available }}>
      {children}
    </Ctx.Provider>
  )
}

export function CurrencyPicker() {
  const { currency, setCurrency, available } = useCurrency()
  return (
    <select
      className="bg-app border border-border rounded-lg px-2 py-1 text-xs text-gray-200 outline-none"
      value={currency}
      onChange={(e) => setCurrency(e.target.value)}
      data-testid="global-currency-picker"
      title="Display currency for all cost values"
    >
      {available.map((c) => (
        <option key={c} value={c}>{(SYMBOLS[c] || '').trim()} {c}</option>
      ))}
    </select>
  )
}
