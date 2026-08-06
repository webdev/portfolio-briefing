"""Actionable Rotation Playbook (task #22) — the whole composed trade.

Origin: user 2026-07-07. After a manual "Scenario A" walk-through (close 4
winner CSPs → free ~$86K → redeploy into 4 conviction-ranked deferred CSP
candidates with live E*TRADE limits), the user asked for the pattern in the
daily briefing every day: "here's the whole composed trade if you want to
sweep the winners."

Distinct from task #20 (analysis/csp_rotation.py):
  - #20 is a strict 1:1 / n:1 rotation, coverage-neutral (freed ≥ 95% of
    required). #22 composes ONE portfolio-level playbook: ALL freeable
    winner closes on one side, a conviction-ranked greedy deployment of the
    freed collateral on the other. Partial deployment is fine (target ~90%,
    cushion kept), and candidates rank by Parkev conviction × freshness —
    not just yield.

Discipline reused from #20 (all fail-open — a missing input can only make
the playbook SMALLER, never wrong; any exception returns None):
  #39 lt_verdict_gate    — never open into a broken/downtrending chart
  #40 put_overlap_check  — never open a strike within 5% of a held put
  earnings ≤ expiration  — never open across a print
  directive holds        — close-side skips fable_advisor_memory hold names
  capture ≥ 30%          — close-side only sweeps banked winners
  #19 no fabricated data — candidates without a measured mid are skipped
NOT reused: the coverage-neutral freed ≥ 95% floor (this is a sweep, not a
swap — the cushion is the point).

Conviction score (mirrors the manual Scenario A ranking):
  score = parkev_tier × conviction_weight × freshness_multiplier
    conviction_weight:    High 3 · Medium 2 · Low 1 · None 0.5
    freshness_multiplier (task #32 — smooth decay, config-overridable):
      ≤7d → 2.5 · ≤14d → 2.0 · ≤21d → 1.5 · ≤30d → 1.0 · ≤60d → 0.5 ·
      >60d → 0.25 · age unknown (no rec) → NOT applied (tier × weight only)
  + 2  pullback-zone setup (verdict says pullback, or RSI in 35-50 band)
  + 2  RSI < 35 (deep-oversold override)
  + 1  IV rank ≥ 85 (rich premium)
  − 3  LT verdict broken/downtrend (when not already hard-excluded)
  − 3  stacks with an existing short put on the same underlying
  − 5  earnings inside the expiry window (informational — such candidates
       are hard-excluded from the opens anyway)
  − 1/−3  equity-stacking penalty (task #30): held equity 2-5% NLV → −1,
       5-10% → −3, ≥10% → HARD SKIP (surfaced in the warnings footer,
       never silent; per-ticker force_include kill switch downgrades to −3)
  − 2  support-quality penalty (task #31): strike anchored only to a
       1-touch cluster, or floating > 5% from any support cluster; strikes
       at/below a ≥2-touch cluster carry no penalty (below-support is
       protective). Missing S/R data → no penalty (fail-open).

Config (briefing.yaml → rotation_playbook):
  enabled                   (default true)
  min_freed_collateral_usd  (default 30_000)  skip playbook below this
  max_opens                 (default 6)
  deploy_target_pct         (default 0.90)    stop deploying at 90% of freed
  min_conviction_score      (default 6)       skip candidates below this
  diversify_max_same_ticker (default 1)       max opens per underlying
  min_close_capture         (default 0.30)    close-side capture floor
  playbook_min_annualized_yield        (default 0.12)   rule #43 gate battery:
      exclude candidates delivering less than this annualized yield
  playbook_min_premium_pct_of_collateral (default 0.005)  companion floor —
      premium below this fraction of collateral is excluded too
  bucket_warning_pct        (default 0.40)    single-expiration share of
                                              deployed collateral that warns
  allow_stacking_with_held  (default false)   override the stacking filter
  lt_override_fresh_high_conviction (default true)
      Rule #39 playbook exception: a FRESH (≤14d) Parkev BUY·High may
      override the LT-verdict gate — admitted with the −3 penalty and a
      visible warning, never silently.
  lt_override_medium_conviction (default true)   task #27 widening of the
      rule #39 exception: fresh BUY-variant + (High·tier ≥3) OR
      (Medium·tier ≥3 + drawdown ≥30%) overrides — same −3 penalty, same
      visible warning. false = revert to the strict High-only exception.
  treat_warn_as_annotation (default true)        task #27: pre-trade
      validator WARN findings annotate the candidate (rendered as ⚠ notes)
      instead of being dropped silently; BLOCK findings still exclude.
  adaptive_deploy_bands (default unset = flat)   task #28: coverage-adaptive
      deployment cap. A list of {below, cap_pct, label} bands resolved
      against the PROJECTED post-Phase-1 coverage ratio (the freed cash is
      the whole point — at 0.06× coverage the correct behavior is
      closes-heavy, opens-light). When set, the resolved cap OVERRIDES the
      flat deploy_target_pct and acts as a HARD ceiling on deployed
      collateral (never exceeded by a single large candidate). When unset /
      null, or when no coverage is measurable (fail-open), the legacy flat
      deploy_target_pct soft-stop behavior is preserved byte-identically.
  bucket_diversification (task #29):
      max_bucket_pct_of_deploy (default 0.60)  warn when a single Friday
          holds more than this share of newly deployed collateral
      tie_break_prefer_diverse (default true)  among tied-score candidates,
          prefer the one whose expiration bucket has less deployment
      hard_cap_pct (default null = off)        optional HARD per-bucket cap,
          measured against the deployment budget (effective cap × freed);
          a candidate that would push its bucket past it is skipped
  equity_stacking (task #30):
      modest_band_pct / concerning_band_pct / hard_skip_pct — NLV-fraction
      bands for held equity in the candidate's underlying; modest_penalty /
      concerning_penalty — score deductions; force_include — per-ticker
      kill switch that downgrades the ≥10% hard-skip to the −3 penalty
  support_quality (task #31):
      min_touches_for_strong / at_support_band_pct / below_support_band_pct
      / single_touch_penalty / floats_penalty — see
      _evaluate_strike_support_quality
  freshness_bands (task #32):
      [{max_days, multiplier}, ...] — smooth freshness decay for the
      conviction score; max_days null = catch-all. Unset → built-in
      DEFAULT_FRESHNESS_BANDS.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date as _date

from analysis.csp_rotation import (
    _analytics_get,
    _days_from_exp,
    _f,
    _normalize_candidate,
    _normalize_held,
    _nlv_from,
    candidate_annualized_yield,
)

# ── Defaults (config-overridable) ─────────────────────────────────────────
DEFAULT_MIN_FREED_USD = 30_000.0
DEFAULT_MAX_OPENS = 6
DEFAULT_DEPLOY_TARGET_PCT = 0.90
DEFAULT_MIN_CONVICTION_SCORE = 6.0
DEFAULT_DIVERSIFY_MAX_SAME_TICKER = 1
DEFAULT_MIN_CLOSE_CAPTURE = 0.30
DEFAULT_BUCKET_WARNING_PCT = 0.40
# Task #29 — share of newly deployed collateral in a single expiration
# Friday above which the diversification warning fires.
DEFAULT_MAX_BUCKET_PCT_OF_DEPLOY = 0.60

# Same-ticker second entry must beat the best different-ticker alternative
# by this factor to justify stacking opens on one underlying.
_SAME_TICKER_BEAT_FACTOR = 1.25
# A put mid ≥ this fraction of strike is suspiciously rich — likely ITM or a
# stale quote. Surface a verify warning (never silently trust it).
_RICH_MID_VS_STRIKE = 0.08

_CONVICTION_WEIGHT = {"High": 3.0, "Medium": 2.0, "Low": 1.0}

# Task #32 — smooth freshness decay: (max_age_days, multiplier) pairs,
# evaluated in order; None = catch-all. Config-overridable via
# rotation_playbook.freshness_bands ([{max_days, multiplier}, ...]).
DEFAULT_FRESHNESS_BANDS: tuple = (
    (7, 2.5), (14, 2.0), (21, 1.5), (30, 1.0), (60, 0.5), (None, 0.25))

# Task #30 — equity-stacking bands (fractions of NLV held in the
# candidate's underlying EQUITY). Config: rotation_playbook.equity_stacking.
# Canonical values live in analysis/equity_stacking.py (rule #43 move);
# aliased here so the playbook's documented defaults can't drift.
from analysis.equity_stacking import (  # noqa: E402
    DEFAULT_CONCERNING_BAND as DEFAULT_EQ_CONCERNING_BAND,
    DEFAULT_CONCERNING_PENALTY as DEFAULT_EQ_CONCERNING_PENALTY,
    DEFAULT_HARD_SKIP_PCT as DEFAULT_EQ_HARD_SKIP_PCT,
    DEFAULT_MODEST_BAND as DEFAULT_EQ_MODEST_BAND,
    DEFAULT_MODEST_PENALTY as DEFAULT_EQ_MODEST_PENALTY,
)

# Rule #43 (RDDT 2026-07-31) — conviction penalty when a single-stock
# candidate's earnings date is UNKNOWN (calendar + fallback both empty).
# Config: rotation_playbook.earnings_unknown_penalty. Demotion + loud
# annotation, never exclusion (data absence ≠ evidence of earnings).
DEFAULT_EARNINGS_UNKNOWN_PENALTY = 2.0

# Task #31 — support-quality gate defaults. Config:
# rotation_playbook.support_quality.
DEFAULT_SQ_MIN_TOUCHES = 2
DEFAULT_SQ_AT_BAND_PCT = 0.05       # ±5% of strike counts as "at" a cluster
DEFAULT_SQ_BELOW_BAND_PCT = 0.15    # strike ≤15% under a cluster = protected
DEFAULT_SQ_SINGLE_TOUCH_PENALTY = 2.0
DEFAULT_SQ_FLOATS_PENALTY = 2.0

# Bug #23 — validator rule ids that describe PORTFOLIO STATE (true pre-close,
# false post-Phase-1). Candidates blocked upstream ONLY by these gates are
# re-admitted into the playbook pool and re-validated against the PROJECTED
# post-close state. Position-shape gates (earnings, overlap, RSI, ...) are
# never in this set — they don't change when cash frees up.
_PORTFOLIO_STATE_RULES = frozenset({
    "CASH_FLOOR", "ENTRY_GATES_CLOSED",
    "EXPIRATION_BUCKET_CRITICAL", "EXPIRATION_BUCKET_WARNING",
})

_UNLOCK_TAG = "🔓 unlocks after Phase 1"


def _readmit_portfolio_blocked(raw) -> dict | None:
    """A candidate skipped upstream ONLY by portfolio-state validator gates
    (CASH_FLOOR / ENTRY_GATES_CLOSED / EXPIRATION_BUCKET_*) gets a second
    look in the playbook — those gates were measured PRE-close and the
    playbook's whole premise is "here's the trade AFTER you sweep the
    winners". Returns a copy with the skip lifted, or None when the skip
    was (also) a position-shape gate — those stay skipped."""
    if not isinstance(raw, dict):
        return None
    sr = str(raw.get("skip_reason") or "")
    if not sr.startswith("pre-trade validator BLOCK:"):
        return None
    rules = {str(f.get("rule_id")) for f in raw.get("validator_findings") or []
             if isinstance(f, dict) and f.get("severity") == "BLOCK"}
    if not rules:
        rules = {t.strip() for t in sr.split(":", 1)[1].split(";") if t.strip()}
    if not rules or not rules.issubset(_PORTFOLIO_STATE_RULES):
        return None
    c = dict(raw)
    c.pop("skip_reason", None)
    if c.get("kind_when_skipped"):
        c["kind"] = c["kind_when_skipped"]
    c["_preclose_blocked_rules"] = sorted(rules)
    return c


def _cash_from(analytics) -> float | None:
    """Measured cash from the stress-coverage payload or snapshot balance.
    None when unavailable — projections are then skipped, never fabricated."""
    sc = _analytics_get(analytics, "stress_coverage")
    if sc is not None:
        val = getattr(sc, "cash", None)
        if val is None and isinstance(sc, dict):
            val = sc.get("cash")
        out = _f(val, None)
        if out is not None:
            return out
    snap = _analytics_get(analytics, "snapshot_data")
    if isinstance(snap, dict):
        bal = snap.get("balance") or {}
        if isinstance(bal, dict):
            return _f(bal.get("cash") or bal.get("cashBalance"), None)
    return _f(_analytics_get(analytics, "cash"), None)


def _fmt_exp_compact(exp_iso) -> str:
    """"2026-08-28" → "Aug 28 '26" (warning text). Raw string when
    unparseable — never fabricated."""
    try:
        y, m, d = str(exp_iso)[:10].split("-")
        return _date(int(y), int(m), int(d)).strftime("%b %d '%y")
    except (ValueError, TypeError):
        return str(exp_iso)


def _resolve_deploy_cap(
    coverage_ratio: float | None,
    cfg: dict,
    flat_target: float,
) -> tuple[float, str, str, bool]:
    """Task #28 — resolve the deployment cap from the coverage-adaptive
    bands. Returns (cap_pct, band_label, next_band_note, adaptive_active).

    ``coverage_ratio`` is the PROJECTED post-Phase-1 coverage (falling back
    to the measured pre-close ratio when the projection is uncomputable).
    Fail-open on every path: bands unset/null/malformed, or no measurable
    coverage → the legacy flat ``deploy_target_pct`` with adaptive=False.
    The cap is a ceiling, not a floor — selection deploys less whenever the
    natural top-N picks come in under it.
    """
    bands_raw = cfg.get("adaptive_deploy_bands")
    if not isinstance(bands_raw, list) or not bands_raw:
        return flat_target, "", "", False
    if coverage_ratio is None:
        return flat_target, "", "", False    # no measured coverage → flat
    bands: list[tuple[float | None, float, str]] = []
    for b in bands_raw:
        if not isinstance(b, dict):
            continue
        cap = _f(b.get("cap_pct"), None)
        if cap is None:
            continue
        below = _f(b.get("below"), None)     # None = catch-all top band
        bands.append((below, cap, str(b.get("label") or "")))
    if not bands:
        return flat_target, "", "", False
    for i, (below, cap, label) in enumerate(bands):
        if below is None or float(coverage_ratio) < below:
            next_note = ""
            if below is not None and i + 1 < len(bands):
                next_note = (f"Once coverage clears {below:.2f}× the cap "
                             f"rises to {bands[i + 1][1] * 100:.0f}%.")
            return cap, label, next_note, True
    # Coverage above every bounded band and no catch-all → last band's cap.
    below, cap, label = bands[-1]
    return cap, label, "", True


# ── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class FreeableClose:
    """A held CSP eligible for winner-close (≥30% capture, not directive-held)."""
    ticker: str
    contract: str                    # e.g. "GOOG_PUT_325_20260821"
    strike: float
    expiration: str                  # ISO date
    qty: int                         # positive (position count)
    capture_pct: float
    remaining_extrinsic: float       # what you're leaving on the table ($)
    freed_collateral: float          # strike × 100 × qty
    realized_profit: float           # what you bank if you close
    buy_to_close_mid: float
    dte_remaining: int
    directive_held: bool             # if True, excluded from playbook

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "contract": self.contract,
            "strike": self.strike, "expiration": self.expiration,
            "qty": self.qty,
            "capture_pct": round(self.capture_pct, 4),
            "remaining_extrinsic": round(self.remaining_extrinsic, 2),
            "freed_collateral": round(self.freed_collateral, 2),
            "realized_profit": round(self.realized_profit, 2),
            "buy_to_close_mid": round(self.buy_to_close_mid, 4),
            "dte_remaining": self.dte_remaining,
            "directive_held": self.directive_held,
        }


@dataclass
class ConvictionScoredCandidate:
    """A deferred CSP candidate enriched with Parkev conviction score."""
    ticker: str
    strike: float
    expiration: str
    dte: int
    collateral_required: float
    premium: float                   # dollars per contract (mid × 100)
    annualized_yield_pct: float
    mid_price: float                 # per share
    parkev_rating: str | None        # BUY/HOLD/SELL or None
    parkev_tier: int                 # 0-5
    parkev_conviction: str | None    # High/Medium/Low
    parkev_age_days: int | None
    conviction_score: float
    setup_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lt_verdict: str | None = None
    has_earnings_in_window: bool = False
    # Rule #43 (RDDT): True when NO earnings date could be measured for a
    # single-stock underlying — penalized + "⚠ earnings unverified" flagged.
    earnings_unknown: bool = False
    stacks_with_held: bool = False
    rsi: float | None = None
    # Rule #44 (INTC 2026-08-05): True when the RSI the gate battery judged
    # was VERIFIED against a live quote this cycle (fresh vintage or live
    # recompute); False = snapshot value. The conviction cell renders
    # "RSI NN✓" vs "RSI NN (snapshot)" from this — never an unlabeled value.
    rsi_verified: bool = False
    # Rule #38 (INTC): a non-BUY-rated candidate that qualified via the full
    # independent-setup bar carries the precise badge here (rendered in the
    # conviction cell instead of the bare "Parkev HOLD" segment).
    independent_setup_badge: str | None = None
    # Task #30/#31 — measured gate context (None/"unknown" when unmeasured).
    equity_held_pct: float | None = None
    support_quality: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "strike": self.strike,
            "expiration": self.expiration, "dte": self.dte,
            "collateral_required": round(self.collateral_required, 2),
            "premium": round(self.premium, 2),
            "annualized_yield_pct": round(self.annualized_yield_pct, 2),
            "mid_price": round(self.mid_price, 4),
            "parkev_rating": self.parkev_rating,
            "parkev_tier": self.parkev_tier,
            "parkev_conviction": self.parkev_conviction,
            "parkev_age_days": self.parkev_age_days,
            "conviction_score": round(self.conviction_score, 2),
            "setup_flags": list(self.setup_flags),
            "warnings": list(self.warnings),
            "lt_verdict": self.lt_verdict,
            "has_earnings_in_window": self.has_earnings_in_window,
            "earnings_unknown": self.earnings_unknown,
            "stacks_with_held": self.stacks_with_held,
            "rsi": self.rsi,
            "rsi_verified": self.rsi_verified,
            "independent_setup_badge": self.independent_setup_badge,
            "equity_held_pct": (round(self.equity_held_pct, 4)
                                if self.equity_held_pct is not None else None),
            "support_quality": self.support_quality,
        }


@dataclass
class RotationPlaybook:
    """The composed playbook — Phase 1 closes + Phase 2 opens."""
    closes: list[FreeableClose]
    opens: list[ConvictionScoredCandidate]
    total_freed: float
    total_collateral_deployed: float
    cash_cushion_kept: float
    total_premium_collected: float
    total_realized_profit: float
    weighted_yield_pct: float
    bucket_concentrations: dict[str, float] = field(default_factory=dict)
    coverage_before: float | None = None
    coverage_after: float | None = None
    warnings: list[str] = field(default_factory=list)
    # Task #28 — coverage-adaptive deployment cap. None/"" when the flat
    # legacy deploy_target_pct is in effect (bands unset or no coverage).
    deploy_cap_pct: float | None = None
    deploy_cap_label: str = ""
    deploy_cap_coverage: float | None = None   # projected ratio the cap used
    deploy_cap_next_note: str = ""             # "Once coverage clears X× ..."

    def to_dict(self) -> dict:
        return {
            "closes": [c.to_dict() for c in self.closes],
            "opens": [o.to_dict() for o in self.opens],
            "total_freed": round(self.total_freed, 2),
            "total_collateral_deployed": round(self.total_collateral_deployed, 2),
            "cash_cushion_kept": round(self.cash_cushion_kept, 2),
            "total_premium_collected": round(self.total_premium_collected, 2),
            "total_realized_profit": round(self.total_realized_profit, 2),
            "weighted_yield_pct": round(self.weighted_yield_pct, 2),
            "bucket_concentrations": {
                k: round(v, 2) for k, v in self.bucket_concentrations.items()},
            "coverage_before": self.coverage_before,
            "coverage_after": self.coverage_after,
            "warnings": list(self.warnings),
            "deploy_cap_pct": self.deploy_cap_pct,
            "deploy_cap_label": self.deploy_cap_label,
            "deploy_cap_coverage": self.deploy_cap_coverage,
            "deploy_cap_next_note": self.deploy_cap_next_note,
        }


# ── Conviction score (single source of truth) ─────────────────────────────


def _parse_freshness_bands(raw) -> tuple:
    """Task #32 — config freshness_bands ([{max_days, multiplier}, ...]) →
    ((max_days, multiplier), ...) sorted with the None catch-all last.
    Fail-open: malformed / empty → the built-in DEFAULT_FRESHNESS_BANDS."""
    if not isinstance(raw, list) or not raw:
        return DEFAULT_FRESHNESS_BANDS
    bounded: list[tuple[float, float]] = []
    catch_all: float | None = None
    for b in raw:
        if not isinstance(b, dict):
            continue
        mult = _f(b.get("multiplier"), None)
        if mult is None:
            continue
        max_days = _f(b.get("max_days"), None)
        if max_days is None:
            catch_all = mult
        else:
            bounded.append((max_days, mult))
    if not bounded and catch_all is None:
        return DEFAULT_FRESHNESS_BANDS
    bounded.sort(key=lambda x: x[0])
    out: list[tuple] = list(bounded)
    if catch_all is not None:
        out.append((None, catch_all))
    return tuple(out)


def freshness_multiplier(
    age_days: int | None,
    bands: tuple = DEFAULT_FRESHNESS_BANDS,
) -> float | None:
    """Task #32 — smooth freshness weight for a Parkev rec's age.

    Returns None when ``age_days`` is None (no rec) — the multiplier is then
    NOT applied at all (score = tier × conviction_weight), so absence of a
    rec is neither rewarded as fresh nor punished as stale.
    """
    if age_days is None:
        return None
    for max_days, mult in bands:
        if max_days is None or age_days <= max_days:
            return mult
    return bands[-1][1]               # above every bounded band


def conviction_score(
    tier: int | None,
    conviction: str | None,
    age_days: int | None,
    *,
    pullback_zone: bool = False,
    rsi: float | None = None,
    iv_rank: float | None = None,
    lt_broken: bool = False,
    stacks_with_held: bool = False,
    earnings_in_window: bool = False,
    freshness_bands: tuple = DEFAULT_FRESHNESS_BANDS,
) -> float:
    """tier × conviction_weight × freshness, plus setup bonuses/penalties.

    All inputs are MEASURED values (Parkev cache / technicals) — a missing
    input contributes its most conservative multiplier, never a guess.
    Task #32: freshness decays smoothly (≤7d 2.5 → >60d 0.25); an unknown
    age (no rec) skips the multiplier entirely.
    """
    t = int(tier) if isinstance(tier, (int, float)) else 0
    weight = _CONVICTION_WEIGHT.get(conviction or "", 0.5)
    fresh = freshness_multiplier(age_days, freshness_bands)
    score = t * weight * (fresh if fresh is not None else 1.0)
    if pullback_zone:
        score += 2.0
    if rsi is not None and rsi < 35:
        score += 2.0
    if iv_rank is not None and iv_rank >= 85:
        score += 1.0
    if lt_broken:
        score -= 3.0
    if stacks_with_held:
        score -= 3.0
    if earnings_in_window:
        score -= 5.0
    return score


def star_badge(score: float) -> str:
    """⭐⭐⭐ ≥18 · ⭐⭐ ≥12 · ⭐ ≥6 · '' below (qualified but unstarred)."""
    if score >= 18:
        return "⭐⭐⭐"
    if score >= 12:
        return "⭐⭐"
    if score >= 6:
        return "⭐"
    return ""


# ── Task #30 — equity-stacking gate ───────────────────────────────────────
# Single source of truth moved to analysis/equity_stacking.py (rule #43,
# GOOG 2026-07-31: the LTO card recommended the exact trade this gate
# hard-skips — the card surfaces now consume the same module). The wrappers
# below keep the playbook's historical call shape.


def _equity_pct_by_ticker(analytics, nlv: float) -> dict[str, float]:
    """Held EQUITY market value per ticker as a fraction of NLV, measured
    from the snapshot positions. Empty dict when unmeasurable (fail-open —
    no penalty is ever applied on missing data)."""
    snap = _analytics_get(analytics, "snapshot_data")
    positions = snap.get("positions") if isinstance(snap, dict) else None
    try:
        from analysis.equity_stacking import equity_pct_by_ticker
    except ImportError:
        from equity_stacking import equity_pct_by_ticker  # type: ignore
    return equity_pct_by_ticker(positions, nlv)


def _equity_stacking_penalty(
    ticker: str,
    held_equity_pct: float | None,
    cfg: dict,
) -> tuple[float, str | None, bool]:
    """Task #30 — delegates to the shared analysis.equity_stacking module
    (see its docstring for the bands). Kept as a wrapper so the playbook's
    call sites and tests are unchanged."""
    try:
        from analysis.equity_stacking import stacking_penalty
    except ImportError:
        from equity_stacking import stacking_penalty  # type: ignore
    return stacking_penalty(ticker, held_equity_pct, cfg)


# ── Task #31 — support-quality gate ───────────────────────────────────────


def _evaluate_strike_support_quality(
    strike: float,
    spot: float | None,
    clusters: list | None,
    cfg: dict,
) -> tuple[str, float, str | None]:
    """Task #31 — sharpen the proximity-only support read with TOUCH COUNT.
    Returns ``(quality_label, score_delta, tag_or_None)``:

      "at_multi_touch"    strike within ±5% of a ≥2-touch cluster → OK
      "below_multi_touch" strike ≤15% BELOW a ≥2-touch cluster → OK
                          (protective — assignment happens under support)
      "single_touch_only" anchored only to a 1-touch level → −2 + tag
      "floats"            > 5% from ANY cluster → −2 + tag
      "unknown"           no S/R data → no penalty (fail-open)

    ``clusters`` is the ``supports`` list from the snapshot's
    support_resistance payload ([{price, touches, ...}]). ``spot`` is
    accepted for signature completeness (supports are below spot by
    construction).
    """
    del spot                          # measured but not needed — see docstring
    if not strike or strike <= 0 or not clusters:
        return "unknown", 0.0, None
    min_touches = int(_f(cfg.get("min_touches_for_strong"),
                         DEFAULT_SQ_MIN_TOUCHES))
    at_band = _f(cfg.get("at_support_band_pct"), DEFAULT_SQ_AT_BAND_PCT)
    below_band = _f(cfg.get("below_support_band_pct"),
                    DEFAULT_SQ_BELOW_BAND_PCT)
    p_single = _f(cfg.get("single_touch_penalty"),
                  DEFAULT_SQ_SINGLE_TOUCH_PENALTY)
    p_floats = _f(cfg.get("floats_penalty"), DEFAULT_SQ_FLOATS_PENALTY)
    parsed: list[tuple[float, int]] = []
    for lv in clusters:
        if not isinstance(lv, dict):
            continue
        price = _f(lv.get("price"), None)
        if price is None or price <= 0:
            continue
        touches = lv.get("touches")
        touches = int(touches) if isinstance(touches, (int, float)) else 1
        parsed.append((price, touches))
    if not parsed:
        return "unknown", 0.0, None   # payload present but unreadable

    def _at(price: float) -> bool:
        return abs(price - strike) / strike <= at_band

    def _below(price: float) -> bool:
        # Strike sits BELOW the cluster by up to below_band — protective.
        return strike < price and (price - strike) / price <= below_band

    multi = [(p, t) for p, t in parsed if t >= min_touches]
    if any(_at(p) for p, _ in multi):
        return "at_multi_touch", 0.0, None
    if any(_below(p) for p, _ in multi):
        return "below_multi_touch", 0.0, None
    if any(_at(p) or _below(p) for p, _ in parsed):
        return "single_touch_only", -p_single, \
            "⚠ strike anchored to 1-touch support only (weak floor)"
    return "floats", -p_floats, \
        "⚠ strike floats in air (no support within 5%)"


# ── Rule #43 (AMZN 2026-08-03) — Phase 2 final gate battery ───────────────
# Observed defect: the playbook selected "AMZN $240P Sep 04 '26 | +$77 |
# $24,000 | 4% | ⭐⭐⭐ · Parkev BUY High · 3d · IV rank 100 · 🔓 unlocks after
# Phase 1" while AMZN's LIVE RSI was ~80 (the snapshot RSI 66 was pre-gap —
# the same briefing's vintage-guard footer listed "AMZN +5.5%"), the yield
# was 4% annualized (below risk-free), and IV rank 100 was the post-gap
# realized-vol artifact. Three gates, applied to EVERY candidate before
# selection; every exclusion renders in the warnings footer (rule #24).

DEFAULT_PLAYBOOK_MIN_ANNUALIZED_YIELD = 0.12      # 12% ann floor
DEFAULT_PLAYBOOK_MIN_PREMIUM_PCT = 0.005          # 0.5% of collateral


def _quote_price(q) -> float | None:
    """Live last-trade price from a snapshot quotes entry (lastTrade/last/
    price — the same keys the vintage guard reads). None when unmeasured."""
    if not isinstance(q, dict):
        return None
    for k in ("lastTrade", "last", "price"):
        v = _f(q.get(k), None)
        if v is not None and v > 0:
            return v
    return None


def _wilder_rsi_live(recent_closes, live_price, period: int = 14) -> float | None:
    """Wilder's RSI on the daily close series with today's LIVE price
    appended as the current bar. Returns None when the series is too short
    (< period+1 prices) — never fabricated (rule #19). In production
    ``recent_closes`` carries ~6 closes, so this is usually uncomputable and
    the fail-safe exclusion path applies instead."""
    if not isinstance(recent_closes, (list, tuple)) or live_price is None:
        return None
    try:
        prices = [float(c) for c in recent_closes
                  if c is not None and float(c) > 0]
        prices.append(float(live_price))
    except (TypeError, ValueError):
        return None
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, ls in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + ls) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _phase2_gate_battery(
    cand: ConvictionScoredCandidate,
    *,
    iv_rank: float | None,
    tech: dict | None,
    live_quote: dict | None,
    cfg: dict,
    config: dict | None,
    min_score: float,
) -> list[str]:
    """Final gate battery every Phase 2 candidate passes BEFORE selection.

    1. Live-RSI enforcement — the snapshot RSI is vintage-checked against the
       live quote (same threshold the vintage guard uses,
       ``vintage_guard.max_intraday_move_pct``, default 5%). Beyond it the
       snapshot RSI is untrusted for QUALIFYING: live RSI is recomputed
       (Wilder's, live price as the current bar) when the close series
       allows, and the standard hook applies to the trusted value — >70 hard
       block, 60-70 extended-wait (the playbook opens only from the ≤60
       bands). Live RSI uncomputable + UPWARD move → fail-safe exclude with
       "⚠ stale RSI — reverify" (a big up-move on an already-elevated RSI
       means the live RSI is plausibly >70); the exclusion stays visible in
       the warnings footer (rule #24).
    2. Yield floor — ``playbook_min_annualized_yield`` (default 0.12) and
       ``playbook_min_premium_pct_of_collateral`` (default 0.005): below
       EITHER → excluded ("premium doesn't pay for the risk"); ⭐ conviction
       never renders on a floor-failing candidate (it never reaches opens).
    3. IV-rank honesty — reuses analysis/iv_honesty: claimed IV rank ≥60
       meeting delivered yield <20% ann drops the "IV rank N" token from the
       conviction cell, appends "⚠ IV rank gap-inflated", and REVOKES the +1
       IV-rank conviction bonus (re-score; re-checked against the floor).

    Returns the list of exclusion reasons (empty = candidate stays); mutates
    ``cand`` for annotations and the IV re-score. Fail-open on missing data
    except the stale-upmove fail-safe.
    """
    reasons: list[str] = []

    # Gate 3 — IV-rank honesty (re-score first, before floors read the score).
    try:
        from analysis.iv_honesty import claimed_fat_but_thin
    except ImportError:
        claimed_fat_but_thin = None
    if (claimed_fat_but_thin is not None and iv_rank is not None
            and claimed_fat_but_thin(
                iv_rank, cand.annualized_yield_pct, config)):
        iv_token = f"IV rank {iv_rank:.0f}"
        cand.setup_flags = [f for f in cand.setup_flags if f != iv_token]
        cand.setup_flags.append("⚠ IV rank gap-inflated")
        if iv_rank >= 85.0:
            # Revoke the +1 IV-rank conviction bonus — a gap-inflated rank
            # is not rich premium (the delivered yield says so).
            cand.conviction_score -= 1.0
            if cand.conviction_score < min_score:
                reasons.append(
                    f"conviction {cand.conviction_score:.1f} fell below the "
                    f"floor {min_score:g} after the IV-rank bonus was "
                    f"revoked (rank {iv_rank:.0f} is gap-inflated)")

    # Gate 2 — yield floor: the premium must pay for the risk.
    min_ann = _f(cfg.get("playbook_min_annualized_yield"),
                 DEFAULT_PLAYBOOK_MIN_ANNUALIZED_YIELD)
    min_prem_pct = _f(cfg.get("playbook_min_premium_pct_of_collateral"),
                      DEFAULT_PLAYBOOK_MIN_PREMIUM_PCT)
    ann = cand.annualized_yield_pct
    if ann is not None and min_ann is not None and ann < min_ann * 100.0:
        reasons.append(
            f"yield floor: {ann:.0f}% ann < {min_ann * 100:.0f}% — "
            f"premium doesn't pay for the risk")
    prem_pct = (cand.mid_price / cand.strike) if cand.strike > 0 else None
    if (prem_pct is not None and min_prem_pct is not None
            and prem_pct < min_prem_pct):
        reasons.append(
            f"yield floor: premium {prem_pct * 100:.2f}% of collateral < "
            f"{min_prem_pct * 100:.1f}% — premium doesn't pay for the risk")

    # Gate 1 — live-RSI enforcement (vintage check on the snapshot RSI).
    try:
        from analysis.vintage_guard import (
            DEFAULT_MAX_INTRADAY_MOVE_PCT as _vg_default)
    except ImportError:
        _vg_default = 0.05
    vg_cfg = (config or {}).get("vintage_guard") \
        if isinstance(config, dict) else None
    vg_cfg = vg_cfg if isinstance(vg_cfg, dict) else {}
    vg_enabled = bool(vg_cfg.get("enabled", True))
    thr = _f(vg_cfg.get("max_intraday_move_pct"), _vg_default) or _vg_default
    tech = tech if isinstance(tech, dict) else {}
    tech_close = _f(tech.get("spot"), None)
    live = _quote_price(live_quote)

    # Gate 0 — chase guard (rule #44, INTC 2026-08-05): a multi-session
    # vertical already baked into FRESH daily bars can't be caught by RSI
    # off an oversold base (INTC: +24.1% in 5 sessions, RSI 49.5 in-band)
    # nor by the staleness check above — measure the tape directly.
    try:
        from analysis import chase_guard as _chase
    except ImportError:
        _chase = None
    if _chase is not None:
        _spot_for_chase = live if live is not None else tech_close
        ch = _chase.check_chase(cand.ticker,
                                closes=tech.get("recent_closes"),
                                spot=_spot_for_chase, config=config)
        if ch["blocked"]:
            reasons.append(ch["reason"])
        elif ch["caution"]:
            cand.warnings.append(ch["caution"])

    move = None
    if vg_enabled and tech_close and tech_close > 0 and live is not None:
        move = (live - tech_close) / tech_close
    trusted_rsi = cand.rsi
    if move is not None and abs(move) <= thr and cand.rsi is not None:
        # Vintage-checked against a live quote and fresh — verified.
        cand.rsi_verified = True
    if move is not None and abs(move) > thr:
        # Snapshot RSI is pre-gap — untrusted for qualifying purposes.
        live_rsi = _wilder_rsi_live(tech.get("recent_closes"), live)
        prev = f"{cand.rsi:.0f}" if cand.rsi is not None else "n/a"
        if live_rsi is not None:
            cand.warnings.append(
                f"⚠ live RSI {live_rsi:.0f} recomputed at ${live:,.2f} "
                f"(spot {move * 100:+.1f}% since the technicals close; "
                f"snapshot RSI {prev} was pre-gap)")
            trusted_rsi = live_rsi
            cand.rsi = round(float(live_rsi), 1)
            cand.rsi_verified = True   # recomputed at the live price
        else:
            cand.warnings.append(
                f"⚠ stale RSI — reverify (spot {move * 100:+.1f}% since the "
                f"technicals close; live RSI not computable from the close "
                f"series)")
            trusted_rsi = None
            if move > 0:
                # Fail-safe: a big UP-move on an already-elevated RSI means
                # the live RSI is plausibly >70 — exclude from the composed
                # NEW-OPEN trade, keep in the footer as a reference note.
                reasons.append(
                    f"⚠ stale RSI — reverify: spot {move * 100:+.1f}% since "
                    f"the technicals close; snapshot RSI {prev} is pre-gap "
                    f"and the live RSI is plausibly >70")
    # Standard hook on the TRUSTED value (snapshot when fresh, live when
    # recomputed): >70 hard block, 60-70 extended-wait. Fail-open on None.
    if trusted_rsi is not None:
        try:
            from analysis import rsi_discipline as _rsi
            th = _rsi.load_thresholds(config)
            if _rsi.hook("put", float(trusted_rsi), th).removed:
                reasons.append(
                    f"RSI {trusted_rsi:.0f} overbought — new put-sale hard "
                    f"block (hard rule #11)")
            elif _rsi.put_extended_wait(float(trusted_rsi), th) is not None:
                reasons.append(
                    f"RSI {trusted_rsi:.0f} extended (60-70) — the playbook "
                    f"opens only from the ≤60 bands; wait for a pullback")
        except Exception:
            pass                      # fail-open — never crash the playbook

    # Gate 4 — rule #38 (INTC 2026-08-05): a non-BUY rating (or no rec) is
    # not a catalyst. INTC rode in on "HOLD · Medium · 27d" + the
    # pullback-zone bonus; bonuses alone can never carry a non-BUY past the
    # floor. Non-BUY candidates qualify ONLY via the full independent-setup
    # bar: RSI VERIFIED (live-checked this cycle) in the 35-55 band + IV
    # rank ≥ 50 (+ the chase guard, gate 0 above). Qualifiers carry the
    # rule-#38-precise badge; failures are excluded with the measured values.
    if (cand.parkev_rating or "").upper() != "BUY":
        rating_label = (f"Parkev {cand.parkev_rating}" if cand.parkev_rating
                        else "no third-party rec")
        indep_ok = (cand.rsi_verified and trusted_rsi is not None
                    and 35.0 <= float(trusted_rsi) <= 55.0
                    and iv_rank is not None and float(iv_rank) >= 50.0)
        if indep_ok:
            cand.independent_setup_badge = (
                f"{rating_label} (not a BUY catalyst) — independent setup: "
                f"RSI {trusted_rsi:.0f} verified · IV {iv_rank:.0f}")
        else:
            if trusted_rsi is None:
                rsi_s = "RSI unmeasured"
            else:
                rsi_s = (f"RSI {trusted_rsi:.0f}"
                         + ("" if cand.rsi_verified else " unverified"))
            iv_s = (f"IV rank {iv_rank:.0f}" if iv_rank is not None
                    else "IV rank unmeasured")
            reasons.append(
                f"{rating_label} is not a BUY catalyst (rule #38) — a "
                f"non-BUY qualifies only via the full independent-setup bar "
                f"(RSI verified 35-55 + IV rank ≥ 50 + chase guard); "
                f"measured: {rsi_s}, {iv_s}")
    return reasons


# ── Core ──────────────────────────────────────────────────────────────────


def compute_playbook(
    held_csps: list[dict],
    candidate_csps: list[dict],
    parkev_recs: dict[str, dict] | None,
    directive_holds: set[str] | None,
    analytics: dict | None,
    config: dict | None,
    *,
    today: _date | None = None,
) -> RotationPlaybook | None:
    """Compose the daily rotation playbook: all freeable winner closes +
    a conviction-ranked greedy deployment of the freed collateral.

    Args:
        held_csps: held SHORT-put dicts (snapshot positions or options
            reviews — same shapes ``analysis.csp_rotation`` accepts).
        candidate_csps: open-side candidate dicts (LT opportunities,
            PULLBACK CSP ideas, scout CSP entries). Optional per-dict
            enrichment keys read when present: ``iv_rank``, ``rsi_14``,
            ``drawdown_pct``, ``verdict``, ``earnings_date``,
            ``days_to_earnings``.
        parkev_recs: {TICKER: rec dict} with ``rating_tier``,
            ``conviction``, ``age_days``, ``recommendation``.
        directive_holds: tickers (or contract symbols) with a standing hold
            from fable_advisor_memory.md — excluded from the close side.
        analytics: optional context — nlv, snapshot_data, technicals,
            iv_ranks, earnings_calendar, stress_coverage.
        config: briefing config (reads the ``rotation_playbook`` block).
        today: injection point for deterministic tests.

    Returns None when total freeable collateral is below the configured
    floor (not worth composing), or on ANY internal error (fail-open —
    this step never blocks the briefing).
    """
    try:
        return _compute(held_csps, candidate_csps, parkev_recs or {},
                        {str(t).upper() for t in (directive_holds or set())},
                        analytics, config, today=today or _date.today())
    except Exception as e:  # noqa: BLE001 — hard fail-open per user constraint
        print(f"[rotation-playbook] compute failed (non-fatal): {e}",
              file=sys.stderr)
        return None


def _compute(
    held_csps: list[dict],
    candidate_csps: list[dict],
    parkev_recs: dict[str, dict],
    directive_holds: set[str],
    analytics: dict | None,
    config: dict | None,
    *,
    today: _date,
) -> RotationPlaybook | None:
    cfg = (config or {}).get("rotation_playbook", {}) if isinstance(config, dict) else {}
    if not cfg.get("enabled", True):
        return None
    min_freed = _f(cfg.get("min_freed_collateral_usd"), DEFAULT_MIN_FREED_USD)
    max_opens = int(_f(cfg.get("max_opens"), DEFAULT_MAX_OPENS))
    deploy_target = _f(cfg.get("deploy_target_pct"), DEFAULT_DEPLOY_TARGET_PCT)
    min_score = _f(cfg.get("min_conviction_score"), DEFAULT_MIN_CONVICTION_SCORE)
    max_same_ticker = int(_f(cfg.get("diversify_max_same_ticker"),
                             DEFAULT_DIVERSIFY_MAX_SAME_TICKER))
    min_capture = _f(cfg.get("min_close_capture"), DEFAULT_MIN_CLOSE_CAPTURE)
    bucket_warning_pct = _f(cfg.get("bucket_warning_pct"),
                            DEFAULT_BUCKET_WARNING_PCT)
    # Task #29 — bucket diversification knobs.
    div_cfg = cfg.get("bucket_diversification")
    div_cfg = div_cfg if isinstance(div_cfg, dict) else {}
    max_bucket_share = _f(div_cfg.get("max_bucket_pct_of_deploy"),
                          DEFAULT_MAX_BUCKET_PCT_OF_DEPLOY)
    tie_break_diverse = bool(div_cfg.get("tie_break_prefer_diverse", True))
    hard_bucket_cap = _f(div_cfg.get("hard_cap_pct"), None)
    allow_stacking = bool(cfg.get("allow_stacking_with_held", False))
    lt_override_fresh_high = bool(cfg.get("lt_override_fresh_high_conviction",
                                          True))
    lt_override_medium = bool(cfg.get("lt_override_medium_conviction", True))
    treat_warn_as_annotation = bool(cfg.get("treat_warn_as_annotation", True))
    # Task #30/#31/#32 config blocks (each fail-open to defaults).
    eq_cfg = cfg.get("equity_stacking")
    eq_cfg = eq_cfg if isinstance(eq_cfg, dict) else {}
    sq_cfg = cfg.get("support_quality")
    sq_cfg = sq_cfg if isinstance(sq_cfg, dict) else {}
    fresh_bands = _parse_freshness_bands(cfg.get("freshness_bands"))
    # Rule #43 (RDDT) — unknown-earnings conviction penalty for new opens.
    earn_unknown_penalty = _f(cfg.get("earnings_unknown_penalty"),
                              DEFAULT_EARNINGS_UNKNOWN_PENALTY)

    technicals = _analytics_get(analytics, "technicals") or {}
    snapshot_data = _analytics_get(analytics, "snapshot_data")
    if not isinstance(snapshot_data, dict):
        snapshot_data = {"technicals": technicals} if technicals else {}
    elif not technicals:
        technicals = snapshot_data.get("technicals") or {}
    iv_ranks = _analytics_get(analytics, "iv_ranks") \
        or snapshot_data.get("iv_ranks") or {}
    earnings_cal = _analytics_get(analytics, "earnings_calendar") or {}
    # Rule #43 gate battery — live quotes for the vintage check (fail-open).
    quotes_map = snapshot_data.get("quotes") or {}
    quotes_map = quotes_map if isinstance(quotes_map, dict) else {}

    # Spots for intrinsic math (fail-open).
    spots: dict[str, float] = {}
    if isinstance(technicals, dict):
        for tk, tech in technicals.items():
            if isinstance(tech, dict):
                s = _f(tech.get("spot"), None)
                if s:
                    spots[str(tk).upper()] = s

    # ── Phase 1: freeable closes ─────────────────────────────────────────
    closes: list[FreeableClose] = []
    excluded_directive: list[str] = []
    held_strikes_by_ticker: dict[str, list[float]] = {}
    # Task #29 — the short-put book that SURVIVES Phase 1 (not swept), in
    # the shape analyze_put_buckets reads. Post-playbook single-Friday
    # obligation = surviving book + Phase 2 opens.
    surviving_book: list[dict] = []
    for raw in held_csps or []:
        h = _normalize_held(raw, today, spots)
        if h is None:
            continue
        held_strikes_by_ticker.setdefault(h.ticker, []).append(h.strike)
        _book_row = {"underlying": h.ticker, "position_type": "short_put",
                     "strike": h.strike, "expiration": h.expiration,
                     "qty": h.qty}
        if h.capture_pct < min_capture:
            surviving_book.append(_book_row)
            continue                  # not a banked winner — never swept
        sym = h.symbol or f"{h.ticker}_PUT_{h.strike:g}"
        directive_held = (h.ticker in directive_holds
                          or str(sym).upper() in directive_holds)
        if directive_held:
            excluded_directive.append(sym)
            surviving_book.append(_book_row)
            continue                  # standing user directive — excluded
        closes.append(FreeableClose(
            ticker=h.ticker, contract=sym, strike=h.strike,
            expiration=h.expiration or "", qty=h.qty,
            capture_pct=h.capture_pct,
            remaining_extrinsic=h.remaining_theta_dollars,
            freed_collateral=h.collateral,
            realized_profit=h.banked_profit_dollars,
            buy_to_close_mid=h.current_mid,
            dte_remaining=h.days_remaining,
            directive_held=False,
        ))

    # Rank: biggest wins first — freed collateral × capture.
    closes.sort(key=lambda c: -(c.freed_collateral * c.capture_pct))
    total_freed = sum(c.freed_collateral for c in closes)
    total_realized = sum(c.realized_profit for c in closes)

    if total_freed < min_freed:
        return None                   # not worth composing a playbook

    # ── Bug #23: projected post-Phase-1 portfolio state ──────────────────
    # Phase 2 candidates are gated against the state AFTER the closes fire
    # (cash + freed, obligations − freed) — gating the composed deployment
    # on the PRE-close cash floor defeats the playbook's purpose. All
    # fail-open: an uncomputable projection silences the portfolio gates.
    try:
        from analysis.capacity_gate import coverage_ratio_from as _cov_from
        coverage_measured = _cov_from(analytics)
    except ImportError:
        coverage_measured = None
    measured_cash = _cash_from(analytics)
    obligations_measured = _total_put_obligations(analytics)
    nlv = _nlv_from(analytics)
    projected_cash = (measured_cash + total_freed
                      if measured_cash is not None else None)
    projected_obl = (max(obligations_measured - total_freed, 0.0)
                     if obligations_measured and obligations_measured > 0
                     else None)
    projected_state: dict = {}
    if nlv > 0:
        projected_state["nlv"] = nlv
    if projected_cash is not None:
        projected_state["cash"] = projected_cash
        if nlv > 0:
            projected_state["cash_pct"] = projected_cash / nlv
    if projected_obl is not None:
        projected_state["obligation"] = projected_obl
        if projected_cash is not None and projected_obl > 0:
            projected_state["coverage_ratio"] = projected_cash / projected_obl
    # Task #28 — coverage-adaptive deployment cap, resolved against the
    # PROJECTED post-Phase-1 coverage (Phase 1 already happened by the time
    # Phase 2 fires). Projection uncomputable → the measured pre-close
    # ratio; neither → flat legacy behavior (fail-open).
    _cov_for_cap = projected_state.get("coverage_ratio")
    if _cov_for_cap is None and coverage_measured is not None:
        _cov_for_cap = float(coverage_measured)
    deploy_cap, cap_label, cap_next_note, cap_adaptive = _resolve_deploy_cap(
        _cov_for_cap, cfg, deploy_target)
    # Freed collateral per expiration DATE — subtracted from the validator's
    # per-date bucket map so the bucket gate also sees the post-close book.
    closed_freed_by_exp: dict = {}
    for _c in closes:
        _d = None
        try:
            _y, _m, _dd = str(_c.expiration)[:10].split("-")
            _d = _date(int(_y), int(_m), int(_dd))
        except (ValueError, TypeError):
            pass
        if _d is not None:
            closed_freed_by_exp[_d] = (closed_freed_by_exp.get(_d, 0.0)
                                       + _c.freed_collateral)
    # Were the portfolio gates closed PRE-close? Passing candidates then get
    # the "unlocks after Phase 1" tag so the sequencing is explicit.
    _cfg_top = config if isinstance(config, dict) else {}
    _entry_floor = _f(_cfg_top.get("entry_gate_min_coverage"), 0.50)
    _cash_floor = _f(_cfg_top.get("cash_floor_pct"), 0.05)
    preclose_gated = bool(
        (coverage_measured is not None and coverage_measured < _entry_floor)
        or (measured_cash is not None and nlv > 0
            and measured_cash / nlv < _cash_floor))
    try:
        from analysis import pre_trade_validator as _ptv
    except ImportError:
        _ptv = None

    # ── Phase 2: enrich + filter + rank candidates ───────────────────────
    try:
        from analysis.put_overlap_check import check_strike_overlap
    except ImportError:
        check_strike_overlap = None
    try:
        from analysis.lt_verdict_gate import check_lt_verdict_gate
    except ImportError:
        check_lt_verdict_gate = None

    # Bug #23 — re-admit candidates skipped upstream ONLY by portfolio-state
    # gates; they re-run the validator below against the projected state.
    cand_pool: list = []
    for raw in candidate_csps or []:
        readmitted = _readmit_portfolio_blocked(raw)
        cand_pool.append(readmitted if readmitted is not None else raw)

    # Task #30 — held EQUITY concentration per ticker (fraction of NLV);
    # empty when unmeasurable so the gate is a no-op (fail-open).
    equity_pct = _equity_pct_by_ticker(analytics, nlv)
    equity_hard_skips: list[str] = []
    # Rule #43 — candidates excluded by the final Phase 2 gate battery
    # (live-RSI / yield floor / IV honesty), surfaced in the footer.
    battery_skips: list[str] = []

    scored: list[ConvictionScoredCandidate] = []
    seen: set[tuple] = set()
    for raw in cand_pool:
        c = _normalize_candidate(raw, today, technicals)
        if c is None:
            continue                  # includes missing-mid skip (rule #19)
        key = (c.ticker, c.strike, c.expiration)
        if key in seen:
            continue
        seen.add(key)

        tech = technicals.get(c.ticker) if isinstance(technicals, dict) else None
        tech = tech if isinstance(tech, dict) else {}
        raw_d = raw if isinstance(raw, dict) else {}
        rsi = _f(raw_d.get("rsi_14"), None)
        if rsi is None:
            rsi = c.rsi
        iv_rank = _f(raw_d.get("iv_rank"), None)
        if iv_rank is None and isinstance(iv_ranks, dict):
            iv_rank = _f(iv_ranks.get(c.ticker), None)
        drawdown = _f(raw_d.get("drawdown_pct"), None)
        if drawdown is None:
            drawdown = _f(tech.get("drawdown_pct"), None)
        verdict = str(raw_d.get("verdict") or "")

        setup_flags: list[str] = []
        warnings: list[str] = []

        # Pullback-zone setup: the scout's own verdict, or the RSI 35-50
        # pullback band (both measured this cycle).
        pullback = ("pullback" in verdict.lower()
                    or (rsi is not None and 35 <= rsi < 50))
        if pullback:
            setup_flags.append("pullback zone")
        if rsi is not None and rsi < 35:
            setup_flags.append(f"RSI {rsi:.0f} deep oversold")
        if iv_rank is not None and iv_rank >= 85:
            setup_flags.append(f"IV rank {iv_rank:.0f}")
        if drawdown is not None and drawdown >= 30:
            setup_flags.append(f"drawdown {drawdown:.0f}%")

        # Rule #39 — LT-verdict gate. Gate failure = hard exclude, with ONE
        # playbook-specific exception (config-gated, on by default): a
        # FRESH (≤14d) Parkev BUY with High conviction may override — the
        # candidate stays in with the −3 LT penalty and a visible warning,
        # never silently. (The manual Scenario A opened CRM/ORCL on exactly
        # this read: fresh high-conviction BUY on a drawdown chart.)
        rec_pk = parkev_recs.get(c.ticker) or {}
        lt_verdict = None
        lt_broken = False
        if check_lt_verdict_gate is not None and snapshot_data:
            g = check_lt_verdict_gate(c.ticker, snapshot_data, rec_pk)
            lt_verdict = g.get("verdict")
            if not g.get("pass", True):
                age_pk = rec_pk.get("age_days")
                conv_pk = rec_pk.get("conviction")
                tier_pk = rec_pk.get("rating_tier")
                tier_pk = int(tier_pk) if isinstance(tier_pk, (int, float)) \
                    else 0
                fresh_pk = (isinstance(age_pk, (int, float)) and age_pk <= 14)
                is_buy_pk = (str(rec_pk.get("recommendation") or "").upper()
                             == "BUY")
                override_note = None
                if lt_override_fresh_high and is_buy_pk and fresh_pk:
                    if conv_pk == "High" and (not lt_override_medium
                                              or tier_pk >= 3):
                        # Strict branch (pre-#27 behavior when the medium
                        # widening is switched off: High, any tier).
                        override_note = f"Parkev BUY·High ({int(age_pk)}d)"
                    elif (lt_override_medium and conv_pk == "Medium"
                          and tier_pk >= 3
                          and drawdown is not None and drawdown >= 30):
                        # Task #27 widened branch — a fresh Medium BUY on a
                        # deep-drawdown chart (the ORCL/ZS shape) earns the
                        # same exception, never a silent pass.
                        override_note = (
                            f"Parkev BUY·Medium ({int(age_pk)}d) on a "
                            f"{drawdown:.0f}% drawdown")
                if override_note is None:
                    continue          # never open into a broken chart
                lt_broken = True
                warnings.append(
                    f"⚠ LT {lt_verdict or 'trend'} gate overridden — fresh "
                    f"{override_note}; rule #39 playbook "
                    f"exception, verify the chart before firing")
            elif g.get("warning"):
                warnings.append(f"⚠ {g['warning']}")
        if not lt_broken and lt_verdict in ("broken", "downtrend"):
            lt_broken = True
            warnings.append(f"LT {lt_verdict} chart")

        # Rule #40 — 5% strike overlap with a held put = hard exclude.
        held_strikes = held_strikes_by_ticker.get(c.ticker) or []
        if check_strike_overlap is not None and held_strikes:
            ov = check_strike_overlap(c.ticker, c.strike, held_strikes)
            if ov.get("overlap"):
                continue
        # Different-strike same-name = stacking.
        stacks = bool(held_strikes)
        if stacks:
            strikes = ", ".join(f"${s:g}" for s in sorted(held_strikes))
            warnings.append(
                f"stacks with existing {c.ticker} put(s) at {strikes}")

        # Earnings inside the expiry window = hard exclude (no override).
        edays = raw_d.get("days_to_earnings")
        edays = int(edays) if isinstance(edays, (int, float)) else None
        if edays is None:
            edate = raw_d.get("earnings_date") or earnings_cal.get(c.ticker)
            edays = _days_from_exp(str(edate)[:10] if edate else None, today)
        earnings_in_window = (edays is not None and c.dte is not None
                              and 0 <= edays <= c.dte)
        if earnings_in_window:
            warnings.append(f"earnings in {edays}d — inside expiry window")
        # Rule #43 (RDDT 2026-07-31): NO measured earnings date on a
        # single-stock underlying is NOT a pass — the candidate stays in
        # (data absence ≠ evidence of earnings) but takes the conviction
        # penalty and carries a loud "⚠ earnings unverified" flag. ETFs
        # exempt (no print). Fail-open only when the helper is missing.
        earnings_unknown = False
        if edays is None:
            try:
                from analysis.earnings_unknown import (
                    UNVERIFIED_FLAG, is_earnings_exempt)
                _earn_exempt = is_earnings_exempt(c.ticker, _cfg_top)
            except ImportError:
                _earn_exempt, UNVERIFIED_FLAG = True, "⚠ earnings unverified"
            if not _earn_exempt:
                earnings_unknown = True
                setup_flags.append(UNVERIFIED_FLAG)
                warnings.append(
                    f"⚠ earnings unverified — no date from the calendar; "
                    f"verify no {c.ticker} print before "
                    f"{_fmt_exp_compact(c.expiration)} at the broker "
                    f"before placing")

        tier = rec_pk.get("rating_tier")
        tier = int(tier) if isinstance(tier, (int, float)) else 0
        conviction = rec_pk.get("conviction")
        conviction = str(conviction) if conviction else None
        age = rec_pk.get("age_days")
        age = int(age) if isinstance(age, (int, float)) else None
        rating = rec_pk.get("recommendation")
        rating = str(rating).upper() if rating else None
        # Task #27 guard: with the floor at 4, setup bonuses alone (+2
        # pullback +2 oversold +1 IV = 5) could float a Parkev SELL name
        # over it. The scout's AVOID branch vetoes SELL recs as new-open
        # catalysts (hard rule #25) — same discipline here, no override.
        if rating == "SELL":
            continue

        score = conviction_score(
            tier, conviction, age,
            pullback_zone=pullback, rsi=rsi, iv_rank=iv_rank,
            lt_broken=lt_broken, stacks_with_held=stacks,
            earnings_in_window=earnings_in_window,
            freshness_bands=fresh_bands,
        )
        # Rule #43 — demote (never exclude) when the earnings date is
        # unknown: the IV-90-on-a-drawdown shape is exactly pre-earnings
        # premium, and "no date found" must not read as "no print coming".
        if earnings_unknown:
            score -= earn_unknown_penalty

        # Task #30 — equity-stacking gate: a short put on a name whose
        # EQUITY is already a large slice of NLV concentrates single-name
        # risk. ≥10% NLV → HARD SKIP, surfaced in the warnings footer.
        held_eq_pct = equity_pct.get(c.ticker)
        eq_delta, eq_tag, eq_skip = _equity_stacking_penalty(
            c.ticker, held_eq_pct, eq_cfg)
        if eq_skip:
            equity_hard_skips.append(
                f"⛔ {c.ticker} ${c.strike:g}P skipped — {eq_tag}")
            continue
        if eq_tag:
            warnings.append(eq_tag)
        score += eq_delta

        # Task #31 — support-quality gate: proximity alone isn't an anchor.
        # 1-touch-only or floating strikes take a penalty; at/below a
        # ≥2-touch cluster is clean. Missing S/R → no penalty (fail-open).
        sr_payload = tech.get("support_resistance")
        sq_clusters = (sr_payload.get("supports")
                       if isinstance(sr_payload, dict) else None)
        sq_label, sq_delta, sq_tag = _evaluate_strike_support_quality(
            c.strike, spots.get(c.ticker), sq_clusters, sq_cfg)
        if sq_tag:
            warnings.append(sq_tag)
        score += sq_delta

        # Task #27 → rule #38 (INTC 2026-08-05): non-BUY names clearing the
        # floor on setup bonuses are now judged by the battery's
        # independent-setup gate (gate 4) — verified RSI 35-55 + IV ≥ 50 +
        # chase guard — which either attaches the precise badge or excludes
        # with reasons in the footer. No pre-battery annotation needed here.

        cand = ConvictionScoredCandidate(
            ticker=c.ticker, strike=c.strike, expiration=c.expiration or "",
            dte=c.dte, collateral_required=c.collateral,
            premium=c.premium_per_share * 100.0 * 1,
            annualized_yield_pct=candidate_annualized_yield(
                c.premium_per_share, c.dte, c.strike),
            mid_price=c.premium_per_share,
            parkev_rating=rating, parkev_tier=tier,
            parkev_conviction=conviction, parkev_age_days=age,
            conviction_score=score,
            setup_flags=setup_flags, warnings=warnings,
            lt_verdict=lt_verdict,
            has_earnings_in_window=earnings_in_window,
            earnings_unknown=earnings_unknown,
            stacks_with_held=stacks, rsi=rsi,
            equity_held_pct=held_eq_pct, support_quality=sq_label,
        )
        # Suspiciously rich mid — verify moneyness before firing (rule #19:
        # surface doubt, never silently trust a possibly-ITM quote).
        if c.strike > 0 and c.premium_per_share / c.strike >= _RICH_MID_VS_STRIKE:
            cand.warnings.append(
                f"mid ${c.premium_per_share:.2f} is "
                f"{c.premium_per_share / c.strike * 100:.0f}% of strike — "
                f"verify chain moneyness before firing (may be ITM)")

        # Hard filters (step 5): earnings window always; stacking unless
        # the config override is on.
        if earnings_in_window:
            continue
        if stacks and not allow_stacking:
            continue
        if score < min_score:
            continue

        # Bug #23 — pre-trade validator against the PROJECTED post-Phase-1
        # state. Portfolio gates (cash floor / entry gates / buckets) read
        # projected values; position-shape gates run unchanged. Fail-open:
        # a validator error keeps the candidate (validation is enrichment).
        if _ptv is not None and snapshot_data and c.expiration:
            try:
                exp_d = _ptv._parse_date(c.expiration)
                if exp_d is not None:
                    vctx = _ptv.build_context_from_snapshot(
                        snapshot_data, ticker=c.ticker, strike=c.strike,
                        expiration=exp_d, option_type="PUT",
                        action="SELL_OPEN", quantity=1,
                        stress_coverage=coverage_measured)
                    ps = dict(projected_state)
                    if vctx.obligation_by_expiration:
                        ps["obligation_by_expiration"] = {
                            d: max((_f(v, 0.0) or 0.0)
                                   - closed_freed_by_exp.get(d, 0.0), 0.0)
                            for d, v in vctx.obligation_by_expiration.items()}
                    fnd = _ptv.validate_proposed_trade(
                        vctx, config, projected_state=ps)
                    # ENTRY_GATES_CLOSED does not block a coverage-IMPROVING
                    # composed rotation: deploy ≤ freed guarantees the
                    # playbook never digs the hole deeper, and the rotation
                    # modules exist precisely as the sanctioned path to
                    # rebuild coverage while staying deployed (task #20
                    # origin). Cash floor + bucket gates still apply
                    # (projected values).
                    ignorable: set = set()
                    proj_cov_c = ps.get("coverage_ratio")
                    if (proj_cov_c is not None and coverage_measured is not None
                            and proj_cov_c >= float(coverage_measured)):
                        ignorable.add("ENTRY_GATES_CLOSED")
                    # Rule #46: build_context_from_snapshot resolves the LIVE
                    # RSI, so RSI_OVERBOUGHT_PUT can now fire here — but the
                    # phase-2 gate battery right below is THIS surface's RSI
                    # authority and renders the exclusion WITH its reason
                    # (rule #24). Defer to it so an RSI-blocked candidate is
                    # never silently dropped without a ⛔ footer line.
                    ignorable.add("RSI_OVERBOUGHT_PUT")
                    if any(f.severity == _ptv.SEV_BLOCK
                           and f.rule_id not in ignorable for f in fnd):
                        continue      # blocked even post-close — excluded
                    if treat_warn_as_annotation:
                        # Task #27: WARN findings (STRIKE_NOT_AT_SUPPORT,
                        # bucket warnings, ...) surface on the ticket as ⚠
                        # notes — worth reading, never disqualifying.
                        for f in fnd:
                            if f.severity != _ptv.SEV_WARN:
                                continue
                            if f.rule_id == "EARNINGS_DATE_UNKNOWN":
                                continue  # playbook carries its own
                                # earnings-unverified annotation (rule #43)
                            cand.warnings.append(
                                f"⚠ {f.reason} ({f.rule_id})")
            except Exception:
                pass                  # fail-open — never crash the playbook
        # Rule #43 (AMZN $240P 2026-08-03) — final gate battery: live-RSI
        # enforcement, yield floor, IV-rank honesty. Applied to EVERY
        # candidate before selection; exclusions render in the warnings
        # footer with reasons (rule #24), never silently.
        battery_reasons = _phase2_gate_battery(
            cand, iv_rank=iv_rank, tech=tech,
            live_quote=(quotes_map.get(c.ticker)
                        or quotes_map.get(str(c.ticker).upper())),
            cfg=cfg, config=_cfg_top, min_score=min_score)
        if battery_reasons:
            battery_skips.append(
                f"⛔ {c.ticker} ${c.strike:g}P excluded — "
                + "; ".join(battery_reasons))
            continue
        if preclose_gated:
            # Pre-close portfolio gates would have blocked this open — it's
            # actionable ONLY because Phase 1 fires first. Say so.
            cand.setup_flags.append(_UNLOCK_TAG)
        scored.append(cand)

    scored.sort(key=lambda x: (-x.conviction_score, -x.annualized_yield_pct))

    # ── Greedy selection ─────────────────────────────────────────────────
    # Task #28 semantics: with the adaptive cap active, the cap is a HARD
    # ceiling — a candidate that would push deployed past cap × freed is
    # skipped. Flat legacy mode keeps the pre-#28 soft-stop byte-identically
    # (fit against total freed, stop once deployed ≥ target × freed).
    opens: list[ConvictionScoredCandidate] = []
    deployed = 0.0
    per_ticker: dict[str, int] = {}
    bucket_deployed: dict[str, float] = {}   # task #29 — {exp ISO: $}
    fit_budget = deploy_cap * total_freed if cap_adaptive else total_freed
    stop_budget = deploy_cap * total_freed
    # Task #29 — optional HARD per-bucket cap, measured against the
    # deployment budget (well-defined up front, unlike the final total).
    hard_bucket_limit = (hard_bucket_cap * stop_budget
                         if hard_bucket_cap is not None else None)
    remaining = list(scored)

    def _feasible(cand: ConvictionScoredCandidate) -> bool:
        if deployed + cand.collateral_required > fit_budget:
            return False              # never deploy past the budget
        if hard_bucket_limit is not None and cand.expiration and \
                bucket_deployed.get(cand.expiration, 0.0) \
                + cand.collateral_required > hard_bucket_limit:
            return False              # would over-concentrate one Friday
        n_same = per_ticker.get(cand.ticker, 0)
        if n_same >= max_same_ticker:
            # A repeat entry is allowed ONLY when it beats the best
            # remaining different-ticker candidate by 25%+ — otherwise
            # diversify. No comparison available → diversify (skip).
            if n_same >= max_same_ticker + 1:
                return False          # never more than one extra stack
            alt = next((a for a in remaining
                        if a.ticker != cand.ticker
                        and per_ticker.get(a.ticker, 0) < max_same_ticker
                        and deployed + a.collateral_required <= fit_budget),
                       None)
            if alt is None or cand.conviction_score < \
                    _SAME_TICKER_BEAT_FACTOR * alt.conviction_score:
                return False
        return True

    while remaining and len(opens) < max_opens:
        if deployed >= stop_budget:
            break
        picked = None
        for i, cand in enumerate(remaining):
            if _feasible(cand):
                picked = i
                break
        if picked is None:
            break
        # Task #29 tie-break: among feasible candidates TIED on conviction
        # score, prefer the one whose expiration bucket carries the least
        # deployment so far (stable: earlier rank wins on equal load).
        if tie_break_diverse:
            lead_score = remaining[picked].conviction_score
            best_load = bucket_deployed.get(
                remaining[picked].expiration or "", 0.0)
            for j in range(picked + 1, len(remaining)):
                cand_j = remaining[j]
                if cand_j.conviction_score != lead_score:
                    break             # sorted by score DESC — tie group over
                if not _feasible(cand_j):
                    continue
                load_j = bucket_deployed.get(cand_j.expiration or "", 0.0)
                if load_j < best_load:
                    picked, best_load = j, load_j
        cand = remaining.pop(picked)
        opens.append(cand)
        deployed += cand.collateral_required
        per_ticker[cand.ticker] = per_ticker.get(cand.ticker, 0) + 1
        if cand.expiration:
            bucket_deployed[cand.expiration] = bucket_deployed.get(
                cand.expiration, 0.0) + cand.collateral_required

    # ── Aggregates + portfolio-level warnings ────────────────────────────
    total_premium = sum(o.premium for o in opens)
    weighted_yield = (
        sum(o.annualized_yield_pct * o.collateral_required for o in opens)
        / deployed) if deployed > 0 else 0.0
    buckets: dict[str, float] = {}
    for o in opens:
        if o.expiration:
            buckets[o.expiration] = buckets.get(o.expiration, 0.0) \
                + o.collateral_required

    pb_warnings: list[str] = []
    if excluded_directive:
        pb_warnings.append(
            f"{len(excluded_directive)} directive-held winner(s) excluded "
            f"from the sweep: {', '.join(excluded_directive)} "
            f"(fable_advisor_memory.md)")
    # Task #30 — hard equity-stacking skips are NEVER silent: the reason
    # renders in the warnings footer so the user knows why the name is out.
    pb_warnings.extend(equity_hard_skips)
    # Rule #43 — gate-battery exclusions (live-RSI / yield floor / IV
    # honesty) render with reasons too; the footer is the reference note.
    pb_warnings.extend(battery_skips)
    if not opens:
        pb_warnings.append(
            "close for coverage, no qualified re-deployment today — every "
            "candidate failed a discipline gate or the conviction floor")
    if len(opens) >= 2 and deployed > 0:
        for exp, coll in sorted(buckets.items()):
            share = coll / deployed
            if share > bucket_warning_pct:
                n_in = sum(1 for o in opens if o.expiration == exp)
                pb_warnings.append(
                    f"{n_in} open(s) on {exp} = {share * 100:.0f}% of "
                    f"deployed collateral (> {bucket_warning_pct * 100:.0f}%) "
                    f"— bucket concentration to monitor")
            # Task #29 — hard diversification warning: one Friday carrying
            # the bulk of new deployment is the same trade N times over,
            # not diversification. Suggest a real alternative Friday from
            # the unselected pool when one exists — never a fabricated date.
            if share > max_bucket_share:
                alt_exps = sorted({a.expiration for a in remaining
                                   if a.expiration and a.expiration != exp})
                if alt_exps:
                    tail = (f"— consider staggering by opening the next "
                            f"trade at {_fmt_exp_compact(alt_exps[0])} "
                            f"instead")
                else:
                    tail = ("— candidate pool offers no alternative Friday "
                            "today; stagger future opens across expirations")
                pb_warnings.append(
                    f"⚠️ {_fmt_exp_compact(exp)} bucket carries "
                    f"{share * 100:.0f}% of new deployment "
                    f"(> {max_bucket_share * 100:.0f}%) {tail}")
    if opens and all(o.parkev_rating == "BUY" for o in opens):
        pb_warnings.append(
            "high-conviction only day — every selected open carries a "
            "Parkev BUY or better")
    for o in opens:
        for w in o.warnings:
            if "verify chain moneyness" in w:
                pb_warnings.append(f"⚠️ {o.ticker} ${o.strike:g}P {w}")
            elif ("equity concentration" in w or w.startswith("⚠ strike")
                  or "earnings unverified" in w):
                # Task #30/#31 + rule #43 — selected-anyway penalties surface
                # at the playbook level too, not just in the candidate JSON.
                pb_warnings.append(
                    f"⚠️ {o.ticker} ${o.strike:g}P "
                    f"{w[2:] if w.startswith('⚠ ') else w}")

    # Task #29 — book-level single-Friday obligation on the POST-playbook
    # book (surviving held puts + Phase 2 opens), reusing the rule #21
    # analyzer. Only buckets a NEW open lands in are surfaced here — pre-
    # existing concentrations are the Risk Alerts panel's job. Fail-open.
    try:
        from analysis.expiration_ladder import analyze_put_buckets
        eb_cfg = _cfg_top.get("expiration_bucket")
        eb_cfg = eb_cfg if isinstance(eb_cfg, dict) else {}
        eb_warn = _f(eb_cfg.get("warning_pct"), 0.20)
        eb_crit = _f(eb_cfg.get("critical_pct"), 0.30)
        eb_info = _f(eb_cfg.get("info_pct"), 0.10)
        open_exps = {o.expiration for o in opens if o.expiration}
        if nlv > 0 and open_exps:
            proj_book = surviving_book + [
                {"underlying": o.ticker, "position_type": "short_put",
                 "strike": o.strike, "expiration": o.expiration, "qty": 1}
                for o in opens]
            for cl in analyze_put_buckets(
                    proj_book, nlv, today=today, critical_pct=eb_crit,
                    warning_pct=eb_warn, info_pct=eb_info):
                iso = cl.expiration.isoformat()
                if iso not in open_exps:
                    continue
                phrase = (
                    f"exceeds the {eb_warn * 100:.0f}% single-Friday "
                    f"concentration threshold" if cl.pct_of_nlv >= eb_warn
                    else f"approaching the {eb_warn * 100:.0f}% "
                         f"single-Friday concentration threshold")
                pb_warnings.append(
                    f"⚠️ Post-playbook, {_fmt_exp_compact(iso)} obligation "
                    f"reaches ${cl.total_obligation:,.0f} = "
                    f"{cl.pct_of_nlv * 100:.1f}% NLV ({phrase})")
    except Exception:
        pass                          # fail-open — never crash the playbook

    # ── Coverage before/after (first-order approximation, measured only) ─
    coverage_before = None
    coverage_after = None
    try:
        from analysis.capacity_gate import coverage_ratio_from
        coverage_before = coverage_ratio_from(analytics)
    except ImportError:
        pass
    if coverage_before is not None:
        obligations = _total_put_obligations(analytics)
        if obligations and obligations > 0:
            after_obl = obligations - total_freed + deployed
            if after_obl > 0:
                if measured_cash is not None:
                    # Bug #23: freed collateral returns to cash, so the
                    # post-playbook ratio is (cash + freed) / (obligations
                    # − freed + newly deployed) — both sides move.
                    coverage_after = round(
                        (measured_cash + total_freed) / after_obl, 4)
                else:
                    # No measured cash → first-order scale of the measured
                    # ratio by the obligation change (legacy estimate).
                    coverage_after = round(
                        coverage_before * obligations / after_obl, 4)
            coverage_before = round(float(coverage_before), 4)
        else:
            coverage_before = round(float(coverage_before), 4)
            coverage_after = coverage_before

    return RotationPlaybook(
        closes=closes,
        opens=opens,
        total_freed=total_freed,
        total_collateral_deployed=deployed,
        cash_cushion_kept=total_freed - deployed,
        total_premium_collected=total_premium,
        total_realized_profit=total_realized,
        weighted_yield_pct=weighted_yield,
        bucket_concentrations=buckets,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        warnings=pb_warnings,
        deploy_cap_pct=(round(float(deploy_cap), 4) if cap_adaptive else None),
        deploy_cap_label=cap_label,
        deploy_cap_coverage=(round(float(_cov_for_cap), 4)
                             if cap_adaptive and _cov_for_cap is not None
                             else None),
        deploy_cap_next_note=cap_next_note,
    )


def _total_put_obligations(analytics) -> float | None:
    """Total short-put obligations from the stress-coverage payload
    (StressCoverage dataclass or its dict form). None when unavailable —
    the coverage-after estimate is then skipped, never fabricated."""
    sc = _analytics_get(analytics, "stress_coverage")
    if sc is None:
        return None
    val = getattr(sc, "total_put_obligations", None)
    if val is None and isinstance(sc, dict):
        val = sc.get("total_put_obligations")
    return _f(val, None)
