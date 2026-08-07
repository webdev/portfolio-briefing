"""Intrinsic value — attach a fair-value read to every single-stock recommendation.

Source of truth for "what is this company worth" in the briefing. Two
independent estimates, fetched from Financial Modeling Prep (FMP):

  1. DCF — FMP's discounted-cash-flow model (`/stable/discounted-cash-flow`).
  2. Analyst price-target consensus (`/stable/price-target-summary`), with the
     number of contributing analysts so the user can weight it.

NOTE: FMP migrated to the "/stable/" API. The legacy /api/v3 + /api/v4
endpoints return HTTP 403 for keys created after 2025-08-31, so we use
/stable/ exclusively. The JSON shapes are the same as the legacy ones.

Design rules (mirror the project's hard constraints):

  - **Single stocks only.** ETFs are baskets — there is no company-level DCF, so
    they are labelled "n/a — basket (ETF)", never given a fabricated number.
  - **Fail closed.** No FMP key, a network error, or an empty response → no
    value is invented. The per-line note shows "n/a" and a single transparency
    footer explains why. We never substitute a default.
  - **Cache.** Results cache to disk for `ttl_hours` (default 24h) so the daily
    run doesn't burn the FMP free-tier quota (2 calls per ticker).

The pure functions (`format_fv_note`, `annotate_intrinsic`, `is_etf`) take
injected data and are fully unit-testable without network. `get_fair_values`
is the only function that touches FMP.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from analysis import rsi_discipline
    from analysis import line_exclusions
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from analysis import rsi_discipline
        from analysis import line_exclusions
    except ImportError:
        import rsi_discipline  # type: ignore
        import line_exclusions  # type: ignore


FMP_BASE = "https://financialmodelingprep.com"

# Broad-market, sector, and thematic ETFs the briefing may surface. A
# recommendation on any of these is labelled a basket, not given a DCF.
# Extend via briefing.yaml -> intrinsic_value.etf_tickers.
DEFAULT_ETFS = {
    # broad market
    "SPY", "VOO", "IVV", "VTI", "QQQ", "QQQM", "DIA", "IWM", "RSP", "VT",
    # sector / style
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "IGV", "VGT", "ARKK", "ARKX", "ARKG",
    # thematic (mirror thematic-scout theme_universes.yaml `etfs`)
    "URA", "NLR", "BOTZ", "ROBO", "PPA", "ITA", "REMX", "QTUM", "HACK",
    "CIBR", "UFO", "TAN", "ICLN", "LIT", "XBI", "IBB",
    "DRAM", "CHPX", "WGMI", "POWR", "GRID",  # memory / AI-semi+quantum / btc-miners / power infra / smart grid
    "OIH",  # VanEck Oil Services — energy diversification theme (verified 2026-08-07)
    # bonds / commodity / vol
    "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "UNG", "VXX", "UVXY",
}


def default_etf_set(config: dict | None = None) -> set[str]:
    """ETF set = built-in defaults ∪ config `intrinsic_value.etf_tickers`."""
    etfs = set(DEFAULT_ETFS)
    cfg = (config or {}).get("intrinsic_value") or {}
    for t in (cfg.get("etf_tickers") or []):
        if t:
            etfs.add(str(t).upper())
    return etfs


def is_etf(ticker: str | None, etf_set: set[str]) -> bool:
    return bool(ticker) and ticker.upper() in etf_set


# ---------------------------------------------------------------------------
# FMP fetch (network) — isolated, fail-closed
# ---------------------------------------------------------------------------

def _to_float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _get_json(url: str, timeout: float):
    """GET + parse JSON. Returns parsed object or None on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "portfolio-briefing"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fetch_fair_value(
    ticker: str,
    *,
    api_key: str,
    base_url: str = FMP_BASE,
    timeout: float = 8.0,
) -> dict | None:
    """Fetch DCF + analyst-target consensus for one ticker. None if neither
    estimate is available (fail closed — never fabricate)."""
    if not api_key:
        return None

    dcf = dcf_date = None
    j = _get_json(f"{base_url}/stable/discounted-cash-flow?symbol={ticker}&apikey={api_key}", timeout)
    if isinstance(j, list) and j and isinstance(j[0], dict):
        dcf = _to_float(j[0].get("dcf"))
        dcf_date = j[0].get("date")

    target = num_analysts = None
    j2 = _get_json(f"{base_url}/stable/price-target-summary?symbol={ticker}&apikey={api_key}", timeout)
    row = None
    if isinstance(j2, list) and j2 and isinstance(j2[0], dict):
        row = j2[0]
    elif isinstance(j2, dict):
        row = j2
    if row:
        target = (_to_float(row.get("lastQuarterAvgPriceTarget"))
                  or _to_float(row.get("lastMonthAvgPriceTarget")))
        n = row.get("lastQuarterCount") or row.get("lastMonthCount")
        try:
            num_analysts = int(n) if n else None
        except (TypeError, ValueError):
            num_analysts = None

    if dcf is None and target is None:
        return None
    return {
        "dcf": dcf,
        "dcf_date": dcf_date,
        "analyst_target": target,
        "num_analysts": num_analysts,
        "source": "FMP",
        "fetched_at": datetime.now().isoformat(),
    }


def _load_cache(cache_path: Path) -> dict:
    try:
        return json.loads(Path(cache_path).read_text())
    except Exception:
        return {}


def _is_fresh(entry: dict, ttl_hours: int) -> bool:
    try:
        ts = datetime.fromisoformat(entry.get("fetched_at", ""))
        return (datetime.now() - ts) < timedelta(hours=ttl_hours)
    except Exception:
        return False


def get_fair_values(
    tickers,
    *,
    cache_path: Path | str,
    ttl_hours: int = 24,
    api_key: str | None = None,
    base_url: str = FMP_BASE,
    timeout: float = 8.0,
) -> dict:
    """Return {TICKER: fair_value_dict} for the requested single-stock tickers.

    Fail-closed: with no FMP key, returns {} (caller surfaces a transparency
    footer instead of fabricating values). Cached results are reused for
    `ttl_hours`; only stale/missing tickers hit the network.
    """
    api_key = api_key or os.getenv("FMP_API_KEY")
    if not api_key:
        return {}

    cache_path = Path(cache_path)
    cache = _load_cache(cache_path)
    out: dict = {}
    dirty = False
    for raw in tickers:
        t = (raw or "").upper()
        if not t:
            continue
        ent = cache.get(t)
        if ent and _is_fresh(ent, ttl_hours):
            out[t] = ent
            continue
        fv = fetch_fair_value(t, api_key=api_key, base_url=base_url, timeout=timeout)
        if fv is not None:
            cache[t] = fv
            out[t] = fv
            dirty = True
        # else: don't cache a miss — retry next cycle.
    if dirty:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=2, default=str))
        except Exception as e:
            print(f"  [warn] intrinsic-value cache write failed: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Pure formatting + annotation (testable without network)
# ---------------------------------------------------------------------------

def format_fv_note(ticker: str, spot: float | None, fv: dict | None,
                   *, etf_set: set[str]) -> str | None:
    """One-line fair-value note for a recommendation. None → caller skips."""
    if is_etf(ticker, etf_set):
        return "💵 FV: n/a — basket (ETF)"
    if not fv:
        return "💵 FV: n/a (no FMP data)"

    bits: list[str] = []
    dcf = fv.get("dcf")
    tgt = fv.get("analyst_target")
    n = fv.get("num_analysts")

    if dcf:
        if spot:
            d = (dcf - spot) / spot * 100.0
            bits.append(f"DCF ${dcf:,.0f} ({d:+.0f}% vs spot)")
        else:
            bits.append(f"DCF ${dcf:,.0f}")
    if tgt:
        ntxt = f", n={int(n)}" if n else ""
        if spot:
            d = (tgt - spot) / spot * 100.0
            bits.append(f"analyst PT ${tgt:,.0f} ({d:+.0f}%{ntxt})")
        else:
            bits.append(f"analyst PT ${tgt:,.0f}{(' (' + ntxt.lstrip(', ') + ')') if ntxt else ''}")

    if not bits:
        return "💵 FV: n/a (no FMP data)"
    return "💵 FV: " + " · ".join(bits)


# Recommendation-header signatures we annotate. A line qualifies when it is a
# numbered action header OR a bold-led header that also carries a recommendation
# keyword — this targets actual trade recommendations and skips prose / the
# Scout's market-read bullets (which carry no rec keyword).
_HEADER_NUM_RE = re.compile(r"^\s*\d+\.\s")
_BOLD_HEADER_RE = re.compile(r"^\s*[-*]?\s*\*\*")
_REC_KEYWORDS = re.compile(
    r"\b(BUY|CSP|SELL TO OPEN|COVERED CALL|WRITE|LEAP|LONG-DATED|ENTRY|"
    r"STRANGLE|SUB-LOT|ADD|TRIM|CLOSE|ROLL|COLLAR|COMPLETE)\b",
    re.I,
)
# Literal trade-ticket line inside an LTO / strategy-upgrade card:
#   "**Trade:** SELL 1× MSFT $410P exp Fri Oct 16 '26 (78 DTE)"
_TRADE_LINE_RE = re.compile(r"^\s*[-*]?\s*\*\*Trade:\*\*")
# Card header with a backticked ticker: "### 💎 8. LONG DATED CSP · `MSFT`"
_CARD_HEADER_TICKER_RE = re.compile(r"^\s*#{2,4}\s.*`([A-Z][A-Z0-9]{0,4})`")


def _is_rec_header(line: str) -> bool:
    if _HEADER_NUM_RE.match(line):
        return True
    if _TRADE_LINE_RE.match(line):
        return True
    if _BOLD_HEADER_RE.match(line) and _REC_KEYWORDS.search(line):
        return True
    return False


def annotate_intrinsic(
    lines: list[str],
    *,
    fv_by_ticker: dict | None,
    spot_by_ticker: dict | None,
    known_tickers,
    etf_set: set[str],
    fmp_available: bool = True,
) -> tuple[list[str], dict]:
    """Append a fair-value note to every single-stock recommendation header.

    Returns (new_lines, stats). Skips the Capital Plan rollup (detailed above),
    italic transparency notes, and any line already carrying an "FV:" note.

    When `fmp_available` is False (no FMP key), single stocks are left
    un-annotated (the caller emits one footer instead of spamming "n/a" on every
    line); ETFs are still marked as baskets since that needs no fetch.
    """
    tickers_sorted = sorted({(t or "").upper() for t in (known_tickers or [])
                             if t}, key=len, reverse=True)
    fv_by_ticker = {(k or "").upper(): v for k, v in (fv_by_ticker or {}).items()}
    spot_by_ticker = {(k or "").upper(): v for k, v in (spot_by_ticker or {}).items()}

    out: list[str] = []
    stats = {"annotated": 0, "etf": 0, "unavailable": 0}
    in_excluded = False
    parent_ticker: str | None = None
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            # Skip sections that render their own fair value: the Capital Plan
            # rollup and the Candidate Trades section (its cards already carry FV).
            in_excluded = ("Capital Plan" in s) or ("Candidate Trades" in s)
        # Track the current card's ticker from its `### ... \`TICK\`` header so
        # a **Trade:** ticket line can inherit it when the line itself doesn't
        # resolve. Continuation/sub-lines NEVER get their own FV (rule #27) —
        # the 2026-07-30 bug attached AT&T's FV to MSFT's "Triggers:" line.
        hm = _CARD_HEADER_TICKER_RE.match(line)
        if hm:
            parent_ticker = hm.group(1).upper()
        elif s.startswith("#"):
            parent_ticker = None
        if line_exclusions.is_excluded_line(line):
            out.append(line)
            continue
        if in_excluded or s.startswith("_") or "FV:" in line or not _is_rec_header(line):
            out.append(line)
            continue
        tk = rsi_discipline.first_known_ticker(line, tickers_sorted)
        if not tk and _TRADE_LINE_RE.match(line):
            # A trade-ticket line whose ticker didn't resolve inherits the
            # parent card's ticker — never a bare-token guess.
            tk = parent_ticker
        if not tk:
            out.append(line)
            continue
        # No FMP key → only mark ETFs (no fetch needed); leave single stocks alone.
        if not fmp_available and not is_etf(tk, etf_set):
            out.append(line)
            continue
        note = format_fv_note(tk, spot_by_ticker.get(tk), fv_by_ticker.get(tk), etf_set=etf_set)
        if note is None:
            out.append(line)
            continue
        out.append(f"{line}  · {note}")
        if "basket" in note:
            stats["etf"] += 1
        elif "n/a" in note:
            stats["unavailable"] += 1
        else:
            stats["annotated"] += 1
    return out, stats
