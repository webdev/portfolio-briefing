"""True chain-implied vol — ATM IV, 25Δ skew, term structure (task #43).

Motivation — the AMZN "IV rank 100 / 4% ann" case (2026-07-31): the
pipeline's legacy "IV rank" is a 252-day REALIZED-vol percentile
(yfinance closes). It is backward-looking, so it spikes AFTER big moves
and has repeatedly claimed "IV rank 100 = fat premium" on names whose
actual implied premium had crushed (AMZN post-gap delivered 4% ann, GOOG
9-15% ann — ``analysis/iv_honesty.py`` exists to apologize for this).
Meanwhile every snapshot fetches ~400 option chains with real per-strike
``impliedVolatility`` sitting unused for this purpose.

This module computes, per underlying, from the chains already fetched
this cycle:

- ``atm_iv_30d`` — ATM implied vol interpolated to a 30-DTE horizon
  (mid-IV of the strikes bracketing spot, per expiry).
- ``skew_25d`` — 25-delta put IV − 25-delta call IV on the expiry
  nearest 30 DTE. When the chain carries no deltas (yfinance chains
  don't), a documented moneyness proxy is used: the 25Δ strike is
  approximated as K = S·exp(∓z·σ_atm·√T) with z = 0.675 (the 25th-
  percentile z-score of a lognormal terminal distribution — i.e. the
  strike a ~25-delta option sits at under Black-Scholes, ignoring drift
  and the vol smile's own feedback). Positive skew = puts bid.
- ``term_slope`` — front-expiry ATM IV − back-expiry ATM IV. Positive =
  inverted term structure (front-stressed), wheelhouz signal #8.

**True IV rank** is the percentile of today's ``atm_iv_30d`` against the
trailing history of the SAME measure, persisted per-day per-ticker to
``state/chain_iv_history.json`` (rolling 252 entries). While history is
shorter than ``min_history_days`` (default 20) the rank renders as
"building history" and the legacy realized-vol percentile stays available
as a LABELED companion (``RVrank``) — never as "IV rank".

Signals (flag-only, no autonomous actions — flags, not forecasts):
- skew blowout: today's skew_25d > mean + ``skew_sigma_threshold``·σ of
  its own trailing history (wheelhouz signal #7).
- term inversion: term_slope > 0 (wheelhouz signal #8).

Fail-closed everywhere: insane IVs (outside 0.05..5.0) are discarded, a
chain with no usable IV near spot yields None, and nothing is ever
fabricated — a missing measure renders as the labeled RV fallback.

Config (briefing.yaml → ``chain_iv``):
    enabled: true
    history_path: state/chain_iv_history.json   # relative to the skill root
    min_history_days: 20
    skew_sigma_threshold: 2.0
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from datetime import date as _date
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────

IV_SANE_LO = 0.05          # below this an "IV" is a data artifact, not a vol
IV_SANE_HI = 5.0           # above this likewise (500% vol)
_Z_25_DELTA = 0.675        # lognormal 25th-percentile z — moneyness proxy
_ATM_MAX_DIST_PCT = 0.10   # one-sided ATM fallback must be within 10% of spot
_MIN_FRONT_DTE = 5         # skip last-week expiries for the term front leg
ROLLING_LIMIT = 252        # per-ticker history entries kept
_MIN_SKEW_OBS = 10         # minimum history obs before a skew z is computed

DEFAULT_MIN_HISTORY_DAYS = 20
DEFAULT_SKEW_SIGMA_THRESHOLD = 2.0
DEFAULT_HISTORY_FILENAME = "chain_iv_history.json"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# "IV rank 62" / "IV rank 62.5" tokens — but NOT "IV rank 61→69" transition
# notes and NOT "IV rank ≥ 50" rule text (no digits directly after the space).
_IV_RANK_TOKEN_RE = re.compile(r"\bIV rank (\d+(?:\.\d+)?)(?!\d)(?!\s*→)")


# ── Config ────────────────────────────────────────────────────────────────


def load_chain_iv_config(config: dict | None) -> dict:
    """Merge briefing.yaml → ``chain_iv`` over defaults."""
    cfg = {
        "enabled": True,
        "history_path": None,
        "min_history_days": DEFAULT_MIN_HISTORY_DAYS,
        "skew_sigma_threshold": DEFAULT_SKEW_SIGMA_THRESHOLD,
    }
    user = (config or {}).get("chain_iv") if isinstance(config, dict) else None
    user = user if isinstance(user, dict) else {}
    if "enabled" in user:
        cfg["enabled"] = bool(user["enabled"])
    if user.get("history_path"):
        cfg["history_path"] = str(user["history_path"])
    for key in ("min_history_days",):
        try:
            if user.get(key) is not None:
                cfg[key] = int(user[key])
        except (TypeError, ValueError):
            pass
    for key in ("skew_sigma_threshold",):
        try:
            if user.get(key) is not None:
                cfg[key] = float(user[key])
        except (TypeError, ValueError):
            pass
    return cfg


def resolve_history_path(config: dict | None, snapshot_dir: Path | None) -> Path:
    """History file location: config override, else ``<skill>/state/chain_iv_history.json``.

    ``snapshot_dir`` is ``<skill>/state/briefing_snapshots/<date>`` — the
    default drops the file next to ``briefing_snapshots/`` in the skill's
    state dir so it survives snapshot pruning.
    """
    cfg = load_chain_iv_config(config)
    if cfg["history_path"]:
        p = Path(os.path.expanduser(cfg["history_path"]))
        if not p.is_absolute() and snapshot_dir is not None:
            # Relative paths are anchored at the skill root (two levels above
            # the snapshot root), so "state/chain_iv_history.json" lands in
            # the skill's own state dir regardless of CWD.
            try:
                return snapshot_dir.resolve().parents[2] / p
            except IndexError:
                return p
        return p
    if snapshot_dir is not None:
        try:
            return snapshot_dir.resolve().parents[1] / DEFAULT_HISTORY_FILENAME
        except IndexError:
            pass
    return Path("state") / DEFAULT_HISTORY_FILENAME


# ── Per-chain math ────────────────────────────────────────────────────────


def _sane_iv(v) -> float | None:
    """Sanity-bounded IV: 0.05 < iv < 5.0 → float, else None (never fabricate)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or not (IV_SANE_LO < f < IV_SANE_HI):
        return None
    return f


def _strike_ivs(contracts) -> list[tuple[float, float]]:
    """[(strike, iv)] for contracts with a sane impliedVolatility."""
    out: list[tuple[float, float]] = []
    for c in contracts or []:
        if not isinstance(c, dict):
            continue
        iv = _sane_iv(c.get("impliedVolatility"))
        try:
            k = float(c.get("strike"))
        except (TypeError, ValueError):
            continue
        if iv is not None and k > 0:
            out.append((k, iv))
    return out


def atm_iv(chain: dict | None, spot: float | None) -> float | None:
    """ATM implied vol of one chain: mid-IV of the strikes bracketing spot.

    Uses the chain's per-strike ``impliedVolatility`` fields (yfinance
    chains carry them), merging calls and puts (IVs at identical strikes
    are averaged). Sanity bound 0.05 < iv < 5.0. When spot sits outside
    the listed strikes, falls back to the single nearest strike ONLY if
    it is within 10% of spot — otherwise returns None. Never fabricates.
    """
    if not isinstance(chain, dict) or spot is None:
        return None
    try:
        s = float(spot)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None

    merged: dict[float, list[float]] = {}
    for side in ("calls", "puts"):
        for k, iv in _strike_ivs(chain.get(side)):
            merged.setdefault(k, []).append(iv)
    if not merged:
        return None
    pts = sorted((k, sum(vs) / len(vs)) for k, vs in merged.items())

    below = [(k, iv) for k, iv in pts if k <= s]
    above = [(k, iv) for k, iv in pts if k >= s]
    if below and above:
        lo_k, lo_iv = below[-1]
        hi_k, hi_iv = above[0]
        if hi_k == lo_k:
            return lo_iv
        # Linear interpolation in strike between the bracketing quotes.
        w = (s - lo_k) / (hi_k - lo_k)
        return lo_iv + w * (hi_iv - lo_iv)
    # One-sided: only trust a strike near spot.
    k, iv = below[-1] if below else above[0]
    if abs(k - s) / s <= _ATM_MAX_DIST_PCT:
        return iv
    return None


def _dte(expiration: str | None, as_of: _date) -> int | None:
    try:
        return (_date.fromisoformat(str(expiration)) - as_of).days
    except (TypeError, ValueError):
        return None


def _wing_iv_by_delta(contracts, target_abs_delta: float = 0.25) -> float | None:
    """IV of the contract whose |delta| is nearest 0.25 — chain-delta path."""
    best: tuple[float, float] | None = None   # (distance, iv)
    for c in contracts or []:
        if not isinstance(c, dict) or c.get("delta") is None:
            continue
        iv = _sane_iv(c.get("impliedVolatility"))
        if iv is None:
            continue
        try:
            d = abs(float(c.get("delta")))
        except (TypeError, ValueError):
            continue
        dist = abs(d - target_abs_delta)
        if best is None or dist < best[0]:
            best = (dist, iv)
    return best[1] if best else None


def _wing_iv_by_moneyness(contracts, target_strike: float) -> float | None:
    """IV at the strike nearest the moneyness-proxy 25Δ target strike."""
    pts = _strike_ivs(contracts)
    if not pts or target_strike <= 0:
        return None
    k, iv = min(pts, key=lambda p: abs(p[0] - target_strike))
    # Refuse a "wing" more than 25% away from the target — that's not the
    # wing, that's whatever happened to be listed (never fabricate).
    if abs(k - target_strike) / target_strike > 0.25:
        return None
    return iv


def skew_25d(chain: dict | None, spot: float | None, atm: float | None,
             dte: int | None) -> float | None:
    """25Δ put IV − 25Δ call IV for one chain (positive = puts bid).

    Prefers real chain deltas when contracts carry a ``delta`` field.
    Falls back to the documented moneyness proxy: 25Δ strike ≈
    S·exp(∓0.675·σ_atm·√T) (lognormal 25th-percentile z-score).
    """
    if not isinstance(chain, dict) or spot is None:
        return None
    put_iv = _wing_iv_by_delta(chain.get("puts"))
    call_iv = _wing_iv_by_delta(chain.get("calls"))
    if put_iv is None or call_iv is None:
        if atm is None or dte is None or dte <= 0:
            return None
        try:
            s = float(spot)
        except (TypeError, ValueError):
            return None
        if s <= 0:
            return None
        width = _Z_25_DELTA * atm * math.sqrt(dte / 365.0)
        if put_iv is None:
            put_iv = _wing_iv_by_moneyness(chain.get("puts"), s * math.exp(-width))
        if call_iv is None:
            call_iv = _wing_iv_by_moneyness(chain.get("calls"), s * math.exp(width))
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


def _interp_to_30d(pairs: list[tuple[int, float]]) -> float | None:
    """Linear interpolation of ATM IV to a 30-DTE horizon; nearest outside."""
    if not pairs:
        return None
    pairs = sorted(pairs)
    below = [p for p in pairs if p[0] <= 30]
    above = [p for p in pairs if p[0] >= 30]
    if below and above:
        lo_d, lo_iv = below[-1]
        hi_d, hi_iv = above[0]
        if hi_d == lo_d:
            return lo_iv
        w = (30 - lo_d) / (hi_d - lo_d)
        return lo_iv + w * (hi_iv - lo_iv)
    return (below[-1] if below else above[0])[1]


def iv_metrics(chains_for_underlying, spot: float | None,
               as_of: _date | None = None) -> dict:
    """Chain-implied metrics for one underlying across its expirations.

    Args:
        chains_for_underlying: dict {expiration: chain} or list of chain
            dicts (each carrying an ``expiration`` key).
        spot: the underlying's spot price this cycle.
        as_of: valuation date (defaults to today).

    Returns a dict with ``atm_iv_30d``, ``skew_25d``, ``term_slope``,
    ``expirations_used`` — each None when unmeasurable (fail-closed).
    """
    as_of = as_of or _date.today()
    out = {"atm_iv_30d": None, "skew_25d": None, "term_slope": None,
           "expirations_used": []}

    items: list[tuple[str, dict]] = []
    if isinstance(chains_for_underlying, dict):
        items = [(str(k), v) for k, v in chains_for_underlying.items()
                 if isinstance(v, dict)]
    else:
        for ch in chains_for_underlying or []:
            if isinstance(ch, dict) and ch.get("expiration"):
                items.append((str(ch["expiration"]), ch))
    per_exp: list[tuple[int, str, dict, float]] = []   # (dte, exp, chain, atm)
    for exp, ch in items:
        d = _dte(exp, as_of)
        if d is None or d <= 0:
            continue
        a = atm_iv(ch, spot)
        if a is None:
            continue
        per_exp.append((d, exp, ch, a))
    if not per_exp:
        return out
    per_exp.sort()
    out["expirations_used"] = [e for _, e, _, _ in per_exp]

    out["atm_iv_30d"] = _interp_to_30d([(d, a) for d, _, _, a in per_exp])

    # Skew on the expiry nearest 30 DTE.
    near = min(per_exp, key=lambda p: abs(p[0] - 30))
    out["skew_25d"] = skew_25d(near[2], spot, near[3], near[0])

    # Term slope: front (first expiry ≥ 5 DTE, avoiding last-week gamma
    # noise; falls back to the shortest) minus back (longest expiry).
    # Positive = inverted / front-stressed.
    if len(per_exp) >= 2:
        fronts = [p for p in per_exp if p[0] >= _MIN_FRONT_DTE]
        front = fronts[0] if fronts else per_exp[0]
        back = per_exp[-1]
        if back[0] > front[0]:
            out["term_slope"] = front[3] - back[3]
    return out


def compute_metrics_for_snapshot(chains: dict | None,
                                 spot_by_ticker: dict | None,
                                 as_of: _date | None = None) -> dict:
    """Per-underlying iv_metrics from a snapshot's ``chains`` map.

    ``chains`` is the snapshot shape {"SYM_YYYY-MM-DD": chain_dict}; the
    underlying is read from each chain's own ``underlying`` field (never
    parsed from the key — tickers can contain separators).
    """
    grouped: dict[str, dict[str, dict]] = {}
    for key, ch in (chains or {}).items():
        if not isinstance(ch, dict):
            continue
        u = str(ch.get("underlying") or "").upper()
        exp = str(ch.get("expiration") or "")
        if not u or not exp:
            # Fall back to the key convention SYM_YYYY-MM-DD.
            parts = str(key).rsplit("_", 1)
            if len(parts) == 2 and _DATE_RE.match(parts[1]):
                u = u or parts[0].upper()
                exp = exp or parts[1]
        if not u or not exp:
            continue
        grouped.setdefault(u, {})[exp] = ch

    out: dict[str, dict] = {}
    spots = {str(k).upper(): v for k, v in (spot_by_ticker or {}).items()}
    for u, by_exp in grouped.items():
        spot = spots.get(u)
        try:
            spot = float(spot) if spot is not None else None
        except (TypeError, ValueError):
            spot = None
        if spot is None or spot <= 0:
            continue   # fail-closed: no spot → no metrics for this name
        m = iv_metrics(by_exp, spot, as_of=as_of)
        if m.get("atm_iv_30d") is None:
            continue
        m["spot"] = spot
        out[u] = m
    return out


# ── History (state/chain_iv_history.json) ─────────────────────────────────


def load_history(path: Path) -> dict:
    """Load the rolling per-ticker history; empty structure when absent."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("tickers"), dict):
            raw.setdefault("version", 1)
            raw.setdefault("dates_seeded", [])
            return raw
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {"version": 1, "dates_seeded": [], "tickers": {}}


def update_history(history: dict, date_str: str,
                   metrics_by_ticker: dict) -> dict:
    """Record one day's metrics (idempotent — same date overwrites),
    trimming each ticker to the most recent ``ROLLING_LIMIT`` dates."""
    tickers = history.setdefault("tickers", {})
    for tk, m in (metrics_by_ticker or {}).items():
        if not isinstance(m, dict) or m.get("atm_iv_30d") is None:
            continue
        entry = {"atm_iv_30d": round(float(m["atm_iv_30d"]), 6)}
        for k in ("skew_25d", "term_slope"):
            if m.get(k) is not None:
                entry[k] = round(float(m[k]), 6)
        series = tickers.setdefault(str(tk).upper(), {})
        series[str(date_str)] = entry
        if len(series) > ROLLING_LIMIT:
            for old in sorted(series)[:-ROLLING_LIMIT]:
                del series[old]
    return history


def save_history(path: Path, history: dict) -> None:
    """Atomic write (tempfile + replace) so a crash never corrupts state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def attach_ranks(chain_iv_map: dict, history: dict,
                 config: dict | None = None) -> dict:
    """Mutate each ticker's metrics with the TRUE IV rank + signal flags.

    - ``iv_rank``: percentile of today's atm_iv_30d against the trailing
      history of the same measure (None while history < min_history_days).
    - ``history_days``: how many daily observations exist.
    - ``skew_z`` / ``skew_blowout``: today's skew vs its own trailing
      mean/σ (needs ≥ 10 obs; threshold ``skew_sigma_threshold``).
    - ``term_inversion``: term_slope > 0 (front-stressed).
    """
    cfg = load_chain_iv_config(config)
    min_days = cfg["min_history_days"]
    sigma_thr = cfg["skew_sigma_threshold"]
    tickers = (history or {}).get("tickers") or {}
    for tk, m in (chain_iv_map or {}).items():
        if not isinstance(m, dict):
            continue
        series = tickers.get(str(tk).upper()) or {}
        ivs = [e.get("atm_iv_30d") for _, e in sorted(series.items())
               if isinstance(e, dict) and e.get("atm_iv_30d") is not None]
        n = len(ivs)
        m["history_days"] = n
        cur = m.get("atm_iv_30d")
        if cur is not None and n >= min_days:
            m["iv_rank"] = round(
                100.0 * sum(1 for v in ivs if v <= cur) / n, 1)
        else:
            m["iv_rank"] = None
        # Skew blowout vs the skew's OWN trailing distribution.
        skews = [e.get("skew_25d") for _, e in sorted(series.items())
                 if isinstance(e, dict) and e.get("skew_25d") is not None]
        m["skew_z"] = None
        m["skew_blowout"] = False
        cur_skew = m.get("skew_25d")
        if cur_skew is not None and len(skews) >= _MIN_SKEW_OBS:
            mean = sum(skews) / len(skews)
            var = sum((v - mean) ** 2 for v in skews) / len(skews)
            sd = math.sqrt(var)
            if sd > 1e-9:
                z = (cur_skew - mean) / sd
                m["skew_z"] = round(z, 2)
                m["skew_blowout"] = z >= sigma_thr
        m["term_inversion"] = bool(
            m.get("term_slope") is not None and m["term_slope"] > 0)
    return chain_iv_map


def backfill_history(snapshots_root: Path, history_path: Path,
                     config: dict | None = None,
                     verbose: bool = False) -> int:
    """Seed history from stored snapshot ``chains/`` dirs. Idempotent.

    Iterates every dated snapshot dir under ``snapshots_root`` that has a
    ``chains/`` subdir, computes that day's per-underlying metrics (spots
    from the day's own technicals.json / quotes.json), and records them.
    Dates already seeded are skipped, so re-runs are no-ops. Returns the
    number of NEW dates seeded.
    """
    snapshots_root = Path(snapshots_root)
    history = load_history(history_path)
    seeded_dates = set(history.get("dates_seeded") or [])
    new_dates = 0
    if not snapshots_root.is_dir():
        return 0
    for day_dir in sorted(snapshots_root.iterdir()):
        name = day_dir.name
        if not day_dir.is_dir() or not _DATE_RE.match(name):
            continue
        if name in seeded_dates:
            continue
        chains_dir = day_dir / "chains"
        if not chains_dir.is_dir():
            continue
        try:
            as_of = _date.fromisoformat(name)
        except ValueError:
            continue
        spots: dict[str, float] = {}
        for fname, keys in (("technicals.json", ("spot",)),
                            ("quotes.json", ("last", "lastTrade", "price"))):
            try:
                data = json.loads((day_dir / fname).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for sym, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                for k in keys:
                    v = entry.get(k)
                    if v is not None and str(sym).upper() not in spots:
                        try:
                            fv = float(v)
                        except (TypeError, ValueError):
                            continue
                        if fv > 0:
                            spots[str(sym).upper()] = fv
                        break
        chains: dict[str, dict] = {}
        for cf in chains_dir.glob("*.json"):
            try:
                ch = json.loads(cf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(ch, dict):
                chains[cf.stem] = ch
        metrics = compute_metrics_for_snapshot(chains, spots, as_of=as_of)
        if metrics:
            update_history(history, name, metrics)
        seeded_dates.add(name)
        new_dates += 1
        if verbose:
            print(f"  [chain-iv] backfilled {name}: {len(metrics)} tickers",
                  file=sys.stderr)
    history["dates_seeded"] = sorted(seeded_dates)
    if new_dates:
        save_history(history_path, history)
    return new_dates


# ── Consumers: gate preference, labels, annotation, panel ─────────────────


def effective_iv_rank(ticker: str | None, chain_iv_map: dict | None,
                      rv_rank: float | None) -> tuple[float | None, str]:
    """The rank the gate battery / playbook should test ``iv_rank ≥ N`` on.

    Preference order: TRUE chain-implied rank when available → labeled
    realized-vol fallback → (None, "none"). The AMZN failure mode is the
    reason for the order: post-gap RVrank 100 with true implied crushed.
    """
    tk = str(ticker or "").upper()
    m = (chain_iv_map or {}).get(tk)
    if isinstance(m, dict) and m.get("iv_rank") is not None:
        try:
            return float(m["iv_rank"]), "chain"
        except (TypeError, ValueError):
            pass
    if rv_rank is not None:
        try:
            return float(rv_rank), "rv"
        except (TypeError, ValueError):
            pass
    return None, "none"


# One-voice vol display (George 2026-08-12, rec_grade_audit): a true chain
# IVr and the realized-vol proxy ≥ this many points apart is a DIVERGENCE
# the card must state explicitly — never two vol numbers unexplained (the
# MU 'RVrank 84' card while true chain IVr was 15).
VOL_DIVERGENCE_PTS = 30.0


def _divergence_phrase(true_rank, rv_rank) -> str | None:
    """'proxy diverges — premium thinner/richer than the proxy suggests'
    when |true − proxy| ≥ VOL_DIVERGENCE_PTS; None otherwise / unmeasured."""
    try:
        t, r = float(true_rank), float(rv_rank)
    except (TypeError, ValueError):
        return None
    if abs(t - r) < VOL_DIVERGENCE_PTS:
        return None
    direction = "thinner" if r > t else "richer"
    return f"proxy diverges — premium {direction} than the proxy suggests"


def format_vol_display(eff_rank, source, rv_rank=None) -> str:
    """THE one-voice vol string for recommendation/candidate cards.

    Displays the SAME effective vol the Setup Grade uses
    (``effective_iv_rank`` preference order): true chain IVr preferred,
    labeled 'IVr'; the realized-vol proxy only when chain IV history is
    unavailable, labeled 'RVr (realized-vol proxy)'. When both exist and
    diverge by ≥ VOL_DIVERGENCE_PTS, the divergence is stated explicitly:
    'IVr 15 (true chain) · RVr 84 proxy diverges — premium thinner than
    the proxy suggests'. Never two vol numbers on one card unexplained.
    """
    if eff_rank is None:
        return "IV n/a"
    try:
        eff = float(eff_rank)
    except (TypeError, ValueError):
        return "IV n/a"
    if source == "chain":
        phrase = _divergence_phrase(eff, rv_rank)
        if phrase:
            return (f"IVr {eff:.0f} (true chain) · "
                    f"RVr {float(rv_rank):.0f} {phrase}")
        return f"IVr {eff:.0f}"
    return f"RVr {eff:.0f} (realized-vol proxy)"


def vol_display_for(ticker: str | None, chain_iv_map: dict | None,
                    rv_rank=None) -> str:
    """``format_vol_display`` resolved from a snapshot's chain_iv map —
    the convenience every display site routes through so the card shows
    the SAME effective vol the setup grade scored (one voice)."""
    eff, src = effective_iv_rank(ticker, chain_iv_map, rv_rank)
    return format_vol_display(eff, src, rv_rank=rv_rank)


def iv_label(metrics: dict | None, rv_rank: float | None,
             min_history_days: int = DEFAULT_MIN_HISTORY_DAYS) -> str:
    """Honest one-token IV read for a ticker.

    - true rank available:      ``IV 34% · IVrank 62``
    - true rank, proxy ≥30 pts apart: the divergence is stated explicitly
      (``· RVrank 84 proxy diverges — premium thinner than the proxy
      suggests``) — never two vol numbers unexplained (one-voice rule).
    - true IV, short history:   ``IV 34% (IVrank n/a — building history, 12d)``
    - chains missing:           ``RVrank 55 (realized-vol proxy)``
    - nothing:                  ``IV n/a``
    """
    rv_s = None
    if rv_rank is not None:
        try:
            rv_s = f"RVrank {float(rv_rank):.0f}"
        except (TypeError, ValueError):
            rv_s = None
    if isinstance(metrics, dict) and metrics.get("atm_iv_30d") is not None:
        atm_s = f"IV {float(metrics['atm_iv_30d']) * 100:.0f}%"
        if metrics.get("iv_rank") is not None:
            base = f"{atm_s} · IVrank {float(metrics['iv_rank']):.0f}"
            if rv_s:
                phrase = _divergence_phrase(metrics["iv_rank"], rv_rank)
                return (f"{base} · {rv_s} {phrase}" if phrase
                        else f"{base} · {rv_s}")
            return base
        n = int(metrics.get("history_days") or 0)
        base = f"{atm_s} (IVrank n/a — building history, {n}d)"
        return f"{base} · {rv_s}" if rv_s else base
    if rv_s:
        return f"{rv_s} (realized-vol proxy)"
    return "IV n/a"


def _flag_bits(metrics: dict | None) -> str:
    """Informational signal tags (flags, not forecasts) for a ticker."""
    bits = []
    if isinstance(metrics, dict):
        if metrics.get("skew_blowout"):
            z = metrics.get("skew_z")
            z_s = f", z {z:+.1f}" if z is not None else ""
            bits.append(f"🌊 skew blowout (25Δ "
                        f"{float(metrics.get('skew_25d') or 0) * 100:+.1f} "
                        f"vol pts{z_s})")
        if metrics.get("term_inversion"):
            bits.append("⚠ term inversion (front-stressed)")
    return (" · " + " · ".join(bits)) if bits else ""


def annotate_briefing(md: str, chain_iv_map: dict | None,
                      rv_ranks: dict | None,
                      config: dict | None = None) -> tuple[str, dict]:
    """Rewrite every legacy "IV rank N" token in the briefing honestly.

    The legacy token IS the realized-vol proxy, so every occurrence gets a
    truthful label: when the line's ticker has TRUE chain IV this cycle it
    becomes ``IV 34% · IVrank 62 · RVrank N`` (building-history variant
    while history < min_history_days), plus the skew-blowout / term-
    inversion informational tags; otherwise it becomes ``RVrank N
    (realized-vol proxy)``. Italic transparency footers and lines already
    carrying IVrank/RVrank labels are left untouched (idempotent).

    Returns (annotated_md, stats).
    """
    cfg = load_chain_iv_config(config)
    chain_iv_map = {str(k).upper(): v for k, v in (chain_iv_map or {}).items()}
    rv_ranks = {str(k).upper(): v for k, v in (rv_ranks or {}).items()}
    stats = {"true": 0, "building": 0, "rv_only": 0}

    try:
        from analysis.rsi_discipline import first_known_ticker
    except ImportError:
        from rsi_discipline import first_known_ticker  # type: ignore

    known = sorted(set(chain_iv_map) | set(rv_ranks), key=len, reverse=True)

    out_lines: list[str] = []
    for line in md.split("\n"):
        stripped = line.strip()
        if (not _IV_RANK_TOKEN_RE.search(line)
                or stripped.startswith("_")
                or "IVrank" in line or "RVrank" in line):
            out_lines.append(line)
            continue
        tk = first_known_ticker(line, known) if known else None
        m = chain_iv_map.get(tk) if tk else None

        def _repl(match: re.Match) -> str:
            old = float(match.group(1))
            if isinstance(m, dict) and m.get("atm_iv_30d") is not None:
                if m.get("iv_rank") is not None:
                    stats["true"] += 1
                else:
                    stats["building"] += 1
                return iv_label(m, old, cfg["min_history_days"]) + _flag_bits(m)
            stats["rv_only"] += 1
            return f"RVrank {old:.0f} (realized-vol proxy)"

        out_lines.append(_IV_RANK_TOKEN_RE.sub(_repl, line, count=0))
    return "\n".join(out_lines), stats


def render_vol_surface(chain_iv_map: dict | None,
                       config: dict | None = None,
                       rv_ranks: dict | None = None,
                       top_n: int = 5) -> list[str]:
    """Compact "Vol Surface" subsection for Market Context.

    Top ``top_n`` skew/term readings across held + candidate names (the
    chain map only ever contains names whose chains were fetched this
    cycle). Flag-only — wheelhouz signals #7 (skew blowout) / #8 (term
    inversion) surface as tags, never as autonomous actions. Empty map →
    no section (fail-closed, nothing fabricated).
    """
    cfg = load_chain_iv_config(config)
    entries = [(tk, m) for tk, m in (chain_iv_map or {}).items()
               if isinstance(m, dict) and m.get("atm_iv_30d") is not None]
    if not entries:
        return []

    def _key(item):
        tk, m = item
        flagged = bool(m.get("skew_blowout") or m.get("term_inversion"))
        z = abs(m.get("skew_z") or 0.0)
        skew = abs(m.get("skew_25d") or 0.0)
        return (1 if flagged else 0, z, skew)

    top = sorted(entries, key=_key, reverse=True)[:top_n]
    ranked = sum(1 for _, m in entries if m.get("iv_rank") is not None)
    lines = ["### Vol Surface — chain-implied (true IV)", ""]
    for tk, m in top:
        rv = (rv_ranks or {}).get(tk)
        bits = [iv_label(m, rv, cfg["min_history_days"])]
        if m.get("skew_25d") is not None:
            z = m.get("skew_z")
            z_s = f" (z {z:+.1f})" if z is not None else ""
            bits.append(f"25Δ skew {float(m['skew_25d']) * 100:+.1f} pts{z_s}")
        if m.get("term_slope") is not None:
            bits.append(f"term {float(m['term_slope']) * 100:+.1f} pts")
        flags = _flag_bits(m).lstrip(" ·")
        if flags:
            bits.append(flags.strip())
        lines.append(f"- **{tk}** — " + " · ".join(bits))
    blowouts = sum(1 for _, m in entries if m.get("skew_blowout"))
    inversions = sum(1 for _, m in entries if m.get("term_inversion"))
    lines.append(
        f"_ATM 30d implied vol measured from this cycle's chains for "
        f"{len(entries)} name(s) · true IVrank on {ranked} (history ≥ "
        f"{cfg['min_history_days']}d) · {blowouts} skew blowout(s) · "
        f"{inversions} term inversion(s). Flags, not forecasts._")
    lines.append("")
    return lines
