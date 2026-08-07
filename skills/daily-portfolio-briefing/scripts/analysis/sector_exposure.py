"""Sector look-through exposure + diversification-aware conviction.

Single source of truth. George (2026-08-07): "It seems like I'm pretty
heavily invested in tech. Is it the right thing?" — the book is ~90%
tech-correlated (top4 GOOG/NVDA/PLTR/MSFT alone ≈ 53% NLV, $307K of short-put
obligation nearly all tech), and nothing in the briefing measured it.

Three views (``compute_sector_exposure``):

  1. **Equity MV by sector** — held equity market value, ETFs decomposed via
     the curated look-through table in ``config/sector_map.yaml`` (broad-index
     weights are approximate and rendered as '~' estimates, never exact).
  2. **Assignment-adjusted** — each short put's strike×100 obligation added to
     its ticker's sector: the book "if all puts assign".
  3. **Net effective tech-correlated %** — Info Tech + Comm Services sectors
     plus names explicitly flagged ``tech_correlated: true`` in the YAML
     (TSLA, SOFI, AMZN, VRT, ...) and the tech slices of ETF look-throughs.

Ticker→sector resolution order (rule #19 — never guess in code):

  1. curated mapping in ``config/sector_map.yaml``;
  2. FMP profile sector (``/stable/profile``) IF an API key is injected,
     cached to state with a 24h TTL (same pattern as intrinsic_value);
  3. otherwise bucket **"Unclassified"** — surfaced, never counted as tech.

``sector_conviction_adjustment`` (Task B) is the pure conviction hook —
mirrors the ``cp_agreement_bonus`` / ``mv_fv_adjustment`` architecture:
config-gated (``sector_exposure.enabled``, default OFF in code so legacy
paths are byte-identical), returns ``(delta, note)``, NEVER overrides RSI /
earnings / any hard gate — conviction only. It is fed the ASSIGNMENT-ADJUSTED
sector pcts: a new put in an over-cap sector is the thing to penalize.

Config (``briefing.yaml``)::

    sector_exposure:
      enabled: true
      cap_pct: 35          # wheelhouz sector rule — over this flags red
      underweight_pct: 5   # at/below this earns the +1 diversification bonus
      cache_ttl_hours: 24
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

UNCLASSIFIED = "Unclassified"
TECH_SECTORS = {"Information Technology", "Communication Services"}

DEFAULT_CAP_PCT = 35.0
DEFAULT_UNDERWEIGHT_PCT = 5.0
DEFAULT_TTL_HOURS = 24

DEFAULT_MAP_PATH = (Path(__file__).resolve().parents[2]
                    / "config" / "sector_map.yaml")

FMP_BASE = "https://financialmodelingprep.com"

# FMP profile sectors → GICS-style names used by the curated map. An FMP
# string not in this table is used verbatim (it IS measured data, just a
# different taxonomy) — never silently dropped, never guessed.
_FMP_SECTOR_NORMALIZE = {
    "technology": "Information Technology",
    "information technology": "Information Technology",
    "communication services": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "financial services": "Financials",
    "financial": "Financials",
    "financials": "Financials",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "basic materials": "Materials",
    "materials": "Materials",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}

# Short display names for conviction notes ('🧭 sector Info Tech at 62% —
# over 35% cap'); the panel table uses the full GICS names.
_SHORT_NAMES = {
    "Information Technology": "Info Tech",
    "Communication Services": "Comm Services",
    "Consumer Discretionary": "Cons Discretionary",
    "Consumer Staples": "Staples",
    "Health Care": "Health Care",
}

_map_cache: dict | None = None
_map_cache_path: Path | None = None


def _f(v, default=None):
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _se_cfg(config: dict | None) -> dict:
    se = (config or {}).get("sector_exposure") if isinstance(config, dict) else None
    return se if isinstance(se, dict) else {}


def sector_exposure_enabled(config: dict | None) -> bool:
    """Feature gate. Default OFF in code (flags-off → byte-identical legacy);
    briefing.yaml turns it on."""
    return bool(_se_cfg(config).get("enabled", False))


def short_sector(sector: str) -> str:
    return _SHORT_NAMES.get(sector, sector)


# ---------------------------------------------------------------------------
# Curated map loading
# ---------------------------------------------------------------------------

def load_sector_map(path: Path | str | None = None) -> dict:
    """Parse config/sector_map.yaml → {"sectors": {...}, "etf_lookthrough":
    {...}}. Cached per-path at module level. Fail-open: unreadable file →
    empty map (everything buckets Unclassified — never a crash)."""
    global _map_cache, _map_cache_path
    p = Path(path) if path else DEFAULT_MAP_PATH
    if _map_cache is not None and _map_cache_path == p:
        return _map_cache
    try:
        import yaml
        raw = yaml.safe_load(p.read_text()) or {}
    except Exception:
        raw = {}
    smap = {
        "sectors": {str(k).upper(): v for k, v in
                    (raw.get("sectors") or {}).items() if isinstance(v, dict)},
        "etf_lookthrough": {str(k).upper(): v for k, v in
                            (raw.get("etf_lookthrough") or {}).items()
                            if isinstance(v, dict)},
    }
    _map_cache, _map_cache_path = smap, p
    return smap


def etf_lookthrough(ticker: str | None, smap: dict) -> dict | None:
    """The ETF's look-through entry ({"approx": bool, "weights": {...}}) or
    None for non-ETFs / unmapped ETFs."""
    if not ticker:
        return None
    ent = (smap.get("etf_lookthrough") or {}).get(ticker.upper())
    if not isinstance(ent, dict) or not isinstance(ent.get("weights"), dict):
        return None
    return ent


def sector_of(ticker: str | None, smap: dict) -> str | None:
    """Curated sector for a single stock — no network, no guessing.

    ETFs with a SINGLE-sector look-through (SMH/SOXX/SOXL → 100% Info Tech)
    resolve to that sector so a candidate CSP on them still gets a sector
    read; multi-sector ETFs return None (fail-closed)."""
    if not ticker:
        return None
    t = ticker.upper()
    ent = (smap.get("sectors") or {}).get(t)
    if isinstance(ent, dict):
        s = ent.get("sector")
        if s:
            return str(s)
    lt = etf_lookthrough(t, smap)
    if lt:
        weights = {k: _f(v, 0.0) or 0.0 for k, v in lt["weights"].items()}
        total = sum(weights.values())
        for sec, w in weights.items():
            if total > 0 and w / total >= 0.999:
                return str(sec)
    return None


def is_tech_correlated_ticker(ticker: str | None, smap: dict) -> bool:
    """True when the ticker's curated sector is Info Tech / Comm Services OR
    the YAML explicitly flags it ``tech_correlated: true``."""
    if not ticker:
        return False
    ent = (smap.get("sectors") or {}).get(ticker.upper()) or {}
    if isinstance(ent, dict) and ent.get("tech_correlated"):
        return True
    s = sector_of(ticker, smap)
    return s in TECH_SECTORS


# ---------------------------------------------------------------------------
# FMP profile-sector fallback (network) — fail-closed, cached
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "portfolio-briefing"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fetch_fmp_sector(ticker: str, *, api_key: str,
                     base_url: str = FMP_BASE,
                     timeout: float = 6.0) -> str | None:
    """FMP profile sector for one ticker, normalized to GICS-style names.
    None on any failure — the caller buckets Unclassified, never guesses."""
    if not api_key:
        return None
    j = _get_json(f"{base_url}/stable/profile?symbol={ticker}"
                  f"&apikey={api_key}", timeout)
    row = None
    if isinstance(j, list) and j and isinstance(j[0], dict):
        row = j[0]
    elif isinstance(j, dict):
        row = j
    if not row:
        return None
    s = row.get("sector")
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    return _FMP_SECTOR_NORMALIZE.get(s.lower(), s)


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


class _SectorResolver:
    """Curated map → FMP-cache fallback → Unclassified. One instance per
    compute pass so the disk cache is read once and written at most once."""

    def __init__(self, smap: dict, *, api_key: str | None,
                 cache_path: Path | str | None,
                 ttl_hours: int = DEFAULT_TTL_HOURS,
                 base_url: str = FMP_BASE):
        self.smap = smap
        self.api_key = api_key
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl_hours = ttl_hours
        self.base_url = base_url
        self._cache = _load_cache(self.cache_path) if self.cache_path else {}
        self._dirty = False

    def resolve(self, ticker: str) -> str:
        t = (ticker or "").upper()
        if not t:
            return UNCLASSIFIED
        s = sector_of(t, self.smap)
        if s:
            return s
        if not self.api_key:
            return UNCLASSIFIED           # no key → no fetch → Unclassified
        ent = self._cache.get(t)
        if ent and _is_fresh(ent, self.ttl_hours):
            return ent.get("sector") or UNCLASSIFIED
        fetched = fetch_fmp_sector(t, api_key=self.api_key,
                                   base_url=self.base_url)
        if fetched:
            self._cache[t] = {"sector": fetched,
                              "fetched_at": datetime.now().isoformat()}
            self._dirty = True
            return fetched
        return UNCLASSIFIED               # fetch failed → fail-closed

    def flush(self) -> None:
        if not self._dirty or not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, indent=2, default=str))
        except Exception as e:  # pragma: no cover - disk failure
            print(f"  [warn] sector cache write failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Exposure computation (pure given a resolver — testable without network)
# ---------------------------------------------------------------------------

def _slices(ticker: str, amount: float, smap: dict,
            resolver: _SectorResolver) -> tuple[list[tuple[str, float, bool]], bool]:
    """Decompose one position's dollar ``amount`` into
    ``[(sector, slice_amount, is_tech_correlated), ...]``.

    ETFs use the curated look-through (each slice tech-correlated iff its
    sector is a tech sector); single stocks resolve curated→FMP→Unclassified.
    Returns (slices, used_approx_lookthrough)."""
    t = (ticker or "").upper()
    lt = etf_lookthrough(t, smap)
    if lt:
        weights = {str(k): _f(v, 0.0) or 0.0 for k, v in lt["weights"].items()}
        total = sum(weights.values())
        if total <= 0:
            return [(UNCLASSIFIED, amount, False)], False
        out = [(sec, amount * w / total, sec in TECH_SECTORS)
               for sec, w in weights.items() if w > 0]
        return out, bool(lt.get("approx"))
    sec = resolver.resolve(t)
    tech = (sec in TECH_SECTORS) or is_tech_correlated_ticker(t, smap)
    if sec == UNCLASSIFIED:
        tech = False                       # Unclassified never counts as tech
    return [(sec, amount, tech)], False


def compute_sector_exposure(positions: list | None, nlv: float,
                            config: dict | None = None, *,
                            smap: dict | None = None,
                            api_key: str | None = None,
                            cache_path: Path | str | None = None) -> dict:
    """The three look-through views over the snapshot's position list.

    ``api_key`` / ``cache_path`` are injected explicitly (never read from the
    environment here) so tests are deterministic and offline. Percentages are
    0-100 vs NLV; when NLV is unmeasurable they are omitted and no cap flag
    fires (fail-open, never a fabricated denominator).
    """
    cfg = _se_cfg(config)
    cap_pct = _f(cfg.get("cap_pct"), DEFAULT_CAP_PCT)
    ttl = int(_f(cfg.get("cache_ttl_hours"), DEFAULT_TTL_HOURS))
    smap = smap if smap is not None else load_sector_map()
    resolver = _SectorResolver(smap, api_key=api_key, cache_path=cache_path,
                               ttl_hours=ttl)

    equity_by_sector: dict[str, float] = {}
    put_by_sector: dict[str, float] = {}
    equity_total = 0.0
    put_total = 0.0
    tech_equity = 0.0
    tech_puts = 0.0
    unclassified: set[str] = set()
    approx_used = False

    for p in positions or []:
        at = p.get("assetType")
        if at == "EQUITY":
            tk = (p.get("symbol") or "").upper()
            mv = _f(p.get("marketValue"), None)
            if mv is None:
                mv = (_f(p.get("qty"), 0.0) or 0.0) * (_f(p.get("price"), 0.0) or 0.0)
            if not tk or not mv:
                continue
            slices, approx = _slices(tk, mv, smap, resolver)
            approx_used = approx_used or approx
            equity_total += mv
            for sec, amt, tech in slices:
                equity_by_sector[sec] = equity_by_sector.get(sec, 0.0) + amt
                if tech:
                    tech_equity += amt
                if sec == UNCLASSIFIED:
                    unclassified.add(tk)
        elif at == "OPTION":
            if (p.get("type") or "").upper() != "PUT":
                continue
            qty = _f(p.get("qty"), 0.0) or 0.0
            if qty >= 0:
                continue
            und = (p.get("underlying") or "").upper()
            strike = _f(p.get("strike"), 0.0) or 0.0
            if not und or strike <= 0:
                continue
            obligation = strike * 100.0 * abs(qty)
            slices, approx = _slices(und, obligation, smap, resolver)
            approx_used = approx_used or approx
            put_total += obligation
            for sec, amt, tech in slices:
                put_by_sector[sec] = put_by_sector.get(sec, 0.0) + amt
                if tech:
                    tech_puts += amt
                if sec == UNCLASSIFIED:
                    unclassified.add(und)

    resolver.flush()

    assignment_by_sector = dict(equity_by_sector)
    for sec, amt in put_by_sector.items():
        assignment_by_sector[sec] = assignment_by_sector.get(sec, 0.0) + amt

    nlv_f = _f(nlv, 0.0) or 0.0
    over_cap: list[dict] = []
    tech_pct = tech_assignment_pct = None
    if nlv_f > 0:
        tech_pct = tech_equity / nlv_f * 100.0
        tech_assignment_pct = (tech_equity + tech_puts) / nlv_f * 100.0
        for sec in sorted(equity_by_sector,
                          key=lambda s: -equity_by_sector[s]):
            if sec == UNCLASSIFIED:
                continue                   # a data gap, not a sector breach
            pct = equity_by_sector[sec] / nlv_f * 100.0
            if cap_pct and pct > cap_pct:
                over_cap.append({
                    "sector": sec,
                    "pct_nlv": pct,
                    "assignment_pct_nlv":
                        assignment_by_sector.get(sec, 0.0) / nlv_f * 100.0,
                })

    return {
        "nlv": nlv_f,
        "cap_pct": cap_pct,
        "equity_mv_total": equity_total,
        "put_obligation_total": put_total,
        "equity_by_sector": equity_by_sector,
        "put_obligation_by_sector": put_by_sector,
        "assignment_by_sector": assignment_by_sector,
        "tech_correlated_equity_mv": tech_equity,
        "tech_correlated_assignment": tech_equity + tech_puts,
        "tech_pct_nlv": tech_pct,
        "tech_assignment_pct_nlv": tech_assignment_pct,
        "over_cap": over_cap,
        "unclassified_tickers": sorted(unclassified),
        "approx_lookthrough": approx_used,
    }


def assignment_pcts(exposure: dict | None) -> dict[str, float]:
    """{sector: assignment-adjusted % of NLV (0-100)} — the pcts that feed
    ``sector_conviction_adjustment``. Empty when NLV is unmeasurable
    (fail-closed → zero adjustment)."""
    if not isinstance(exposure, dict):
        return {}
    nlv = _f(exposure.get("nlv"), 0.0) or 0.0
    if nlv <= 0:
        return {}
    return {sec: amt / nlv * 100.0
            for sec, amt in (exposure.get("assignment_by_sector") or {}).items()}


# ---------------------------------------------------------------------------
# Task B — diversification-aware conviction (pure; cp_agreement_bonus pattern)
# ---------------------------------------------------------------------------

def sector_conviction_adjustment(ticker: str,
                                 current_sector_pcts: dict[str, float] | None,
                                 config: dict | None,
                                 *, smap: dict | None = None
                                 ) -> tuple[float, str | None]:
    """Diversification conviction delta for a NEW-open candidate.

    ``current_sector_pcts`` MUST be the ASSIGNMENT-ADJUSTED sector % of NLV
    (``assignment_pcts``) — a new put in an over-cap sector is exactly the
    thing to penalize. Returns ``(delta, note)``:

    - candidate's sector already ≥ ``cap_pct`` (default 35) →
      ``(-1.0, "🧭 sector Info Tech at 62% — over 35% cap")``
    - sector ≤ ``underweight_pct`` (default 5, including wholly absent when
      the book WAS measured) and the candidate is otherwise actionable →
      ``(+1.0, "🧭 diversifies — Financials at 2%")``. The caller only
      invokes this on already-actionable candidates — the bonus corroborates,
      it never qualifies.
    - in between / feature off / unknown sector / unmeasured book →
      ``(0.0, None)`` (fail-closed).

    Conviction only — NEVER overrides RSI / earnings / any hard gate, and is
    never applied to position management.
    """
    if not sector_exposure_enabled(config):
        return 0.0, None
    if not current_sector_pcts:
        return 0.0, None                  # book unmeasured → no adjustment
    cfg = _se_cfg(config)
    cap = _f(cfg.get("cap_pct"), DEFAULT_CAP_PCT)
    under = _f(cfg.get("underweight_pct"), DEFAULT_UNDERWEIGHT_PCT)
    smap = smap if smap is not None else load_sector_map()
    sec = sector_of(ticker, smap)
    if not sec or sec == UNCLASSIFIED:
        return 0.0, None                  # unknown sector → fail-closed
    pct = _f(current_sector_pcts.get(sec), 0.0) or 0.0
    if cap and pct >= cap:
        return -1.0, (f"🧭 sector {short_sector(sec)} at {pct:.0f}% — "
                      f"over {cap:.0f}% cap")
    if pct <= under:
        return 1.0, f"🧭 diversifies — {short_sector(sec)} at {pct:.0f}%"
    return 0.0, None
