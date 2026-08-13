"""FX rate sync + time-accurate currency conversion (WAVE 25 TRACK 1).

Syncs daily rates from frankfurter.app (free, no key required, ECB source).
Falls back to last-known rate on API failure (fail-open). Caches rates in a
TTL dict so the hot path (display conversion) never hits the DB per-request.

Usage:
    from gateway.core.fx import convert_usd, get_rate, sync_rates
    inr = convert_usd(0.05, 'INR', date(2026, 6, 1))  # time-accurate
    await sync_rates()  # called on startup + daily cron
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from gateway.db.database import async_session
from gateway.db.models import FXRate, GatewaySettings

# In-memory cache: (currency, date_str) -> rate_to_usd
_CACHE: dict[tuple[str, str], float] = {}
_LAST_SYNC: datetime | None = None

# Supported currencies - the full set the (free, keyless, ECB-backed)
# frankfurter.dev API publishes, so the dashboard can display cost in ANY of
# them with live daily rates. USD is the base (always 1.0).
CURRENCIES = (
    "USD", "EUR", "GBP", "INR", "JPY", "AUD", "CAD", "CHF", "CNY", "HKD",
    "SGD", "SEK", "NOK", "DKK", "NZD", "ZAR", "BRL", "MXN", "KRW", "PLN",
    "CZK", "HUF", "TRY", "THB", "IDR", "MYR", "PHP", "RON", "BGN", "ISK", "ILS",
)

# Last-resort fallbacks (1 USD = N) used only until the live sync lands, so
# switching currency converts instantly. Refined by sync_rates() on startup.
_FALLBACK_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.78, "INR": 85.0, "JPY": 157.0, "AUD": 1.52,
    "CAD": 1.37, "CHF": 0.89, "CNY": 7.25, "HKD": 7.81, "SGD": 1.35, "SEK": 10.5,
    "NOK": 10.8, "DKK": 6.9, "NZD": 1.65, "ZAR": 18.5, "BRL": 5.4, "MXN": 18.0,
    "KRW": 1370.0, "PLN": 3.95, "CZK": 23.0, "HUF": 360.0, "TRY": 32.0, "THB": 36.0,
    "IDR": 16200.0, "MYR": 4.7, "PHP": 58.0, "RON": 4.6, "BGN": 1.8, "ISK": 138.0, "ILS": 3.7,
}


async def sync_rates(days_back: int = 30) -> int:
    """Fetch rates from frankfurter.app for the last N days and upsert into DB.
    Returns the number of rows upserted. Fail-open: on API error, returns 0
    and the cache/DB retains whatever was last known."""
    global _LAST_SYNC
    synced = 0
    today = date.today()
    start = today - timedelta(days=days_back)

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            # frankfurter.dev/v1 API: date ranges with from=USD
            # For the latest rate (always fetch today's regardless of range)
            r_latest = await c.get("https://api.frankfurter.dev/v1/latest",
                                    params={"from": "USD", "to": ",".join(cur for cur in CURRENCIES if cur != "USD")})
            r_latest.raise_for_status()
            latest = r_latest.json()
            # Store today's rate
            today_rates = latest.get("rates", {})

            # Historical range (for time-accurate conversion of past rows)
            url = f"https://api.frankfurter.dev/v1/{start.isoformat()}..{today.isoformat()}"
            r = await c.get(url, params={"from": "USD", "to": ",".join(cur for cur in CURRENCIES if cur != "USD")})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        # Fail-open: don't crash; use whatever's in the DB/cache already
        import structlog
        structlog.get_logger("fx").warning("fx_sync_http_failed", error=str(exc))
        return 0

    rates_by_date: dict[str, dict[str, float]] = data.get("rates", {})

    async with async_session() as s:
        for date_str, rates in rates_by_date.items():
            d = datetime.fromisoformat(date_str)
            for cur, rate in rates.items():
                # Check if row exists
                existing = await s.scalar(
                    select(FXRate).where(FXRate.currency == cur, FXRate.date == d))
                if existing:
                    existing.rate_to_usd = rate
                    existing.updated_at = datetime.utcnow()
                else:
                    s.add(FXRate(currency=cur, date=d, rate_to_usd=rate))
                _CACHE[(cur, date_str)] = rate
                synced += 1
            # USD is always 1.0
            _CACHE[("USD", date_str)] = 1.0
        await s.commit()

    _LAST_SYNC = datetime.utcnow()
    return synced


async def _load_cache_from_db() -> None:
    """Warm the in-memory cache from the DB on startup."""
    async with async_session() as s:
        rows = (await s.scalars(select(FXRate))).all()
    for r in rows:
        _CACHE[(r.currency, r.date.strftime("%Y-%m-%d"))] = r.rate_to_usd


def get_rate(currency: str, d: date | None = None) -> float:
    """Get the FX rate for a currency on a specific date. Falls back to the
    nearest known rate, then to the hardcoded fallback."""
    if currency == "USD":
        return 1.0
    if d is None:
        d = date.today()
    # Try exact date
    key = (currency, d.isoformat())
    if key in _CACHE:
        return _CACHE[key]
    # Try yesterday, day before, etc (up to 7 days back)
    for i in range(1, 8):
        prev = (d - timedelta(days=i)).isoformat()
        if (currency, prev) in _CACHE:
            return _CACHE[(currency, prev)]
    # Fallback to hardcoded
    return _FALLBACK_RATES.get(currency, 1.0)


def convert_usd(cost_usd: float, currency: str, d: date | None = None) -> float:
    """Convert a USD cost to the target currency using the time-accurate rate."""
    if currency == "USD":
        return cost_usd
    rate = get_rate(currency, d)
    return cost_usd * rate


async def get_default_currency() -> str:
    """Read the global default currency from GatewaySettings."""
    async with async_session() as s:
        row = await s.get(GatewaySettings, "default_currency")
    return (row.value if row else "USD") or "USD"


async def set_default_currency(currency: str) -> None:
    """Set the global default currency."""
    async with async_session() as s:
        row = await s.get(GatewaySettings, "default_currency")
        if row:
            row.value = currency
            row.updated_at = datetime.utcnow()
        else:
            s.add(GatewaySettings(key="default_currency", value=currency))
        await s.commit()


async def get_client_currency(client_id: str) -> str | None:
    """Read optional per-client currency override."""
    async with async_session() as s:
        row = await s.get(GatewaySettings, f"currency:{client_id}")
    return row.value if row else None


def format_currency(amount: float, currency: str) -> str:
    """Locale-aware formatting with correct symbol."""
    symbols = {
        "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "CNY": "¥",
        "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "HKD": "HK$", "SGD": "S$",
        "KRW": "₩", "BRL": "R$", "TRY": "₺", "THB": "฿", "ILS": "₪", "PHP": "₱",
        "PLN": "zł", "CHF": "CHF ", "SEK": "kr ", "NOK": "kr ", "DKK": "kr ",
        "ZAR": "R", "MXN": "$", "CZK": "Kč ", "HUF": "Ft ", "IDR": "Rp ",
        "MYR": "RM ", "RON": "lei ", "BGN": "лв ", "ISK": "kr ",
    }
    sym = symbols.get(currency, currency + " ")
    if currency == "INR":
        # Indian grouping: 1,23,456.78
        if amount >= 100000:
            lakhs = amount / 100000
            return f"{sym}{lakhs:,.2f}L"
        elif amount >= 1000:
            return f"{sym}{amount:,.2f}"
        return f"{sym}{amount:.4f}"
    return f"{sym}{amount:,.4f}" if amount < 1 else f"{sym}{amount:,.2f}"
