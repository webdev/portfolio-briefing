"""FINVIZ public-mode quote.ashx scraper.

Splits cleanly into two layers:

  - `parse_quote_page(html, ticker)` — pure HTML → dict. No I/O. Testable
    against fixture files. This is where ALL parsing lives.

  - `fetch_one(ticker, ...)` — network layer. Rate-limited HTTP GET, then
    passes the body to `parse_quote_page`. Fails closed on any non-200,
    captcha redirect, or parse failure (returns None).

The parser handles real FINVIZ snapshot-table2 structure (label/value pairs
in alternating <td> cells, multiple tables per page).
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# ─── Parsing ────────────────────────────────────────────────────────────────


def _parse_float(s: str | None) -> float | None:
    """Convert a FINVIZ cell value to float. `-`, empty, or unparseable → None.

    Handles trailing % (drops it), comma thousand separators, K/M/B suffixes
    on counts (e.g. "1.23M" → 1_230_000), and leading + / -.
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace(",", "")
    if s in ("-", "", "N/A"):
        return None
    # Strip % suffix
    if s.endswith("%"):
        s = s[:-1]
    # K/M/B suffix on counts
    mult = 1
    if s and s[-1] in "KMB":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _recommendation_label(score: float | None) -> str | None:
    """FINVIZ uses a 1.0-5.0 score (1=Strong Buy, 5=Strong Sell).
    Map to a human label following FINVIZ's own buckets."""
    if score is None:
        return None
    if score <= 1.5:
        return "Strong Buy"
    if score <= 2.5:
        return "Buy"
    if score <= 3.5:
        return "Hold"
    if score <= 4.5:
        return "Sell"
    return "Strong Sell"


def parse_quote_page(html: str, ticker: str) -> dict[str, Any] | None:
    """Parse a FINVIZ quote.ashx page into the canonical target dict.

    Returns None when:
      - the page is a captcha / error page (no snapshot-table2)
      - target price is missing AND analyst recommendation is missing
        (no signal to surface — don't waste a chip slot)

    The dict keys mirror what `analysis/finviz_targets.py` consumes.
    """
    try:
        from bs4 import BeautifulSoup  # noqa: WPS433 — lazy import
    except ImportError as exc:
        raise ImportError(
            "FINVIZ fetcher requires beautifulsoup4: pip install beautifulsoup4 lxml"
        ) from exc

    if not html or "snapshot-table2" not in html:
        # Captcha / blocked / empty page — fail closed
        return None

    # Prefer lxml (fast, robust) but fall back to Python's built-in
    # html.parser when lxml isn't installed. Without this fallback the
    # entire FINVIZ pipeline step dies with "Couldn't find a tree builder
    # with the features you requested: lxml" — a fresh clone with only
    # `beautifulsoup4` installed shouldn't break the briefing.
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # Spot price lives in a separate quote-price-* table at the top
    spot_price = _extract_spot_price(soup)

    # Snapshot table: rows of alternating (label, value, label, value, ...)
    snapshot: dict[str, str] = {}
    for table in soup.find_all("table", {"class": "snapshot-table2"}):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            for i in range(0, len(cells), 2):
                if i + 1 < len(cells):
                    label = cells[i].get_text(strip=True)
                    value = cells[i + 1].get_text(strip=True)
                    if label:
                        snapshot[label] = value

    target_price = _parse_float(snapshot.get("Target Price"))
    recommendation = _parse_float(snapshot.get("Recom"))

    if target_price is None and recommendation is None:
        # No actionable analyst signal on this page
        return None

    # Compute upside %; needs both spot and target
    upside_pct: float | None = None
    if target_price is not None and spot_price is not None and spot_price > 0:
        upside_pct = round((target_price - spot_price) / spot_price * 100, 1)

    return {
        "ticker": ticker.upper(),
        "spot_price": spot_price,
        "target_price": target_price,
        "target_upside_pct": upside_pct,
        "analyst_recommendation": recommendation,
        "analyst_label": _recommendation_label(recommendation),
        "p_e": _parse_float(snapshot.get("P/E")),
        "fwd_p_e": _parse_float(snapshot.get("Forward P/E")),
        "eps_growth_next_y_pct": _parse_float(snapshot.get("EPS next Y")),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "finviz_public",
    }


def _extract_spot_price(soup: Any) -> float | None:
    """Find the current spot price in a FINVIZ page header.

    FINVIZ has changed the markup a few times; we try several patterns
    before giving up. None on miss — fail closed.
    """
    # Pattern A: 2024-2026 markup — quote-price class on a <strong>
    el = soup.find(class_=re.compile(r"quote-price"))
    if el is not None:
        v = _parse_float(el.get_text(strip=True))
        if v is not None:
            return v
    # Pattern B: js-table-prices-wrap (mobile layout)
    el = soup.find(class_=re.compile(r"prices-wrap"))
    if el is not None:
        match = re.search(r"\b\d+\.\d+\b", el.get_text())
        if match:
            return _parse_float(match.group(0))
    # Pattern C: scrape from the snapshot table "Price" cell (always present)
    for table in soup.find_all("table", {"class": "snapshot-table2"}):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            for i in range(0, len(cells), 2):
                if i + 1 < len(cells):
                    if cells[i].get_text(strip=True) == "Price":
                        return _parse_float(cells[i + 1].get_text(strip=True))
    return None


# ─── Network layer ─────────────────────────────────────────────────────────


@dataclass
class FetchStats:
    """Captures rate-limit + error state across a batch fetch."""
    requests_made: int = 0
    successful: int = 0
    blocked: int = 0
    parse_failures: int = 0
    daily_cap_hit: bool = False


def fetch_one(
    ticker: str,
    *,
    user_agent: str,
    timeout_seconds: float = 15.0,
    last_request_time: list[float] | None = None,
    rate_limit_seconds: float = 2.0,
    stats: FetchStats | None = None,
) -> dict[str, Any] | None:
    """HTTP GET a single quote page and parse it.

    `last_request_time` is a single-element list used as a mutable carrier
    for the throttle clock across calls (Python doesn't have nonlocal at
    the module level; the list trick keeps the API stateless-looking).
    """
    try:
        import requests  # noqa: WPS433 — lazy import
    except ImportError as exc:
        raise ImportError(
            "FINVIZ fetcher requires requests: pip install requests"
        ) from exc

    # Throttle
    if last_request_time is not None and last_request_time[0] > 0:
        elapsed = time.time() - last_request_time[0]
        if elapsed < rate_limit_seconds:
            time.sleep(rate_limit_seconds - elapsed)

    url = f"https://finviz.com/quote.ashx?t={ticker.upper()}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
        )
    except requests.RequestException as e:
        print(f"FINVIZ fetch error for {ticker}: {e}", file=sys.stderr)
        if stats is not None:
            stats.blocked += 1
        if last_request_time is not None:
            last_request_time[0] = time.time()
        return None

    if last_request_time is not None:
        last_request_time[0] = time.time()
    if stats is not None:
        stats.requests_made += 1

    if resp.status_code != 200:
        print(
            f"FINVIZ {resp.status_code} for {ticker} — likely blocked/captcha",
            file=sys.stderr,
        )
        if stats is not None:
            stats.blocked += 1
        return None

    parsed = parse_quote_page(resp.text, ticker)
    if parsed is None:
        if stats is not None:
            stats.parse_failures += 1
    else:
        if stats is not None:
            stats.successful += 1
    return parsed
