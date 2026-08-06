"""Earnings-date discipline — fail CLOSED on missing dates (rule #43).

Origin (2026-07-31 briefing): Rotation Playbook Phase 2 selected RDDT $130P
Sep 04 '26 (42% ann yield, IV rank 90, drawdown 44%) with NO earnings
annotation. RDDT historically prints early August — inside the Sep 04
expiry. The yfinance earnings lookup returned nothing and every downstream
check (EARNINGS_WINDOW validator rule, playbook filter, LT_CSP gate)
silently passed. IV 90 on a beaten-down ad-tech name is exactly pre-earnings
premium.

The fix: for NEW-OPEN recommendations on single-stock underlyings, an
UNKNOWN earnings date is not a pass — it is a WARN (`EARNINGS_DATE_UNKNOWN`)
plus a conviction penalty in the playbook. Data absence ≠ evidence of no
earnings, but it also isn't evidence OF earnings — so the candidate is
demoted + loudly annotated, never silently excluded (hard rule #24).

Single source of truth for:
  - ETF exemption (ETFs have no earnings print)
  - the FMP secondary earnings source (fail-closed: no key / any error →
    None, never a fabricated date; memoized per process)
  - the shared warning phrasing
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date

EARNINGS_UNKNOWN_RULE = "EARNINGS_DATE_UNKNOWN"
# Compact flag for table cells (playbook conviction column).
UNVERIFIED_FLAG = "⚠ earnings unverified"

FMP_BASE = "https://financialmodelingprep.com"

# Per-process memo — the briefing runs once per process, so this doubles as
# the fetch cache (at most one FMP call per ticker per run).
_FMP_MEMO: dict[str, str | None] = {}


def is_earnings_exempt(ticker: str | None, config: dict | None = None) -> bool:
    """True when the underlying has no earnings print (ETFs / baskets).

    Reuses the intrinsic-value ETF set (built-in defaults ∪
    ``intrinsic_value.etf_tickers`` from config) so the two "this is a
    basket" reads never drift apart.
    """
    if not ticker:
        return False
    try:
        from analysis.intrinsic_value import default_etf_set, is_etf
    except ImportError:
        try:
            from intrinsic_value import default_etf_set, is_etf  # type: ignore
        except ImportError:
            return False              # can't tell → treat as single stock
    return is_etf(str(ticker).upper(), default_etf_set(config))


def unknown_reason(ticker: str | None, expiration) -> str:
    """The shared WARN phrasing — always names the expiry to verify against."""
    tk = str(ticker or "?").upper()
    return (f"earnings date unavailable from calendar — verify no {tk} "
            f"print before {expiration} at the broker before placing")


def fmp_next_earnings(
    ticker: str | None,
    *,
    api_key: str | None = None,
    today: date | None = None,
    timeout: float = 6.0,
) -> str | None:
    """Secondary earnings source: FMP ``/stable/earnings?symbol=``.

    Fail-closed on every path — no FMP_API_KEY, network error, empty or
    unparseable payload → None (the caller then treats the date as UNKNOWN
    and the WARN path fires). Never fabricates a date. Memoized per process
    so repeated lookups across briefing surfaces cost one call.
    """
    tk = str(ticker or "").strip().upper()
    if not tk:
        return None
    key = api_key or os.environ.get("FMP_API_KEY")
    if not key:
        return None
    if tk in _FMP_MEMO:
        return _FMP_MEMO[tk]
    as_of = today or date.today()
    out: str | None = None
    try:
        url = f"{FMP_BASE}/stable/earnings?symbol={tk}&apikey={key}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "portfolio-briefing"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = (json.loads(resp.read().decode("utf-8"))
                    if resp.status == 200 else None)
        future: list[str] = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            d = str(r.get("date") or "")[:10]
            try:
                y, m, dd = d.split("-")
                dt = date(int(y), int(m), int(dd))
            except (ValueError, TypeError):
                continue
            if dt >= as_of:
                future.append(dt.isoformat())
        out = min(future) if future else None
    except Exception:
        out = None                    # fail-closed — unknown stays unknown
    _FMP_MEMO[tk] = out
    return out
