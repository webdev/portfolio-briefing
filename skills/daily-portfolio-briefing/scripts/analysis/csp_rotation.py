"""CSP Rotation Recommender (task #20) — close a lower-yield held CSP to fund
a higher-yield new CSP at the same-or-lower total obligation.

Origin: user 2026-07-07 — the pipeline surfaced disciplined CSP entries
(MRVL, INTC, MU $830P, CRWV, IREN) on a red day but ALL were capacity-gated
(coverage 0.17x < 0.50x). "I'd rather close a current CSP with less yields
and get into another one with better opportunities." This module makes that
swap explicit: score every held short put's REMAINING annualized yield, score
every open-side candidate's annualized yield, and propose coverage-neutral
(or coverage-improving) rotations where the yield upgrade clears a threshold.

Yield math (both sides use the same convention — annualized return on the
collateral the position ties up):

  held (remaining):  extrinsic_remaining × 100 × qty × 365
                     ─────────────────────────────────────
                       days_remaining × collateral

    where extrinsic_remaining = max(current_mid − intrinsic, 0) and
    intrinsic = max(strike − spot, 0) when spot is known (an ITM put's mid is
    mostly intrinsic you DON'T earn by holding — only the extrinsic decays).
    When spot is unknown the full mid is used (conservative for the rotation:
    it OVERSTATES the held yield, making rotations harder to qualify — we
    never overstate the alpha of a swap on missing data).

  candidate (new):   premium_mid × 100 × 365 / (dte × collateral)

Discipline honored (all fail-open — a missing input can only make the module
surface FEWER rotations, never a wrong one, and any exception returns []):

  #39 lt_verdict_gate      — never rotate INTO a broken/downtrending chart
  #40 put_overlap_check    — never rotate INTO a strike within 5% of a held put
  earnings ≤ 21d           — never rotate INTO an imminent print
  directive holds          — close-side skips fable_advisor_memory hold names
  capture ≥ 30%            — close-side only surfaces banked winners (never
                             proposes realizing a loss to fund a new trade)
  #21 bucket decompression — closes sitting in a ≥20%-NLV single-expiration
                             bucket earn a score bonus (the rotation ALSO
                             de-concentrates the calendar)
  #19 no fabricated data   — candidates without a real measured premium are
                             skipped, never estimated

Config (briefing.yaml → csp_rotation):
  min_yield_delta        (default 2.0)   candidate must yield ≥ 2× the held's remaining
  min_freed_ratio        (default 0.95)  freed collateral ≥ required × this
  min_close_capture      (default 0.30)  close-side capture floor
  max_rotations          (default 5)
  earnings_block_days    (default 21)
  preferred_close_max_dte(default 60)    short-tail closes preferred (soft)
  bucket_warning_pct     (default 0.20)  bucket-bonus threshold (% NLV)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from itertools import combinations

# ── Defaults (config-overridable) ─────────────────────────────────────────
DEFAULT_MIN_YIELD_DELTA = 2.0
DEFAULT_MIN_FREED_RATIO = 0.95
DEFAULT_MIN_CLOSE_CAPTURE = 0.30
DEFAULT_MAX_ROTATIONS = 5
DEFAULT_EARNINGS_BLOCK_DAYS = 21
DEFAULT_PREFERRED_CLOSE_MAX_DTE = 60
DEFAULT_BUCKET_WARNING_PCT = 0.20

_MAX_CLOSE_COMBO = 3          # try combining up to 3 closes to fund one open
_MAX_ELIGIBLE_CLOSES = 8      # bound the combinatorics

# Task #21 — near-miss surface. A coverage_floor near-miss must still be
# NEAR: freed ≥ 80% of required, else "loosen the floor" is not a sane
# override and the combo is dropped rather than surfaced as noise.
_NEAR_MISS_MIN_FREED = 0.80
_MAX_NEAR_MISS = 5            # top-N near-misses surfaced

_STRIKE_RE = re.compile(r"\$(\d+(?:\.\d+)?)P\b")

# Same header shape entry_exit_recommender.directive_hold_tickers matches —
# used only to pull the QUOTABLE first line of each hold directive.
_DIRECTIVE_LINE_RE = re.compile(r"^\s*-\s*\*\*([A-Z]{1,6})[\s_(]")


def parse_directive_hold_notes(memory_text: str | None) -> dict[str, str]:
    """ticker → first line of its hold directive (user-editable head of
    fable_advisor_memory.md), for quoting in near-miss block details.

    Mirrors ``entry_exit_recommender.directive_hold_tickers``: only the
    section ABOVE "## Recent reviews" is scanned, and a bullet counts when
    its text signals a hold. Fail-open: unparseable input → {}.
    """
    if not memory_text:
        return {}
    head = str(memory_text).split("## Recent reviews", 1)[0]
    out: dict[str, str] = {}
    for line in head.splitlines():
        m = _DIRECTIVE_LINE_RE.match(line)
        if not m:
            continue
        low = line.lower()
        if any(w in low for w in ("hold", "not ready to sell", "don't",
                                  "do not", "stop")):
            note = line.strip().lstrip("-").strip()
            out.setdefault(m.group(1).upper(), note[:160])
    return out


# ── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class HeldCSP:
    """A held short put, normalized from snapshot positions / options reviews."""
    ticker: str
    strike: float
    expiration: str | None          # ISO YYYY-MM-DD when known
    qty: int                        # contracts (positive count)
    entry_premium: float            # per share
    current_mid: float              # per share
    days_remaining: int
    spot: float | None = None
    symbol: str | None = None       # display ident, e.g. GOOG_PUT_325_20260821
    # derived:
    collateral: float = 0.0
    capture_pct: float = 0.0        # (entry − mid) / entry
    remaining_theta_dollars: float = 0.0   # extrinsic × 100 × qty
    remaining_yield_ann: float = 0.0       # % annualized on collateral
    banked_profit_dollars: float = 0.0     # (entry − mid) × 100 × qty
    unrealized_loss_dollars: float = 0.0   # positive when close realizes a loss

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "strike": self.strike,
            "expiration": self.expiration, "qty": self.qty,
            "entry_premium": self.entry_premium, "current_mid": self.current_mid,
            "days_remaining": self.days_remaining, "symbol": self.symbol,
            "collateral": round(self.collateral, 2),
            "capture_pct": round(self.capture_pct, 4),
            "remaining_theta_dollars": round(self.remaining_theta_dollars, 2),
            "remaining_yield_ann": round(self.remaining_yield_ann, 2),
            "banked_profit_dollars": round(self.banked_profit_dollars, 2),
        }


@dataclass
class CandidateCSP:
    """An open-side CSP candidate, normalized from LT opportunities / ideas."""
    ticker: str
    strike: float
    expiration: str | None
    dte: int
    premium_per_share: float        # measured mid (never estimated)
    collateral: float = 0.0
    yield_ann: float = 0.0          # % annualized on collateral
    rsi: float | None = None
    source_kind: str | None = None  # LONG_DATED_CSP / PULLBACK_CSP / ...
    capacity_deferred: bool = False

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "strike": self.strike,
            "expiration": self.expiration, "dte": self.dte,
            "premium_per_share": self.premium_per_share,
            "premium_dollars": round(self.premium_per_share * 100, 2),
            "collateral": round(self.collateral, 2),
            "yield_ann": round(self.yield_ann, 2),
            "rsi": self.rsi,
            "source_kind": self.source_kind,
            "capacity_deferred": self.capacity_deferred,
        }


@dataclass
class CSPRotation:
    close_positions: list[HeldCSP]
    open_position: CandidateCSP
    freed_collateral: float
    required_collateral: float
    net_collateral_delta: float      # freed − required (≥ 0 = coverage-neutral+)
    close_yield_ann: float           # collateral-weighted remaining yield on closes
    open_yield_ann: float
    yield_delta_pct: float           # open − close, percentage points annualized
    score: float
    freed_bucket_pct: float          # % NLV decompressed from overweight buckets
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "close_positions": [c.to_dict() for c in self.close_positions],
            "open_position": self.open_position.to_dict(),
            "freed_collateral": round(self.freed_collateral, 2),
            "required_collateral": round(self.required_collateral, 2),
            "net_collateral_delta": round(self.net_collateral_delta, 2),
            "close_yield_ann": round(self.close_yield_ann, 2),
            "open_yield_ann": round(self.open_yield_ann, 2),
            "yield_delta_pct": round(self.yield_delta_pct, 2),
            "score": round(self.score, 2),
            "freed_bucket_pct": round(self.freed_bucket_pct, 2),
            "warnings": list(self.warnings),
        }


@dataclass
class NearMissRotation:
    """A rotation that WOULD be recommended if exactly one constraint were
    relaxed. Surfaced (task #21) so the user can decide whether to override —
    never an automatic recommendation, and never counted as qualified."""
    close_positions: list[HeldCSP]
    open_position: CandidateCSP
    freed_collateral: float
    required_collateral: float
    yield_delta_pct: float
    score: float
    block_reason: str          # "coverage_floor" | "directive_hold" | "stacking"
                               # | "overlap" | "earnings" | "bucket_concentration"
    block_detail: str          # human-readable, real measured numbers only
    unblock_path: str          # what would need to change to unlock
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "close_positions": [c.to_dict() for c in self.close_positions],
            "open_position": self.open_position.to_dict(),
            "freed_collateral": round(self.freed_collateral, 2),
            "required_collateral": round(self.required_collateral, 2),
            "yield_delta_pct": round(self.yield_delta_pct, 2),
            "score": round(self.score, 2),
            "block_reason": self.block_reason,
            "block_detail": self.block_detail,
            "unblock_path": self.unblock_path,
            "warnings": list(self.warnings),
        }


class CSPRotationReport(list):
    """Qualified rotations (this IS the list — full backward compatibility
    with every existing caller: iteration, len, indexing, ``== []``,
    isinstance(x, list)) plus the near-miss surface as an attribute.

    Near-misses are additive display only (task #21) — they never change
    what qualifies as a real rotation.
    """

    def __init__(self, qualified: list | None = None,
                 near_miss: list | None = None, max_near_miss: int = 5):
        super().__init__(qualified or [])
        self.near_miss: list[NearMissRotation] = list(near_miss or [])
        self.max_near_miss = max_near_miss

    @property
    def qualified(self) -> list:
        return list(self)

    def to_dict(self) -> dict:
        return {
            "qualified": [r.to_dict() for r in self],
            "near_miss": [n.to_dict() for n in self.near_miss],
        }


# ── Yield math (single source of truth, both sides) ───────────────────────


def remaining_annualized_yield(
    strike: float,
    qty: int,
    current_mid: float,
    days_remaining: int,
    spot: float | None = None,
) -> tuple[float, float]:
    """(annualized_yield_pct, remaining_theta_dollars) for a held short put.

    Only the EXTRINSIC value decays to the holder — an ITM put's intrinsic is
    subtracted when spot is known. Missing spot → full mid counts (overstates
    the held yield, which only makes rotations HARDER to qualify — safe).
    Near-expiry: days floors at 1 so the yield stays finite.
    """
    collateral = strike * 100.0 * qty
    if collateral <= 0:
        return 0.0, 0.0
    intrinsic = max(strike - spot, 0.0) if (spot is not None and spot > 0) else 0.0
    extrinsic = max(float(current_mid) - intrinsic, 0.0)
    theta_dollars = extrinsic * 100.0 * qty
    days = max(int(days_remaining), 1)
    return (theta_dollars * 365.0) / (days * collateral) * 100.0, theta_dollars


def candidate_annualized_yield(
    premium_per_share: float, dte: int, strike: float
) -> float:
    """Annualized yield % for a new CSP: premium × 365 / (dte × collateral)."""
    collateral = strike * 100.0
    if collateral <= 0 or premium_per_share <= 0:
        return 0.0
    days = max(int(dte), 1)
    return (premium_per_share * 100.0 * 365.0) / (days * collateral) * 100.0


# ── Normalization helpers ─────────────────────────────────────────────────


def _f(v, default=None):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out


def _days_from_exp(exp: str | None, today: _date) -> int | None:
    if not exp:
        return None
    try:
        y, m, d = str(exp)[:10].split("-")
        return ( _date(int(y), int(m), int(d)) - today).days
    except (ValueError, TypeError):
        return None


def _normalize_held(raw: dict, today: _date, spots: dict) -> HeldCSP | None:
    """Accept snapshot-position OR options-review shaped dicts.

    Snapshot position: underlying/symbol, strike, expiration, qty (negative),
        costPerShare, currentMid, type == "PUT", assetType == "OPTION".
    Options review: underlying, strike, expiration, qty, entry_price,
        current_mid, days_to_expiry, type == "PUT".
    """
    if not isinstance(raw, dict):
        return None
    opt_type = str(raw.get("type") or "").upper()
    if opt_type and opt_type != "PUT":
        return None
    if raw.get("assetType") and raw.get("assetType") != "OPTION":
        return None
    qty_raw = _f(raw.get("qty"), 0.0) or 0.0
    if qty_raw >= 0:                      # only SHORT puts free collateral
        return None
    qty = int(abs(qty_raw))
    if qty <= 0:
        return None
    ticker = str(raw.get("underlying") or raw.get("ticker") or "").upper()
    if not ticker:
        return None
    strike = _f(raw.get("strike"), 0.0) or 0.0
    if strike <= 0:
        return None
    entry = _f(raw.get("entry_price") or raw.get("costPerShare")
               or raw.get("entry_premium") or raw.get("premiumReceived"), 0.0) or 0.0
    mid = _f(raw.get("current_mid") or raw.get("currentMid")
             or raw.get("current_price"), None)
    if entry <= 0 or mid is None or mid < 0:
        return None                       # no measured prices → no yield math
    exp = raw.get("expiration")
    exp_iso = str(exp)[:10] if exp else None
    days = raw.get("days_to_expiry") or raw.get("dte")
    days = int(days) if isinstance(days, (int, float)) and days else _days_from_exp(exp_iso, today)
    if days is None or days <= 0:
        return None                       # expired / unknown tenor → skip
    spot = _f(raw.get("spot"), None) or spots.get(ticker)

    h = HeldCSP(
        ticker=ticker, strike=strike, expiration=exp_iso, qty=qty,
        entry_premium=entry, current_mid=float(mid), days_remaining=days,
        spot=spot,
        symbol=raw.get("symbol") or raw.get("contract")
               or f"{ticker}_PUT_{strike:g}_{(exp_iso or '').replace('-', '')}",
    )
    h.collateral = strike * 100.0 * qty
    h.capture_pct = (entry - float(mid)) / entry
    h.banked_profit_dollars = (entry - float(mid)) * 100.0 * qty
    h.unrealized_loss_dollars = max(-h.banked_profit_dollars, 0.0)
    h.remaining_yield_ann, h.remaining_theta_dollars = remaining_annualized_yield(
        strike, qty, float(mid), days, spot)
    return h


def _normalize_candidate(raw: dict, today: _date, technicals: dict) -> CandidateCSP | None:
    """Accept LT-opportunity dicts (LONG_DATED_CSP with live_* fields),
    PULLBACK_CSP idea dicts, or plain {ticker, strike, premium, dte} dicts.

    Hard rule #19: a candidate WITHOUT a measured premium is skipped — the
    rotation math never runs on an estimated credit.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or raw.get("source_kind") or "").upper()
    if raw.get("skip_reason") or kind.startswith("SKIPPED"):
        return None
    if kind and kind not in (
        "LONG_DATED_CSP", "LT_CSP", "PULLBACK_CSP", "NEW_CSP", "CSP",
    ) and not (raw.get("strike") and (raw.get("premium") or raw.get("premium_per_share"))):
        return None
    ticker = str(raw.get("ticker") or raw.get("underlying") or "").upper()
    if not ticker or ticker == "CAPACITY":
        return None

    strike = _f(raw.get("live_strike") or raw.get("strike"), None)
    if strike is None:
        m = _STRIKE_RE.search(str(raw.get("concrete_trade") or ""))
        strike = _f(m.group(1), None) if m else None
    if not strike or strike <= 0:
        return None

    premium = _f(raw.get("live_mid") or raw.get("live_premium_per_share")
                 or raw.get("premium_per_share") or raw.get("premium")
                 or raw.get("mid"), None)
    if not premium or premium <= 0:
        return None                       # no measured premium → no candidate

    exp = raw.get("target_expiration") or raw.get("expiration")
    exp_iso = str(exp)[:10] if exp else None
    dte = raw.get("target_dte") or raw.get("dte") or raw.get("days_to_expiry")
    dte = int(dte) if isinstance(dte, (int, float)) and dte else _days_from_exp(exp_iso, today)
    if dte is None or dte <= 0:
        return None

    tech = technicals.get(ticker) if isinstance(technicals, dict) else None
    rsi = tech.get("rsi_14") if isinstance(tech, dict) else None

    c = CandidateCSP(
        ticker=ticker, strike=float(strike), expiration=exp_iso, dte=dte,
        premium_per_share=float(premium),
        rsi=_f(rsi, None),
        source_kind=kind or None,
        capacity_deferred=bool(raw.get("capacity_deferred")),
    )
    c.collateral = float(strike) * 100.0
    c.yield_ann = candidate_annualized_yield(float(premium), dte, float(strike))
    return c


# ── Analytics extraction (all fail-open) ──────────────────────────────────


def _analytics_get(analytics, key, default=None):
    if isinstance(analytics, dict):
        return analytics.get(key, default)
    return default


def _nlv_from(analytics) -> float:
    nlv = _f(_analytics_get(analytics, "nlv"), 0.0) or 0.0
    if nlv <= 0:
        bal = _analytics_get(analytics, "balance") or {}
        if isinstance(bal, dict):
            nlv = _f(bal.get("accountValue"), 0.0) or 0.0
    return nlv


# ── Core ──────────────────────────────────────────────────────────────────


def compute_csp_rotations(
    held_csps: list[dict],
    candidate_csps: list[dict],
    analytics: dict | None,
    config: dict | None,
    *,
    today: _date | None = None,
) -> CSPRotationReport:
    """Propose top-N CSP rotations: close lower-yield held put(s) to fund a
    higher-yield new CSP with the same-or-lower total obligation.

    Returns a ``CSPRotationReport`` — a list subclass whose elements are the
    QUALIFIED rotations (all gates passed; fully backward compatible with
    the task-#20 list return), with the additive ``.near_miss`` attribute
    carrying the top rotations blocked by exactly ONE gate (task #21) so
    the user can decide whether to override a single constraint.

    Args:
        held_csps: held SHORT-put dicts (snapshot positions or options
            reviews — see ``_normalize_held`` for accepted shapes).
        candidate_csps: open-side candidate dicts (LT opportunities,
            PULLBACK CSP ideas — see ``_normalize_candidate``).
        analytics: optional context dict. Recognized keys (all optional):
            nlv, balance, snapshot_data (for the LT-verdict gate),
            technicals, earnings_calendar ({tk: "YYYY-MM-DD"}),
            recs_map ({tk: parkev rec dict}), directive_holds (set of
            tickers with a standing hold from fable_advisor_memory.md).
        config: briefing config (reads the ``csp_rotation`` block).
        today: injection point for deterministic tests; default date.today().

    Fail-open: ANY exception returns an empty report (``== []``) — this step
    never blocks the briefing.
    """
    try:
        return _compute(held_csps, candidate_csps, analytics, config,
                        today=today or _date.today())
    except Exception as e:  # noqa: BLE001 — hard fail-open per user constraint
        print(f"[csp-rotation] compute failed (non-fatal): {e}", file=sys.stderr)
        return CSPRotationReport()


def _compute(
    held_csps: list[dict],
    candidate_csps: list[dict],
    analytics: dict | None,
    config: dict | None,
    *,
    today: _date,
) -> CSPRotationReport:
    cfg = (config or {}).get("csp_rotation", {}) if isinstance(config, dict) else {}
    min_yield_delta = _f(cfg.get("min_yield_delta"), DEFAULT_MIN_YIELD_DELTA)
    min_freed_ratio = _f(cfg.get("min_freed_ratio"), DEFAULT_MIN_FREED_RATIO)
    min_capture = _f(cfg.get("min_close_capture"), DEFAULT_MIN_CLOSE_CAPTURE)
    max_rotations = int(_f(cfg.get("max_rotations"), DEFAULT_MAX_ROTATIONS))
    earnings_block_days = int(_f(cfg.get("earnings_block_days"),
                                 DEFAULT_EARNINGS_BLOCK_DAYS))
    preferred_max_dte = int(_f(cfg.get("preferred_close_max_dte"),
                               DEFAULT_PREFERRED_CLOSE_MAX_DTE))
    bucket_warning_pct = _f(cfg.get("bucket_warning_pct"),
                            DEFAULT_BUCKET_WARNING_PCT)

    technicals = _analytics_get(analytics, "technicals") or {}
    snapshot_data = _analytics_get(analytics, "snapshot_data")
    if not isinstance(snapshot_data, dict):
        snapshot_data = {"technicals": technicals} if technicals else {}
    elif not technicals:
        technicals = snapshot_data.get("technicals") or {}
    earnings_cal = _analytics_get(analytics, "earnings_calendar") or {}
    recs_map = _analytics_get(analytics, "recs_map") or {}
    directive_holds = {str(t).upper()
                      for t in (_analytics_get(analytics, "directive_holds") or set())}
    directive_notes = _analytics_get(analytics, "directive_hold_notes") or {}
    if not isinstance(directive_notes, dict):
        directive_notes = {}
    nlv = _nlv_from(analytics)

    # Spots for intrinsic math, pulled from technicals (fail-open).
    spots: dict[str, float] = {}
    if isinstance(technicals, dict):
        for tk, tech in technicals.items():
            if isinstance(tech, dict):
                s = _f(tech.get("spot"), None)
                if s:
                    spots[str(tk).upper()] = s

    # ── Normalize both sides ─────────────────────────────────────────────
    held: list[HeldCSP] = []
    for raw in held_csps or []:
        h = _normalize_held(raw, today, spots)
        if h is not None:
            held.append(h)
    if not held:
        return CSPRotationReport()

    candidates: list[CandidateCSP] = []
    for raw in candidate_csps or []:
        c = _normalize_candidate(raw, today, technicals)
        if c is not None:
            candidates.append(c)
    if not candidates:
        return CSPRotationReport()

    # All held short-put strikes by ticker (rule #40 overlap universe).
    held_strikes_by_ticker: dict[str, list[float]] = {}
    for h in held:
        held_strikes_by_ticker.setdefault(h.ticker, []).append(h.strike)

    # ── Open-side discipline filters ─────────────────────────────────────
    # open_side feeds the qualified path (unchanged from task #20).
    # near_open additionally carries candidates rejected by AT MOST one
    # user-overridable gate (task #21) — each block is (reason, detail,
    # unblock) so the near-miss surface can say exactly what to revisit.
    open_side: list[tuple[CandidateCSP, list[str]]] = []
    near_open: list[tuple[CandidateCSP, list[str], list[tuple[str, str, str]]]] = []
    try:
        from analysis.put_overlap_check import check_strike_overlap
    except ImportError:
        check_strike_overlap = None
    try:
        from analysis.lt_verdict_gate import check_lt_verdict_gate
    except ImportError:
        check_lt_verdict_gate = None

    for c in candidates:
        cand_warnings: list[str] = []
        cand_blocks: list[tuple[str, str, str]] = []
        # Rule #39: never rotate INTO a broken/downtrending chart. HARD skip —
        # a broken chart is not a "revisit one constraint" override, so it
        # never surfaces as a near-miss either.
        if check_lt_verdict_gate is not None and snapshot_data:
            g = check_lt_verdict_gate(c.ticker, snapshot_data,
                                      recs_map.get(c.ticker))
            if not g.get("pass", True):
                continue
            if g.get("warning"):
                cand_warnings.append(f"⚠ {g['warning']}")
        # Rule #40: never rotate INTO a strike within 5% of a held put.
        if check_strike_overlap is not None:
            ov = check_strike_overlap(
                c.ticker, c.strike, held_strikes_by_ticker.get(c.ticker) or [])
            if ov.get("overlap"):
                ex = ov.get("existing_strike")
                dist = ov.get("distance_pct")
                cand_blocks.append((
                    "overlap",
                    f"Proposed {c.ticker} ${c.strike:g}P is within 5% of the "
                    f"existing ${ex:g}P strike on the same underlying"
                    + (f" ({dist * 100:.1f}% apart)" if isinstance(dist, (int, float)) else "")
                    + " — treated as concentration by rule #40.",
                    "Pick a strike further apart or a different underlying.",
                ))
        # Earnings inside the block window → reject; just outside → warn.
        edays = _days_from_exp(
            str(earnings_cal.get(c.ticker) or "")[:10] or None, today)
        if edays is not None and 0 <= edays <= earnings_block_days:
            cand_blocks.append((
                "earnings",
                f"{c.ticker} earnings in {edays}d — inside the "
                f"{earnings_block_days}d earnings block window.",
                "Wait until after earnings, or accept the binary risk.",
            ))
        if edays is not None and earnings_block_days < edays <= earnings_block_days + 14:
            cand_warnings.append(
                f"earnings in {edays}d (just outside the {earnings_block_days}d block)")
        # Stacking note (different strike on a name with an existing put).
        if held_strikes_by_ticker.get(c.ticker):
            strikes = ", ".join(f"${s:g}" for s in sorted(held_strikes_by_ticker[c.ticker]))
            cand_warnings.append(
                f"already short {c.ticker} put(s) at {strikes} — this stacks single-name risk")
        if not cand_blocks:
            open_side.append((c, cand_warnings))
        if len(cand_blocks) <= 1:
            near_open.append((c, cand_warnings, cand_blocks))

    # ── Close-side discipline filters ────────────────────────────────────
    eligible_closes: list[HeldCSP] = []
    directive_closes: list[HeldCSP] = []   # task #21: near-miss close pool
    for h in held:
        if h.ticker in directive_holds:
            # Standing user directive to hold — excluded from the qualified
            # path, but a directive-held position that OTHERWISE clears the
            # capture floor can still fund a near-miss (the user may choose
            # to revisit the directive).
            if h.capture_pct >= min_capture:
                directive_closes.append(h)
            continue
        if h.capture_pct < min_capture:
            continue                      # would realize a loss / thin profit
        eligible_closes.append(h)
    # Prefer lowest remaining yield first, then short-tail (< preferred DTE).
    _close_sort_key = lambda h: (h.remaining_yield_ann,            # noqa: E731
                                 h.days_remaining >= preferred_max_dte,
                                 -h.collateral)
    eligible_closes.sort(key=_close_sort_key)
    eligible_closes = eligible_closes[:_MAX_ELIGIBLE_CLOSES]
    directive_closes.sort(key=_close_sort_key)
    directive_closes = directive_closes[:_MAX_ELIGIBLE_CLOSES]

    # ── Expiration buckets (rule #21 decompression bonus) ────────────────
    bucket_collateral: dict[str, float] = {}
    for h in held:
        if h.expiration:
            bucket_collateral[h.expiration] = (
                bucket_collateral.get(h.expiration, 0.0) + h.collateral)
    overweight_buckets = {
        exp for exp, coll in bucket_collateral.items()
        if nlv > 0 and coll / nlv >= bucket_warning_pct
    }

    # ── Combo search: every valid (candidate, close-set) pairing ─────────
    rotations: list[tuple[tuple, CSPRotation]] = []
    for cand, cand_warnings in open_side:
        for r in range(1, min(_MAX_CLOSE_COMBO, len(eligible_closes)) + 1):
            for combo in combinations(eligible_closes, r):
                freed = sum(h.collateral for h in combo)
                if freed < cand.collateral * min_freed_ratio:
                    continue
                # Collateral-weighted remaining yield on the closes.
                close_yield = (sum(h.remaining_yield_ann * h.collateral for h in combo)
                               / freed) if freed > 0 else 0.0
                if close_yield > 0 and cand.yield_ann < min_yield_delta * close_yield:
                    continue
                yield_delta = cand.yield_ann - close_yield
                if yield_delta <= 0:
                    continue
                # Bucket decompression is NET per overweight bucket: closes in
                # the bucket free it, but an open landing in the SAME bucket
                # re-compresses it — never claim a decompression the rotation
                # itself undoes (floor at 0 per bucket).
                freed_bucket = 0.0
                for bexp in overweight_buckets:
                    freed_in = sum(h.collateral for h in combo
                                   if h.expiration == bexp)
                    if cand.expiration == bexp:
                        freed_in -= cand.collateral
                    freed_bucket += max(freed_in, 0.0)
                freed_bucket_pct = (freed_bucket / nlv * 100.0) if nlv > 0 else 0.0
                loss = sum(h.unrealized_loss_dollars for h in combo)
                score = ((yield_delta * cand.collateral) / 100.0
                         + 0.5 * freed_bucket_pct
                         - 0.3 * loss)
                warnings = list(cand_warnings)
                banked = sum(h.banked_profit_dollars for h in combo)
                if banked > 0:
                    warnings.append(
                        f"closing realizes ~${banked:,.0f} of short-term gains (taxable)")
                net = freed - cand.collateral
                if net < 0:
                    warnings.append(
                        f"obligation increases ${-net:,.0f} "
                        f"(within the {100 - min_freed_ratio * 100:.0f}% allowance)")
                # Rank: score desc, then fewer closes, then least excess freed.
                key = (-score, len(combo), freed - cand.collateral)
                rotations.append((key, CSPRotation(
                    close_positions=list(combo),
                    open_position=cand,
                    freed_collateral=freed,
                    required_collateral=cand.collateral,
                    net_collateral_delta=net,
                    close_yield_ann=close_yield,
                    open_yield_ann=cand.yield_ann,
                    yield_delta_pct=yield_delta,
                    score=score,
                    freed_bucket_pct=freed_bucket_pct,
                    warnings=warnings,
                )))

    # ── Rank + de-conflict — one surfaced rotation per candidate, and a
    # close can only fund ONE surfaced rotation (the whole list stays
    # simultaneously executable). Greedy over ALL valid combos means a
    # candidate whose best close-set is claimed by a better swap falls back
    # to its next-best non-conflicting combo instead of vanishing.
    rotations.sort(key=lambda kv: kv[0])
    surfaced: list[CSPRotation] = []
    claimed: set[str] = set()
    used_candidates: set[tuple] = set()
    for _key, rot in rotations:
        cand_id = (rot.open_position.ticker, rot.open_position.strike,
                   rot.open_position.expiration)
        if cand_id in used_candidates:
            continue
        idents = {c.symbol or f"{c.ticker}_{c.strike}_{c.expiration}"
                  for c in rot.close_positions}
        if idents & claimed:
            continue                      # those closes already fund a better swap
        surfaced.append(rot)
        claimed |= idents
        used_candidates.add(cand_id)
        if len(surfaced) >= max_rotations:
            break

    # ── Task #21: near-miss surface — rotations blocked by exactly ONE
    # gate, so the user can decide whether to override that constraint.
    # Purely additive display: nothing here changes what qualifies above.
    near_pool = eligible_closes + directive_closes
    near_misses: list[NearMissRotation] = []
    if near_open and near_pool:
        qualified_cands = {
            (r.open_position.ticker, r.open_position.strike,
             r.open_position.expiration) for r in surfaced}
        best_by_cand: dict[tuple, NearMissRotation] = {}
        for cand, cand_warnings, cand_blocks in near_open:
            cid = (cand.ticker, cand.strike, cand.expiration)
            if cid in qualified_cands:
                continue                  # its best outcome already surfaced
            for r in range(1, min(_MAX_CLOSE_COMBO, len(near_pool)) + 1):
                for combo in combinations(near_pool, r):
                    blocks = list(cand_blocks)
                    for h in combo:
                        if h.ticker in directive_holds:
                            sym = h.symbol or f"{h.ticker} ${h.strike:g}P"
                            detail = (f"Position {sym} is held per your "
                                      f"directive in fable_advisor_memory.md")
                            note = directive_notes.get(h.ticker)
                            if note:
                                detail += f' ("{note}")'
                            blocks.append((
                                "directive_hold", detail + ".",
                                f"Revisit or amend the {h.ticker} hold "
                                f"directive to unlock this rotation."))
                    if len(blocks) > 1:
                        continue          # ≥2 gates → not one-override-away
                    freed = sum(h.collateral for h in combo)
                    if freed < cand.collateral * _NEAR_MISS_MIN_FREED:
                        continue          # not close enough to call "near"
                    if freed < cand.collateral * min_freed_ratio:
                        ratio = freed / cand.collateral
                        blocks.append((
                            "coverage_floor",
                            f"Freed ${freed:,.0f} vs required "
                            f"${cand.collateral:,.0f} ({ratio * 100:.0f}% < "
                            f"{min_freed_ratio * 100:.0f}% floor).",
                            f"Loosen csp_rotation.min_freed_ratio from "
                            f"{min_freed_ratio:.2f} to {ratio:.2f} to unlock, "
                            f"OR wait for stress coverage to clear 0.35× so "
                            f"the floor becomes safer to lower."))
                    if len(blocks) != 1:
                        continue          # 0 blocks → the qualified path owns it
                    # The swap must still make economic sense — failing the
                    # yield bar is "not a good trade", not a near-miss.
                    close_yield = (sum(h.remaining_yield_ann * h.collateral
                                       for h in combo) / freed) if freed > 0 else 0.0
                    if close_yield > 0 and cand.yield_ann < min_yield_delta * close_yield:
                        continue
                    yield_delta = cand.yield_ann - close_yield
                    if yield_delta <= 0:
                        continue
                    warnings = list(cand_warnings)
                    banked = sum(h.banked_profit_dollars for h in combo)
                    if banked > 0:
                        warnings.append(
                            f"closing realizes ~${banked:,.0f} of short-term "
                            f"gains (taxable)")
                    net = freed - cand.collateral
                    if net < 0:
                        warnings.append(f"obligation increases ${-net:,.0f}")
                    reason, detail, unblock = blocks[0]
                    nm = NearMissRotation(
                        close_positions=list(combo), open_position=cand,
                        freed_collateral=freed,
                        required_collateral=cand.collateral,
                        yield_delta_pct=yield_delta,
                        score=yield_delta * cand.collateral / 100.0,
                        block_reason=reason, block_detail=detail,
                        unblock_path=unblock, warnings=warnings)
                    prev = best_by_cand.get(cid)
                    if prev is None or nm.score > prev.score or (
                            nm.score == prev.score
                            and len(nm.close_positions) < len(prev.close_positions)):
                        best_by_cand[cid] = nm
        near_misses = sorted(best_by_cand.values(),
                             key=lambda n: -n.score)[:_MAX_NEAR_MISS]

    return CSPRotationReport(surfaced, near_misses, max_near_miss=_MAX_NEAR_MISS)
