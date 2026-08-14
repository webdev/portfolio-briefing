"""Setup Grade — side-aware entry-TIMING grade on every transaction.

George (2026-08-10): "We need a very clear message as to when I should get
in on every transaction."

One letter grade (A / A- / B / C / D) per ticker per side, fusing the
measured inputs the pipeline already computes — never recomputed here:

    rsi       RSI(14) position vs the side's favored band
              (``rsi_discipline`` bands from briefing.yaml — rule #44)
    vol       volatility payment — TRUE chain IV rank when available
              (``analysis/chain_iv.py``), else the labeled realized-vol
              proxy; the driver string says WHICH ('IVr 78' vs 'RVr 79')
    support   (CSP) a ≥2-touch support cluster within/under the strike
              zone; (CC) a tested resistance near/above the strike
              (``analysis/support_resistance.py`` clusters, strength-scaled)
    trend     200-SMA distance + the measured long_term_verdict
    context   day color (red day favors puts, green day favors CCs —
              rule #44), earnings distance, drawdown posture

Key distinction: Parkev / CP / MV conviction answer WHAT to trade; the
Setup Grade answers WHEN (entry-timing quality). It NEVER overrides a hard
gate — where a gate blocks (earnings window, coverage, yield floor,
concentration, overlap, RSI), the grade renders beside the block reason
and the block stands. The side's RSI hard block ZEROES the grade ('—').

Missing inputs (rule #19 — never fabricate): an unmeasured component is
EXCLUDED and the remaining weights renormalize; it is listed in the
drivers as 'n/a'; ≥2 missing components cap the letter at B (never an A
on partial data). Nothing measurable → letter 'n/a' (fail closed).

Config: briefing.yaml → ``setup_grade`` (weights, letter floors, vol
floor, S/R band, earnings distances). ``enabled: false`` (the in-code
default) → every call site skips grading → byte-identical legacy output.

Single source of truth for scoring, the per-ticket note line, and the
"🏆 Best Setups Today" spotlight (top-3 CSP + top-3 CC).
"""

from __future__ import annotations

import copy
import re
from datetime import date

try:  # pipeline import context (scripts/ on sys.path)
    from analysis import rsi_discipline as _rsi_mod
except ImportError:  # pragma: no cover — direct-script context
    import rsi_discipline as _rsi_mod  # type: ignore


# ── Config ────────────────────────────────────────────────────────────────

DEFAULTS = {
    # Off by default in code — briefing.yaml turns it on. Flag off →
    # every wired surface renders byte-identical legacy output.
    "enabled": False,
    # Letter floors (score 0-100). Below "c" → D.
    "letters": {"a": 85.0, "a_minus": 78.0, "b": 65.0, "c": 50.0},
    "weights": {
        "csp": {"rsi": 0.30, "vol": 0.25, "support": 0.20,
                "trend": 0.15, "context": 0.10},
        "cc": {"rsi": 0.30, "vol": 0.25, "resistance": 0.20,
               "trend": 0.15, "context": 0.10},
    },
    # IV/RV rank at/below this scores 0; linear to 1.0 at rank 100.
    "vol_floor_rank": 40.0,
    # S/R cluster strength that earns a full support/resistance component.
    "support_full_strength": 4.0,
    # Support qualifies at/under strike×(1+band); resistance at/above
    # strike×(1−band). Mirrors support_resistance.anchor_proximity_pct.
    "strike_band_pct": 0.03,
    # How far below (CSP) / above (CC) the strike a cluster still counts
    # as "in the strike zone" rather than irrelevant.
    "support_zone_depth_pct": 0.15,
    # CSP trend component maps −span%…+span% vs 200-SMA to 0…1;
    # the CC side maps 0…+2·span%.
    "trend_span_pct": 5.0,
    "earnings_clear_days": 14,
    "earnings_near_days": 7,
    # ≥ this many unmeasured components → cap the letter at B.
    "max_missing_for_full_grade": 1,
    "spotlight": {"enabled": True, "top_n": 3},
    # B floor for green-lit NEW-OPEN tickets (George 2026-08-12: "Yes, we
    # absolutely need to fix the right recommendations for both CSPs and
    # CCs so that recommendations are A or B, not D, because I'm very much
    # relying on it."). Off in code → byte-identical legacy; briefing.yaml
    # turns it on. Applies ONLY to new opens — position management (rolls,
    # closes, collars, hedges, TP/defensive) is NEVER floor-gated.
    "actionable_floor": {"enabled": False, "min_score": 65.0},
}

# Fail-OPEN note for ungradeable tickets under the actionable floor (rule
# #19 fail direction — missing data must never silently block a trade).
GRADE_NA_NOTE = "🏁 grade n/a — verify setup manually"

_PRIME_NEEDS_RE = re.compile(r"prime needs (.+?)\.?\s*$")

# CC RSI peak sits this many points above call.favored_above (60 → 70).
_CC_PEAK_SPAN = 10.0

_SIDE_CSP = "csp"
_SIDE_CC = "cc"

_STRIKE_P_RE = re.compile(r"\$(\d+(?:\.\d+)?)P\b")
_ANN_RE = re.compile(r"(\d+(?:\.\d+)?)%\s*annual")


def load_setup_grade_config(config: dict | None) -> dict:
    """Deep-merge briefing.yaml's ``setup_grade`` block over DEFAULTS."""
    cfg = copy.deepcopy(DEFAULTS)
    raw = (config or {}).get("setup_grade") or {}
    if not isinstance(raw, dict):
        return cfg
    for key, val in raw.items():
        if key in ("letters", "spotlight", "actionable_floor") \
                and isinstance(val, dict):
            cfg[key].update(val)
        elif key == "weights" and isinstance(val, dict):
            for side, w in val.items():
                if side in cfg["weights"] and isinstance(w, dict):
                    cfg["weights"][side].update(w)
        else:
            cfg[key] = val
    return cfg


def setup_grade_enabled(config: dict | None) -> bool:
    return bool(load_setup_grade_config(config).get("enabled"))


def spotlight_enabled(config: dict | None) -> bool:
    cfg = load_setup_grade_config(config)
    return bool((cfg.get("spotlight") or {}).get("enabled", True))


def letter_for(score: float, config: dict | None = None) -> str:
    """Map a 0-100 score to its letter using the config floors."""
    letters = load_setup_grade_config(config)["letters"]
    if score >= float(letters["a"]):
        return "A"
    if score >= float(letters["a_minus"]):
        return "A-"
    if score >= float(letters["b"]):
        return "B"
    if score >= float(letters["c"]):
        return "C"
    return "D"


# ── Actionable floor (George 2026-08-12) ─────────────────────────────────


def actionable_floor_enabled(config: dict | None) -> bool:
    """True when both the grader AND the B floor are switched on."""
    cfg = load_setup_grade_config(config)
    if not cfg.get("enabled"):
        return False
    fl = cfg.get("actionable_floor") or {}
    return bool(fl.get("enabled"))


def below_actionable_floor(grade_dict: dict | None,
                           config: dict | None) -> tuple[bool, str]:
    """(is_below, demotion_note) — the B floor for green-lit NEW-OPEN tickets.

    George (2026-08-12): "Yes, we absolutely need to fix the right
    recommendations for both CSPs and CCs so that recommendations are A or
    B, not D, because I'm very much relying on it."

    Single source of truth for the floor decision AND the demotion note —
    every surface that green-lights a new-open option ticket calls this.
    Note format: '⏸ Below setup floor — C (54): <weakest components with
    measured values + config-derived targets>' (reuses the C/D wait-message
    machinery — never a hardcoded threshold in the string).

    Fail-OPEN (rule #19 direction, consistent with redeploy_path): floor
    disabled, ungraded (None), letter 'n/a', or missing score → (False, "")
    — a missing grade must never silently block; callers render
    ``GRADE_NA_NOTE`` beside the still-actionable ticket instead. The RSI
    hard block ('—') is its own gate and stands on its own — not the
    floor's demotion.
    """
    if not actionable_floor_enabled(config):
        return False, ""
    if not isinstance(grade_dict, dict):
        return False, ""
    letter = grade_dict.get("letter")
    score = grade_dict.get("score")
    if letter in (None, "n/a") or score is None:
        return False, ""              # fail-open — ungradeable stays actionable
    if letter == "—" or grade_dict.get("hard_blocked"):
        return False, ""              # the RSI hard block already gates it
    fl = load_setup_grade_config(config).get("actionable_floor") or {}
    try:
        min_score = float(fl.get("min_score", 65.0))
    except (TypeError, ValueError):
        min_score = 65.0
    try:
        score_f = float(score)
    except (TypeError, ValueError):
        return False, ""              # unmeasurable score → fail-open
    if score_f >= min_score:
        return False, ""
    # Weakest-component detail from the existing message machinery
    # ("wait; prime needs RSI 35-45 (now 50) or IVr ≥ 60 (now 15)").
    msg = str(grade_dict.get("message") or "")
    m = _PRIME_NEEDS_RE.search(msg)
    if m:
        detail = m.group(1).rstrip(".")
    else:
        detail = " · ".join(
            str(d) for d in (grade_dict.get("drivers") or [])[:3]
        ) or "setup below the actionable floor"
    note = f"⏸ Below setup floor — {letter} ({score_f:.0f}): {detail}"
    return True, note


# ── Component scorers (pure; every value measured upstream) ──────────────


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _csp_rsi_score(rsi: float, thresholds: dict | None) -> float:
    """Peak over the lower half of the put entry band (35-45 with the
    standard wheel bands), tapering to 0 at the band's upper edge (55);
    outside the band → 0. Bounds derive from config, never hardcoded."""
    lo, hi = _rsi_mod.put_entry_band(thresholds)
    peak_hi = (lo + hi) / 2.0
    if rsi < lo or rsi > hi:
        return 0.0
    if rsi <= peak_hi:
        return 1.0
    return _clamp01((hi - rsi) / (hi - peak_hi)) if hi > peak_hi else 0.0


def _cc_rsi_score(rsi: float, thresholds: dict | None) -> float:
    """Mirror: 0 at call.favored_above (60), 1.0 at favored+10 (70) and
    above — a CC is sold into STRENGTH (rule #44). Below favored → 0."""
    th = thresholds or _rsi_mod.load_thresholds(None)
    favored = float((th.get("call") or {}).get("favored_above", 60.0))
    peak = favored + _CC_PEAK_SPAN
    if rsi < favored:
        return 0.0
    if rsi >= peak:
        return 1.0
    return _clamp01((rsi - favored) / (peak - favored))


def _vol_score(iv_rank: float, cfg: dict) -> float:
    floor = float(cfg["vol_floor_rank"])
    if iv_rank <= floor:
        return 0.0
    return _clamp01((iv_rank - floor) / max(100.0 - floor, 1e-9))


def _vol_target(cfg: dict) -> float:
    """Rank where the vol component reaches 1/3 — the 'worth selling'
    read used in wait-condition messages (floor 40 → target 60)."""
    floor = float(cfg["vol_floor_rank"])
    return floor + (100.0 - floor) / 3.0


def _sr_levels(sr, side_key: str):
    """Coerce a SupportResistance to_dict() shape (or object) to a list of
    level dicts for one side. None → the S/R input is unmeasured."""
    if sr is None:
        return None
    if isinstance(sr, dict):
        levels = sr.get(side_key)
        if levels is None:
            return [] if ("supports" in sr or "resistances" in sr) else None
        return [lv for lv in levels if isinstance(lv, dict)]
    levels = getattr(sr, side_key, None)
    if levels is None:
        return None
    out = []
    for lv in levels:
        if isinstance(lv, dict):
            out.append(lv)
        else:
            out.append({
                "price": getattr(lv, "price", None),
                "touches": getattr(lv, "touches", 1),
                "strength": getattr(lv, "strength", 1.0),
            })
    return out


def _sr_component(levels, ref: float | None, side: str, cfg: dict):
    """(score, best_level) — strongest ≥2-touch cluster in the strike
    zone, strength-scaled. levels/ref unmeasured → (None, None);
    measured-but-empty → (0.0, None) — a real zero, not missing."""
    if levels is None or ref is None or ref <= 0:
        return None, None
    band = float(cfg["strike_band_pct"])
    depth = float(cfg["support_zone_depth_pct"])
    full = max(float(cfg["support_full_strength"]), 1e-9)
    best, best_score = None, 0.0
    for lv in levels:
        try:
            price = float(lv.get("price"))
            touches = int(lv.get("touches", 1) or 1)
            strength = float(lv.get("strength", 1.0) or 1.0)
        except (TypeError, ValueError):
            continue
        if touches < 2:
            continue
        if side == _SIDE_CSP:
            in_zone = ref * (1.0 - depth) <= price <= ref * (1.0 + band)
        else:
            in_zone = ref * (1.0 - band) <= price <= ref * (1.0 + depth)
        if not in_zone:
            continue
        s = _clamp01(strength / full)
        if best is None or s > best_score:
            best, best_score = lv, s
    return (best_score if best is not None else 0.0), best


def _trend_component(spot, sma_200, lt_verdict, side: str, cfg: dict):
    """(score, vs_sma_pct). LT broken/downtrend zeroes the component;
    weakening halves it. Nothing measured → (None, None)."""
    vs = None
    if spot and sma_200:
        try:
            vs = (float(spot) - float(sma_200)) / float(sma_200) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            vs = None
    verdict = str(lt_verdict or "").lower()
    span = float(cfg["trend_span_pct"])
    if vs is not None:
        if side == _SIDE_CSP:
            base = _clamp01((vs + span) / (2.0 * span))
        else:
            base = _clamp01(vs / (2.0 * span))
    else:
        base = None
    if verdict in ("broken", "downtrend"):
        return 0.0, vs
    if base is None:
        if "uptrend" in verdict:
            return 1.0, vs
        return None, vs
    if verdict == "weakening":
        return base * 0.5, vs
    return base, vs


def _context_component(day_change_pct, days_to_earnings, drawdown_pct,
                       side: str, cfg: dict):
    """(score, notes). day_change_pct is a FRACTION (quotes dayChangePct);
    drawdown_pct is PERCENT below the 52w high. Unmeasured subs are
    excluded; all subs unmeasured → (None, [])."""
    subs: list[float] = []
    notes: list[str] = []
    if day_change_pct is not None:
        try:
            move = float(day_change_pct)
        except (TypeError, ValueError):
            move = None
        if move is not None:
            good = (move < 0) if side == _SIDE_CSP else (move > 0)
            subs.append(1.0 if good else 0.0)
            color = "red day" if move < 0 else (
                "green day" if move > 0 else "flat day")
            notes.append(f"{color} {'✓' if good else '✗'}")
    if days_to_earnings is not None:
        try:
            d = int(days_to_earnings)
        except (TypeError, ValueError):
            d = None
        if d is not None:
            clear = int(cfg["earnings_clear_days"])
            near = int(cfg["earnings_near_days"])
            s = 1.0 if d >= clear else (0.0 if d <= near else 0.5)
            subs.append(s)
            notes.append(f"earnings {d}d {'✓' if s >= 1.0 else '✗'}")
    if drawdown_pct is not None:
        try:
            dd = float(drawdown_pct)
        except (TypeError, ValueError):
            dd = None
        if dd is not None:
            if side == _SIDE_CSP:
                s = (1.0 if 5.0 <= dd <= 25.0
                     else (0.6 if dd < 5.0 else (0.5 if dd <= 40.0 else 0.0)))
            else:
                s = 1.0 if dd <= 10.0 else (0.5 if dd <= 25.0 else 0.0)
            subs.append(s)
    if not subs:
        return None, notes
    return sum(subs) / len(subs), notes


# ── 💎 Prime conjunction — George's card-strict entry check ──────────────
#
# George (2026-08-14): "strengthen our algorithm by essentially validating
# that, for CSP, our [RSI] between 35 and 45, [IV] rank is greater than
# 60, support under strike — basically all the tight algorithm... should
# absolutely get incorporated into the daily briefing, and we should
# clearly see all of the good entries based on this algorithm."
#
# The weighted Setup Grade permits tradeoffs (a B can pass with a thin
# vol rank when the other legs are perfect). The prime conjunction is a
# TIER ON TOP — strict AND across every component, thresholds drawn from
# the SAME config single sources the grade uses (never re-weighted, never
# a second voice on any band):
#
#   CSP: RSI in the prime band (lower half of put_entry_band → 35-45) AND
#        TRUE chain IVr ≥ the vol target (floor 40 → 60) AND a ≥2-touch
#        support cluster AT/UNDER the strike AND trend intact AND red
#        day + earnings clear.
#   CC:  RSI ≥ prime strength (favored_above + 10 → 70) AND TRUE chain
#        IVr ≥ the vol floor (40) AND a tested (≥2-touch) resistance at
#        the strike AND trend intact AND green day + earnings clear.
#
# An RVr-proxy-only vol read can NEVER satisfy prime — the proxy is
# labeled as the reason (rule #19: the missing list carries the measured
# value vs the target for every non-prime component).


def prime_conjunction(components: dict, side: str,
                      config: dict | None = None) -> tuple[bool, list[str]]:
    """(is_prime, missing) — TRUE only when EVERY component is in its
    prime band (strict conjunction, George's paper card 2026-08-14).

    ``components`` carries the RAW measured inputs (the same kwargs the
    graders take): rsi, iv_rank, iv_rank_source, support_resistance,
    strike, spot, sma_200, lt_verdict, day_change_pct, days_to_earnings,
    plus optional ``earnings_exempt`` (basket/ETF — no print) and
    ``thresholds`` (pre-loaded rsi_discipline thresholds).

    ``missing`` lists each non-prime component with its measured value vs
    the config-derived target (rule #19 — never a bare "no"); an
    UNMEASURED component is non-prime by construction (a conjunction
    can't be verified on missing data) and is listed as such.
    """
    cfg = load_setup_grade_config(config)
    s = _SIDE_CSP if (side or "").lower() in (_SIDE_CSP, "put") else _SIDE_CC
    th = components.get("thresholds") or _rsi_mod.load_thresholds(config)
    missing: list[str] = []

    # 1 — RSI in the side's prime band.
    rsi = components.get("rsi")
    if s == _SIDE_CSP:
        lo, hi = _rsi_mod.put_entry_band(th)
        peak_hi = (lo + hi) / 2.0
        if rsi is None:
            missing.append(f"RSI n/a (prime {lo:.0f}-{peak_hi:.0f})")
        elif not (lo <= float(rsi) <= peak_hi):
            missing.append(
                f"RSI {float(rsi):.0f} (prime {lo:.0f}-{peak_hi:.0f})")
    else:
        favored = float((th.get("call") or {}).get("favored_above", 60.0))
        prime_min = favored + _CC_PEAK_SPAN
        if rsi is None:
            missing.append(f"RSI n/a (prime ≥ {prime_min:.0f})")
        elif float(rsi) < prime_min:
            missing.append(f"RSI {float(rsi):.0f} < {prime_min:.0f}")

    # 2 — vol payment: TRUE chain IVr at/over the side's prime target.
    iv = components.get("iv_rank")
    src = components.get("iv_rank_source")
    vol_target = (_vol_target(cfg) if s == _SIDE_CSP
                  else float(cfg["vol_floor_rank"]))
    if iv is None:
        missing.append(f"IVr n/a (prime ≥ {vol_target:.0f})")
    elif src != "chain":
        missing.append(
            f"RVr {float(iv):.0f} is a realized-vol proxy — TRUE chain "
            f"IVr ≥ {vol_target:.0f} required for prime")
    elif float(iv) < vol_target:
        missing.append(f"IVr {float(iv):.0f} < {vol_target:.0f}")

    # 3 — ≥2-touch S/R cluster on the RIGHT side of the strike.
    strike = components.get("strike")
    spot = components.get("spot")
    ref = strike if strike else spot
    sr_key = "supports" if s == _SIDE_CSP else "resistances"
    levels = _sr_levels(components.get("support_resistance"), sr_key)
    sr_score, sr_best = _sr_component(levels, ref, s, cfg)
    ref_word = "strike" if strike else "spot"
    if sr_score is None:
        missing.append("S/R n/a (no measured levels)")
    elif sr_best is None:
        missing.append(
            f"no ≥2-touch support under the {ref_word}" if s == _SIDE_CSP
            else f"no tested (≥2-touch) resistance at the {ref_word}")
    elif s == _SIDE_CSP and ref and float(sr_best.get("price", 0)) > float(ref):
        missing.append(
            f"support ${float(sr_best['price']):g} sits above the "
            f"${float(ref):g} {ref_word} (prime needs support under)")

    # 4 — trend intact.
    lt_verdict = components.get("lt_verdict")
    _tscore, vs = _trend_component(spot, components.get("sma_200"),
                                   lt_verdict, s, cfg)
    verdict = str(lt_verdict or "").lower()
    if verdict in ("broken", "downtrend", "weakening"):
        missing.append(f"LT trend {verdict} (prime needs intact)")
    elif vs is not None and vs < 0:
        missing.append(
            f"price {vs:+.0f}% vs 200-SMA (prime needs at/above)")
    elif vs is None and "uptrend" not in verdict:
        missing.append("trend n/a (no 200-SMA / LT read)")

    # 5 — context: day color right for the side + earnings clear.
    move = components.get("day_change_pct")
    if move is None:
        missing.append("day color n/a (no live quote)")
    else:
        try:
            move_f = float(move)
        except (TypeError, ValueError):
            move_f = None
        if move_f is None:
            missing.append("day color n/a (no live quote)")
        else:
            good = (move_f < 0) if s == _SIDE_CSP else (move_f > 0)
            if not good:
                color = ("red" if move_f < 0
                         else ("green" if move_f > 0 else "flat"))
                want = "red" if s == _SIDE_CSP else "green"
                missing.append(f"{color} day (prime wants a {want} day)")
    clear = int(cfg["earnings_clear_days"])
    if not components.get("earnings_exempt"):
        d2e = components.get("days_to_earnings")
        if d2e is None:
            missing.append(
                f"earnings date unknown (prime needs ≥ {clear}d clear)")
        else:
            try:
                d = int(d2e)
            except (TypeError, ValueError):
                d = None
            if d is None:
                missing.append(
                    f"earnings date unknown (prime needs ≥ {clear}d clear)")
            elif d < clear:
                missing.append(f"earnings {d}d away (< {clear}d clear)")

    return (not missing), missing


# ── The two public scorers ────────────────────────────────────────────────


def csp_setup(*, rsi=None, iv_rank=None, iv_rank_source=None,
              support_resistance=None, strike=None, spot=None,
              sma_200=None, lt_verdict=None, day_change_pct=None,
              days_to_earnings=None, drawdown_pct=None,
              earnings_exempt: bool = False,
              thresholds: dict | None = None,
              config: dict | None = None) -> dict:
    """Grade a NEW cash-secured-put open (entry timing only)."""
    return _grade(_SIDE_CSP, rsi=rsi, iv_rank=iv_rank,
                  iv_rank_source=iv_rank_source,
                  support_resistance=support_resistance, strike=strike,
                  spot=spot, sma_200=sma_200, lt_verdict=lt_verdict,
                  day_change_pct=day_change_pct,
                  days_to_earnings=days_to_earnings,
                  drawdown_pct=drawdown_pct,
                  earnings_exempt=earnings_exempt,
                  thresholds=thresholds, config=config)


def cc_setup(*, rsi=None, iv_rank=None, iv_rank_source=None,
             support_resistance=None, strike=None, spot=None,
             sma_200=None, lt_verdict=None, day_change_pct=None,
             days_to_earnings=None, drawdown_pct=None,
             earnings_exempt: bool = False,
             thresholds: dict | None = None,
             config: dict | None = None) -> dict:
    """Grade a NEW covered-call write (entry timing only)."""
    return _grade(_SIDE_CC, rsi=rsi, iv_rank=iv_rank,
                  iv_rank_source=iv_rank_source,
                  support_resistance=support_resistance, strike=strike,
                  spot=spot, sma_200=sma_200, lt_verdict=lt_verdict,
                  day_change_pct=day_change_pct,
                  days_to_earnings=days_to_earnings,
                  drawdown_pct=drawdown_pct,
                  earnings_exempt=earnings_exempt,
                  thresholds=thresholds, config=config)


def _grade(side: str, *, rsi, iv_rank, iv_rank_source, support_resistance,
           strike, spot, sma_200, lt_verdict, day_change_pct,
           days_to_earnings, drawdown_pct, thresholds, config,
           earnings_exempt: bool = False) -> dict:
    cfg = load_setup_grade_config(config)
    th = thresholds if thresholds is not None else _rsi_mod.load_thresholds(config)
    sr_key = "supports" if side == _SIDE_CSP else "resistances"
    sr_weight_key = "support" if side == _SIDE_CSP else "resistance"
    weights = dict(cfg["weights"][_SIDE_CSP if side == _SIDE_CSP else _SIDE_CC])

    # ── Hard-block supremacy: the side's RSI hard block ZEROES the grade.
    assess = _rsi_mod.assess(rsi, "put" if side == _SIDE_CSP else "call", th)
    if rsi is not None and assess.blocked:
        return {
            "side": side, "score": 0.0, "letter": "—", "hard_blocked": True,
            "iv_source": iv_rank_source,
            "drivers": [f"RSI {float(rsi):.0f} hard block ✗"],
            "missing": [],
            "components": {},
            "prime": False,
            "prime_missing": [f"RSI {float(rsi):.0f} hard block"],
            "message": f"🏁 Entry: — — blocked: {assess.reason}",
        }

    # ── Components (None = unmeasured → excluded + renormalized).
    rsi_score = None
    if rsi is not None:
        rsi_score = (_csp_rsi_score(float(rsi), th) if side == _SIDE_CSP
                     else _cc_rsi_score(float(rsi), th))
    vol_score = _vol_score(float(iv_rank), cfg) if iv_rank is not None else None
    ref = strike if strike else spot
    sr_score, sr_best = _sr_component(
        _sr_levels(support_resistance, sr_key), ref, side, cfg)
    trend_score, vs_sma = _trend_component(spot, sma_200, lt_verdict, side, cfg)
    ctx_score, ctx_notes = _context_component(
        day_change_pct, days_to_earnings, drawdown_pct, side, cfg)

    scores = {"rsi": rsi_score, "vol": vol_score, sr_weight_key: sr_score,
              "trend": trend_score, "context": ctx_score}
    components = {k: {"score": scores[k], "weight": weights.get(k, 0.0)}
                  for k in weights}
    missing = [k for k, v in scores.items() if v is None]
    present = {k: v for k, v in scores.items() if v is not None}

    if not present:
        return {
            "side": side, "score": None, "letter": "n/a",
            "hard_blocked": False, "iv_source": iv_rank_source,
            "drivers": [], "missing": missing, "components": components,
            "prime": False,
            "prime_missing": ["insufficient measured data (fail closed)"],
            "message": ("🏁 Entry: n/a — insufficient measured data to "
                        "grade (fail closed)."),
        }

    total_w = sum(weights[k] for k in present) or 1e-9
    score = 100.0 * sum(weights[k] * present[k] for k in present) / total_w
    letter = letter_for(score, config)
    capped = False
    if (len(missing) > int(cfg["max_missing_for_full_grade"])
            and letter in ("A", "A-")):
        letter = "B"  # never an A on partial data (rule #19)
        capped = True

    drivers, driver_by_comp = _drivers(
        side, cfg, th, rsi=rsi, rsi_score=rsi_score,
        iv_rank=iv_rank, iv_source=iv_rank_source,
        vol_score=vol_score, sr_score=sr_score,
        sr_best=sr_best, strike=strike,
        trend_score=trend_score, vs_sma=vs_sma,
        lt_verdict=lt_verdict, ctx_notes=ctx_notes,
        sr_weight_key=sr_weight_key)
    if capped:
        drivers.append(f"⚠ {len(missing)} inputs n/a — capped at B")

    message = _message(side, letter, score, cfg, th, weights, present,
                       rsi=rsi, iv_rank=iv_rank, iv_source=iv_rank_source,
                       vs_sma=vs_sma, lt_verdict=lt_verdict,
                       driver_by_comp=driver_by_comp, capped=capped,
                       n_missing=len(missing))

    # 💎 Prime conjunction (George 2026-08-14) — a TIER ON TOP of the
    # weighted grade, never a re-weighting: strict AND across every
    # component's prime band, thresholds from the same config sources.
    prime, prime_missing = prime_conjunction(
        {"rsi": rsi, "iv_rank": iv_rank, "iv_rank_source": iv_rank_source,
         "support_resistance": support_resistance, "strike": strike,
         "spot": spot, "sma_200": sma_200, "lt_verdict": lt_verdict,
         "day_change_pct": day_change_pct,
         "days_to_earnings": days_to_earnings,
         "earnings_exempt": earnings_exempt, "thresholds": th},
        side, config)

    return {
        "side": side, "score": round(score, 1), "letter": letter,
        "hard_blocked": False, "iv_source": iv_rank_source,
        "drivers": drivers, "missing": missing, "components": components,
        "prime": prime, "prime_missing": prime_missing,
        "message": message,
    }


def _iv_label(iv_source) -> str:
    return "IVr" if iv_source == "chain" else "RVr"


def _drivers(side, cfg, th, *, rsi, rsi_score, iv_rank, iv_source,
             vol_score, sr_score, sr_best, strike, trend_score, vs_sma,
             lt_verdict, ctx_notes, sr_weight_key="support"):
    """(ordered driver strings — heaviest component first,
    {component: driver string} for the message's top-driver pick)."""
    out: list[str] = []
    by_comp: dict[str, str] = {}
    # rsi
    if rsi is None:
        out.append("RSI n/a")
    elif side == _SIDE_CSP:
        if rsi_score >= 1.0:
            out.append(f"RSI {float(rsi):.0f} prime")
        elif rsi_score > 0:
            out.append(f"RSI {float(rsi):.0f} late-band")
        else:
            out.append(f"RSI {float(rsi):.0f} off-band ✗")
    else:
        if rsi_score >= 1.0:
            out.append(f"RSI {float(rsi):.0f} strong")
        elif rsi_score > 0:
            out.append(f"RSI {float(rsi):.0f} building")
        else:
            out.append(f"RSI {float(rsi):.0f} mid-range ✗")
    if rsi is not None:
        by_comp["rsi"] = out[-1]
    # vol
    if iv_rank is None:
        out.append("IV rank n/a")
    else:
        lbl = _iv_label(iv_source)
        mark = "✓" if (vol_score or 0) > 0 else "thin ✗"
        out.append(f"{lbl} {float(iv_rank):.0f} {mark}")
        by_comp["vol"] = out[-1]
    # support / resistance
    ref_word = "strike" if strike else "spot"
    if sr_score is None:
        out.append("S/R n/a")
    elif sr_best is not None:
        touches = int(sr_best.get("touches", 1) or 1)
        price = float(sr_best.get("price", 0) or 0)
        kind = "support" if side == _SIDE_CSP else "resistance"
        out.append(f"{kind} ${price:g} ({touches} touches) ✓")
    elif side == _SIDE_CSP:
        out.append(f"no support under {ref_word} ✗")
    else:
        out.append(f"no tested resistance near {ref_word} ✗")
    if sr_score is not None:
        by_comp[sr_weight_key] = out[-1]
    # trend
    if trend_score is None:
        out.append("trend n/a")
    else:
        verdict = str(lt_verdict or "").lower()
        if trend_score == 0.0 and verdict in ("broken", "downtrend"):
            out.append(f"trend ✗ (LT {verdict})")
        elif vs_sma is not None:
            mark = "✓" if trend_score >= 0.6 else "✗"
            out.append(f"trend {mark} ({vs_sma:+.0f}% vs 200-SMA)")
        else:
            out.append(f"trend {'✓' if trend_score >= 0.6 else '✗'}")
        by_comp["trend"] = out[-1]
    # context notes (already measured strings)
    out.extend(ctx_notes)
    if ctx_notes:
        by_comp["context"] = " · ".join(ctx_notes)
    return out, by_comp


def _message(side, letter, score, cfg, th, weights, present, *, rsi,
             iv_rank, iv_source, vs_sma, lt_verdict, driver_by_comp,
             capped, n_missing) -> str:
    if letter in ("A", "A-", "B"):
        # top driver = the present component contributing the MOST
        # (weight × score) — the message leads with the setup's strength,
        # never a weak heavy component (the CRWV 'good setup; RSI 64
        # off-band ✗' contradiction).
        top_comp = max(present.items(),
                       key=lambda kv: weights.get(kv[0], 0.0) * kv[1])[0]
        top = driver_by_comp.get(top_comp, "")
        qual = "strong setup" if letter in ("A", "A-") else "good setup"
        cap_seg = (f" (capped at B — {n_missing} inputs unmeasured)"
                   if capped else "")
        top_seg = f"; {top}" if top else ""
        return (f"🏁 Entry: {letter} — {qual}{cap_seg}"
                f"{top_seg}. Enter per plan.")
    # C / D — name the WEAKEST present components with measured values and
    # config-derived targets (never a hardcoded threshold in the string).
    # Only GENUINELY weak components (score < 1/3) may be named — a
    # component already past its own target reads nonsensically ("needs
    # RVr ≥ 60 (now 63)", the CDNS 2026-08-10 replay case).
    order = ["rsi", "vol", "support", "resistance", "trend", "context"]
    weakest = sorted(
        ((k, v) for k, v in present.items() if v < 1.0 / 3.0),
        key=lambda kv: (kv[1], -weights.get(kv[0], 0.0),
                        order.index(kv[0]) if kv[0] in order else 9),
    )[:2]
    conds = [_wait_condition(k, side, cfg, th, rsi=rsi, iv_rank=iv_rank,
                             iv_source=iv_source, vs_sma=vs_sma,
                             lt_verdict=lt_verdict)
             for k, _ in weakest]
    conds = [c for c in conds if c]
    joined = (" or ".join(conds) if conds
              else "a stronger overall setup (every component middling)")
    return f"🏁 Entry: {letter} — wait; prime needs {joined}."


def _wait_condition(component, side, cfg, th, *, rsi, iv_rank, iv_source,
                    vs_sma, lt_verdict) -> str:
    if component == "rsi":
        now = f" (now {float(rsi):.0f})" if rsi is not None else ""
        if side == _SIDE_CSP:
            lo, hi = _rsi_mod.put_entry_band(th)
            peak_hi = (lo + hi) / 2.0
            return f"RSI {lo:.0f}-{peak_hi:.0f}{now}"
        favored = float((th.get("call") or {}).get("favored_above", 60.0))
        return f"RSI ≥ {favored + _CC_PEAK_SPAN:.0f}{now}"
    if component == "vol":
        lbl = _iv_label(iv_source)
        now = f" (now {float(iv_rank):.0f})" if iv_rank is not None else ""
        return f"{lbl} ≥ {_vol_target(cfg):.0f}{now}"
    if component == "support":
        return "a ≥2-touch support under the strike"
    if component == "resistance":
        return "a tested (≥2-touch) resistance near the strike"
    if component == "trend":
        verdict = str(lt_verdict or "").lower()
        if verdict in ("broken", "downtrend", "weakening"):
            return f"an intact LT trend (now {verdict})"
        now = f" (now {vs_sma:+.0f}%)" if vs_sma is not None else ""
        return f"price above the 200-SMA{now}"
    if component == "context":
        return ("a red day with earnings clear" if side == _SIDE_CSP
                else "a green day with earnings clear")
    return ""


# ── Rendering helper — the one note line every surface prints ────────────


def format_grade_note(grade: dict | None, max_drivers: int = 3) -> str:
    """'**Setup Grade: B** (68/100) · RSI 41 prime · RVr 79 ✓ — 🏁 Entry:
    B — good setup; ...' — single line, ready for a sub-bullet."""
    if not grade:
        return ""
    letter = grade.get("letter")
    score = grade.get("score")
    # 💎 PRIME badge (George 2026-08-14: "we should clearly see all of
    # the good entries based on this algorithm") — rides NEXT TO the
    # grade on every surface that prints the note line.
    head = (f"**Setup Grade: {letter} 💎 PRIME**" if grade.get("prime")
            else f"**Setup Grade: {letter}**")
    if isinstance(score, (int, float)) and letter not in ("—", "n/a"):
        head += f" ({score:.0f}/100)"
    drivers = [str(d) for d in (grade.get("drivers") or [])][:max_drivers]
    seg = " · ".join([head] + drivers)
    message = grade.get("message") or ""
    return f"{seg} — {message}" if message else seg


# ── Snapshot-driven convenience (shared by the wiring call sites) ─────────


def effective_iv(ticker: str, snapshot_data: dict | None):
    """(iv_rank, source) — TRUE chain IV rank when history allows, else
    the realized-vol proxy. Source ∈ {'chain', 'rv', None}."""
    sd = snapshot_data or {}
    tk = (ticker or "").upper()
    iv_ranks = sd.get("iv_ranks") or {}
    rv = iv_ranks.get(ticker)
    if rv is None:
        rv = iv_ranks.get(tk)
    if rv is None:
        tech = (sd.get("technicals") or {}).get(ticker) or \
            (sd.get("technicals") or {}).get(tk) or {}
        rv = tech.get("iv_rank") if isinstance(tech, dict) else None
    try:
        from analysis.chain_iv import effective_iv_rank
        val, src = effective_iv_rank(tk, sd.get("chain_iv") or {}, rv)
        return val, (src if src != "none" else None)
    except ImportError:  # pragma: no cover
        return rv, ("rv" if rv is not None else None)


def days_to_earnings_for(ticker: str, snapshot_data: dict | None,
                         as_of: date | None = None):
    """Derived days-to-earnings from the snapshot's earnings calendar."""
    try:
        from analysis.earnings_guard import days_until_earnings
        return days_until_earnings(
            ticker, (snapshot_data or {}).get("earnings_calendar") or {},
            as_of or date.today())
    except Exception:
        return None


def grade_for_new_open(ticker: str, side: str, *, snapshot_data: dict,
                       strike=None, spot=None, rsi=None,
                       thresholds: dict | None = None,
                       config: dict | None = None) -> dict | None:
    """Pull every measured input from snapshot_data and grade a new open.

    ``rsi=`` overrides the snapshot value (pass the vintage-resolved live
    RSI where a surface already computed one — rule #46). Returns None when
    nothing at all is measurable (fail closed)."""
    sd = snapshot_data or {}
    tk = (ticker or "").upper()
    technicals = sd.get("technicals") or {}
    tech = technicals.get(ticker) or technicals.get(tk) or {}
    if not isinstance(tech, dict):
        tech = {}
    if rsi is None:
        rsi = tech.get("rsi_14")
    quotes = sd.get("quotes") or {}
    quote = quotes.get(ticker) or quotes.get(tk) or {}
    if spot is None:
        spot = tech.get("spot") or (
            quote.get("last") if isinstance(quote, dict) else None)
    iv_rank, iv_src = effective_iv(ticker, sd)
    deep = tech.get("deep") if isinstance(tech.get("deep"), dict) else {}
    try:
        from analysis.earnings_unknown import is_earnings_exempt
        exempt = is_earnings_exempt(tk, config)
    except Exception:
        exempt = False
    fn = csp_setup if side in (_SIDE_CSP, "put") else cc_setup
    grade = fn(
        earnings_exempt=exempt,
        rsi=rsi, iv_rank=iv_rank, iv_rank_source=iv_src,
        support_resistance=tech.get("support_resistance"),
        strike=strike, spot=spot,
        sma_200=tech.get("sma_200"),
        lt_verdict=deep.get("long_term_verdict"),
        day_change_pct=(quote.get("dayChangePct")
                        if isinstance(quote, dict) else None),
        days_to_earnings=days_to_earnings_for(ticker, sd),
        drawdown_pct=tech.get("drawdown_pct"),
        thresholds=thresholds, config=config,
    )
    if grade.get("letter") == "n/a":
        return None
    return grade


# ── 🏆 Best Setups Today spotlight ────────────────────────────────────────


def _yield_floor_pct(config: dict | None) -> float:
    """Annualized-yield floor in PERCENT (rule #44 delivered-yield floor —
    single source: rotation_playbook.playbook_min_annualized_yield)."""
    rp = (config or {}).get("rotation_playbook") or {}
    try:
        return float(rp.get("playbook_min_annualized_yield", 0.12)) * 100.0
    except (TypeError, ValueError):
        return 12.0


def _held_short_put_strikes(positions) -> dict:
    """{ticker: [strikes]} of currently HELD short puts — the rule #17
    position-aware pool the spotlight/redeploy exclusions check against."""
    out: dict[str, list] = {}
    for p in positions or []:
        if not isinstance(p, dict) or (p.get("assetType") or "") != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty") or 0)
            strike = float(p.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        und = (p.get("underlying") or "").upper()
        if qty < 0 and strike > 0 and und:
            out.setdefault(und, []).append(strike)
    return out


_CLOSE_IDENT_RE = re.compile(
    r"^([A-Z.]{1,6})_(?:PUT|CALL)_(\d+(?:\.\d+)?)(?:_\d{6,8})?$")

# Rule #46, spotlight edition (George 2026-08-14: "RSI on SNDK is 76 now...
# What kind of recommendation is this?"). The note every capped entry carries.
UNVERIFIED_RSI_NOTE = "RSI unverified this cycle"


def _cap_unverified_grade(grade: dict, config: dict | None) -> dict:
    """No A/B on an unverified RSI vintage (rule #46's no-favourable-badge,
    applied to the grade). The letter caps at C, the score caps just under
    the configured B floor (so a capped entry can never outrank a genuinely
    verified B or pass the actionable floor), and the note rides as the
    FIRST driver so it survives the spotlight's 2-driver cut."""
    g = dict(grade or {})
    letters = load_setup_grade_config(config)["letters"]
    try:
        b_floor = float(letters["b"])
    except (TypeError, ValueError):
        b_floor = 65.0
    if g.get("letter") in ("A", "A-", "B"):
        g["letter"] = "C"
    try:
        if g.get("score") is not None and float(g["score"]) >= b_floor:
            g["score"] = round(b_floor - 1.0, 1)
    except (TypeError, ValueError):
        pass
    note = f"⚠ {UNVERIFIED_RSI_NOTE}"
    g["drivers"] = [note] + [d for d in (g.get("drivers") or []) if d != note]
    msg = str(g.get("message") or "")
    if UNVERIFIED_RSI_NOTE not in msg:
        g["message"] = (f"{msg} — {note}" if msg else note)
    g["rsi_unverified"] = True
    # 💎 prime requires a VERIFIED RSI vintage (rule #46) — an unverified
    # read can never claim every check is in its prime band.
    g["prime"] = False
    g["prime_missing"] = [UNVERIFIED_RSI_NOTE]
    return g


def _vintage_adjusted_grade(side, ticker, grade, strike, res,
                            snapshot_data, config):
    """Apply the rule-#46 vintage resolution to a spotlight candidate.

    ``res`` is a ``vintage_guard.resolve_new_open_rsi`` result. Returns
    ``(grade, exclusion_reason)``:

      fresh / no res      → unchanged, no reason (fail-open — rule #19).
      live (recomputed)   → re-graded on the LIVE RSI via
                            ``grade_for_new_open(rsi=...)``; the live hard
                            block voids the stale grade → exclusion with the
                            measured live value + move (rule #24 visible).
      stale + CSP up-move → exclusion (rule #44 fail-safe: drift up with the
                            live RSI uncomputable — plausibly past the block).
      stale (other) /
      unverified          → grade caps via :func:`_cap_unverified_grade`
                            ("RSI unverified this cycle", never A/B).
    """
    if not isinstance(res, dict) or res.get("status") in (None, "fresh"):
        return grade, None
    status = res.get("status")
    move = res.get("move_pct")
    move_s = f"{move:+.1f}%" if isinstance(move, (int, float)) else "n/a"
    if status == "live":
        regraded = grade_for_new_open(
            ticker, side, snapshot_data=snapshot_data or {},
            strike=strike, spot=res.get("live_spot"), rsi=res.get("rsi"),
            config=config)
        if regraded is None:
            return grade, None          # nothing measurable — fail-open
        if regraded.get("letter") == "—" or regraded.get("hard_blocked"):
            prev = res.get("snapshot_rsi")
            prev_s = f"{float(prev):.0f}" if prev is not None else "n/a"
            return regraded, (
                f"live RSI {float(res['rsi']):.0f} hard block "
                f"(spot {move_s} since the technicals close; pre-move "
                f"RSI {prev_s} is void)")
        return regraded, None
    if status == "stale":
        if side in (_SIDE_CSP, "put") and isinstance(move, (int, float)) \
                and move > 0:
            return grade, (
                f"stale RSI on a {move_s} up-move — live RSI not "
                f"computable; new puts excluded (rule #44 fail-safe)")
        return _cap_unverified_grade(grade, config), None
    if status == "unverified":
        return _cap_unverified_grade(grade, config), None
    return grade, None


def closing_today_from_action_lines(action_list_lines) -> set:
    """Contract idents the composed action list recommends CLOSING today.
    Used so the spotlight never re-recommends selling a contract the same
    briefing tells the user to buy back (the SNDK $1230P case)."""
    try:
        from analysis.net_option_cash import _is_close_kind, actionable_blocks
    except ImportError:  # pragma: no cover — standalone use
        return set()
    return {b["ident"] for b in actionable_blocks(list(action_list_lines or []))
            if _is_close_kind(b["kind"])}


def collect_best_setups(*, new_ideas=None, long_term_opportunities=None,
                        strategy_upgrades=None, scout_results=None,
                        snapshot_data=None, capacity_tag=None,
                        gates_closed: bool = False,
                        config: dict | None = None,
                        closing_today=None) -> dict:
    """Top-N CSP + top-N CC setups across the graded universe.

    Filters (per spec): the side's RSI hard block ('—' letters excluded)
    and the delivered annualized-yield floor. Capacity-gated names STAY,
    carrying the ⏸ tag (rules #24/#41). Never padded — fewer than N
    qualify → show what exists.

    Position/concentration awareness (rule #43, 2026-08-14: the spotlight
    listed "B (66) SNDK — SELL 1× $1230P" while the user HELD that exact
    put, action #1 was CLOSE it, and SNDK sat over its 8% Tier C cap; the
    SNDK close card then offered SNDK as its own redeploy target). A CSP
    setup is EXCLUDED — with a visible reason line (rule #24) — when:
      (a) a same/near (5%) strike put is currently held (rule #17 pattern,
          via put_overlap_check against the snapshot's short puts);
      (b) the name is over its projected obligation-inclusive tier cap
          (position_tiers.projected_name_concentration, 1 new contract);
      (c) ``closing_today`` (contract idents from the composed action list)
          recommends closing that same/near-strike contract.
    Exclusions land in ``excluded_csp`` for the renderer and are absent
    from ``csp`` — so the redeploy-path A/B pool never sees them either.

    RSI vintage resolution (rule #46, 2026-08-14 SNDK: "RSI on SNDK is 76
    now... What kind of recommendation is this?" — the spotlight rendered
    "B (66) SNDK — SELL 1× $1230P … RSI 48 late-band" off the stale
    scout-cache close after SNDK moved $1,367 → $1,625 in ~2 sessions):
    every candidate's RSI resolves through
    ``vintage_guard.resolve_new_open_rsi`` BEFORE its grade may occupy a
    slot. Drift past the threshold → re-graded on the recomputed LIVE RSI
    (hard block → excluded with a visible reason, per side); drift with the
    live RSI uncomputable on an up-move → CSP excluded (rule #44
    fail-safe); unverifiable vintage → the grade caps at C with the
    "RSI unverified this cycle" note (never A/B on unverified RSI)."""
    cfg = load_setup_grade_config(config)
    top_n = int((cfg.get("spotlight") or {}).get("top_n", 3) or 3)
    floor_pct = _yield_floor_pct(config)
    pools: dict[str, dict] = {}   # (side, ticker) → best entry
    excluded: dict[str, dict] = {}  # ticker → best excluded CSP entry
    excluded_cc: dict[str, dict] = {}  # ticker → best excluded CC entry

    _snapshot = snapshot_data or {}
    _vintage_cache: dict[str, dict | None] = {}

    def _vintage(ticker) -> dict | None:
        """Memoized rule-#46 vintage resolution for a candidate ticker."""
        tk = (ticker or "").upper()
        if tk not in _vintage_cache:
            res = None
            try:
                from analysis.vintage_guard import resolve_new_open_rsi
                res = resolve_new_open_rsi(
                    tk, _snapshot.get("technicals") or {},
                    _snapshot.get("quotes") or {},
                    _snapshot.get("positions") or [], config)
            except Exception:
                res = None      # guard failure → fail-open (rule #19)
            _vintage_cache[tk] = res
        return _vintage_cache[tk]

    _positions = (snapshot_data or {}).get("positions") or []
    _held_puts = _held_short_put_strikes(_positions)
    _nlv = None
    try:
        _nlv = float(((snapshot_data or {}).get("balance") or {})
                     .get("accountValue") or 0) or None
    except (TypeError, ValueError):
        _nlv = None
    _closing: dict[str, list] = {}   # ticker → [strikes] closing today
    for ident in (closing_today or set()):
        m = _CLOSE_IDENT_RE.match(str(ident or "").upper())
        if m:
            _closing.setdefault(m.group(1), []).append(float(m.group(2)))

    def _csp_exclusion_reasons(ticker: str, strike) -> list[str]:
        """Measured exclusion reasons for a would-be CSP spotlight entry.
        Fail-open: unresolvable inputs produce no reason (rule #19)."""
        reasons: list[str] = []
        tk = (ticker or "").upper()
        if not tk or strike is None:
            return reasons
        try:
            from analysis.put_overlap_check import check_strike_overlap
        except Exception:
            check_strike_overlap = None
        if check_strike_overlap is not None:
            ov = check_strike_overlap(tk, strike, _held_puts)
            if ov.get("overlap"):
                reasons.append(f"you hold this put "
                               f"(${float(ov['existing_strike']):g}P held)")
            cv = check_strike_overlap(tk, strike, _closing)
            if cv.get("overlap"):
                reasons.append("the action list closes this contract today")
        if _nlv:
            try:
                from analysis.position_tiers import (
                    projected_name_concentration)
                proj = projected_name_concentration(
                    tk, strike, 1, _nlv, _positions, config)
            except Exception:
                proj = None
            if proj is not None and proj.over:
                reasons.append(
                    f"over the {proj.cap_pct:g}% cap "
                    f"({proj.cap_label}; projected {proj.pct:.1f}% of NLV)")
        return reasons

    def _consider(side, ticker, grade, ticket, ann_pct, deferred, source,
                  strike=None, expiration=None, dte=None, premium_mid=None):
        if not grade or grade.get("letter") in ("—", "n/a", None):
            return
        if grade.get("score") is None:
            return
        # Rule #46 (2026-08-14 SNDK): resolve the RSI vintage BEFORE this
        # grade may occupy a slot — a +19% mover kept its pre-move "RSI 48"
        # and earned a B while the live RSI was ~76 (hard block).
        _res = _vintage(ticker)
        grade, _v_reason = _vintage_adjusted_grade(
            side, ticker, grade, strike, _res, _snapshot, config)
        if _v_reason:
            # Visible exclusion (rule #24) — never a green-lit slot.
            _tk = (ticker or "").upper()
            try:
                _v_score = float((grade or {}).get("score") or 0.0)
            except (TypeError, ValueError):
                _v_score = 0.0
            _v_entry = {
                "side": side, "ticker": _tk,
                "letter": (grade or {}).get("letter") or "—",
                "score": _v_score, "ticket": ticket, "reason": _v_reason,
            }
            _v_pool = excluded if side == "csp" else excluded_cc
            _v_prev = _v_pool.get(_tk)
            if _v_prev is None or _v_entry["score"] > _v_prev["score"]:
                _v_pool[_tk] = _v_entry
            return
        if not grade or grade.get("letter") in ("—", "n/a", None) \
                or grade.get("score") is None:
            return
        # Actionable floor (George 2026-08-12): a spotlit ticket must never
        # carry the floor demotion elsewhere — one voice. Below-floor
        # setups never occupy a 🏆 slot.
        _below_fl, _ = below_actionable_floor(grade, config)
        if _below_fl:
            return
        if ann_pct is None or float(ann_pct) < floor_pct:
            return  # delivered-yield floor (rule #44) — not income
        if side == "csp":
            _reasons = _csp_exclusion_reasons(ticker, strike)
            if _reasons:
                _tk = (ticker or "").upper()
                _entry = {
                    "side": side, "ticker": _tk,
                    "letter": grade["letter"], "score": grade["score"],
                    "ticket": ticket,
                    "reason": " / ".join(_reasons),
                }
                _prev = excluded.get(_tk)
                if _prev is None or _entry["score"] > _prev["score"]:
                    excluded[_tk] = _entry
                return
        key = f"{side}:{(ticker or '').upper()}"
        entry = {
            "side": side, "ticker": (ticker or "").upper(),
            "letter": grade["letter"], "score": grade["score"],
            "ticket": ticket, "annualized_pct": round(float(ann_pct), 1),
            "drivers": list(grade.get("drivers") or [])[:2],
            "message": grade.get("message") or "",
            # 💎 prime conjunction (George 2026-08-14) — card-strict tier.
            "prime": bool(grade.get("prime")),
            "prime_missing": list(grade.get("prime_missing") or []),
            "deferred_tag": (capacity_tag if deferred and capacity_tag
                             else None),
            "deferred": bool(deferred),
            "source": source,
            # Rule #48 — the canonical entry algorithm re-evaluates every
            # pooled entry before ranking; it needs the real contract data.
            "strike": strike, "expiration": expiration, "dte": dte,
            "premium_mid": premium_mid,
        }
        prev = pools.get(key)
        if prev is None or entry["score"] > prev["score"]:
            pools[key] = entry

    # Rule #43/#14 consistency: an extended-band (RSI 60-70) put is
    # demoted to "⏸ wait for a pullback" on its OWN surface — the
    # spotlight never green-lights what the briefing itself defers.
    def _put_extended(rsi) -> bool:
        try:
            return bool(_rsi_mod.put_extended_wait(
                rsi, _rsi_mod.load_thresholds(config)))
        except Exception:
            return False

    # 1) new_ideas (CSP) — graded at generation time.
    for idea in new_ideas or []:
        if not isinstance(idea, dict) or not idea.get("setup_grade"):
            continue
        if idea.get("rsi_blocked") or idea.get("rsi_wait") \
                or _put_extended(idea.get("rsi_14")):
            continue
        grade = {"letter": idea.get("setup_grade"),
                 "score": idea.get("setup_grade_score"),
                 "drivers": idea.get("setup_grade_drivers") or [],
                 "message": idea.get("setup_grade_message") or "",
                 "prime": bool(idea.get("setup_grade_prime")),
                 "prime_missing": list(
                     idea.get("setup_grade_prime_missing") or [])}
        strike = idea.get("strike")
        exp = idea.get("expiration_pretty") or idea.get("expiration") or ""
        ticket = (f"SELL ${strike:g}P {exp}".strip()
                  if strike else (idea.get("instruction") or "")[:60])
        _consider("csp", idea.get("ticker"), grade, ticket,
                  idea.get("annualized_pct"),
                  bool(idea.get("capacity_blocked")), "income opportunity",
                  strike=idea.get("strike"),
                  expiration=idea.get("expiration"),
                  dte=idea.get("dte"), premium_mid=idea.get("mid"))

    # 2) LT_CSP opportunities — re-graded here from the same snapshot
    #    inputs (the op dict itself only carries the note inside
    #    trigger_reasons — advisor-dataclass compatibility).
    for op in long_term_opportunities or []:
        if not isinstance(op, dict):
            continue
        if (op.get("kind") or "").upper() != "LONG_DATED_CSP" \
                or op.get("skip_reason") or op.get("rsi_wait"):
            continue
        ct = op.get("concrete_trade") or ""
        sm = _STRIKE_P_RE.search(ct)
        am = _ANN_RE.search(str(op.get("yield_or_cost") or ""))
        grade = grade_for_new_open(
            (op.get("ticker") or "").upper(), "csp",
            snapshot_data=snapshot_data or {},
            strike=(float(sm.group(1)) if sm else None), config=config)
        _consider("csp", op.get("ticker"), grade,
                  ct[:70] or "LT CSP — see Long-Term Opportunities",
                  (float(am.group(1)) if am else None),
                  bool(op.get("capacity_deferred")) or gates_closed,
                  "long-term opportunity",
                  strike=(float(sm.group(1)) if sm else None),
                  dte=op.get("target_dte"))

    # 3) Covered-call writes + index CCs (CC side).
    for up in strategy_upgrades or []:
        if not isinstance(up, dict) or not up.get("setup_grade"):
            continue
        if up.get("type") not in ("write_covered_call", "index_covered_call"):
            continue
        if up.get("earnings_blocked") or up.get("rsi_blocked"):
            continue  # hard gates stand — never spotlight a blocked write
        if up.get("type") == "index_covered_call" and not up.get("writable"):
            continue
        strike = up.get("target_strike")
        dte = up.get("target_dte")
        contracts = up.get("contracts_writable")
        ticket = (f"SELL {contracts}× ${strike:g}C"
                  + (f" ({dte} DTE)" if dte is not None else ""))
        grade = {"letter": up.get("setup_grade"),
                 "score": up.get("setup_grade_score"),
                 "drivers": (up.get("setup_grade_drivers") or []),
                 "message": up.get("setup_grade_message") or "",
                 "prime": bool(up.get("setup_grade_prime")),
                 "prime_missing": list(
                     up.get("setup_grade_prime_missing") or [])}
        if not grade["drivers"] and up.get("setup_grade_line"):
            # drivers ride inside the composed line; keep it short
            grade["drivers"] = []
        _consider("cc", up.get("underlying"), grade, ticket,
                  up.get("est_annualized_pct"), False, "covered call",
                  strike=strike, dte=dte)

    # 4) Scout candidates with a live CSP ticket — graded here.
    for r in scout_results or []:
        if not isinstance(r, dict):
            continue
        if _put_extended(r.get("rsi_14")):
            continue
        q = r.get("csp_entry") or {}
        strike, mid, dte = q.get("strike"), q.get("mid"), q.get("dte")
        if not strike or not mid or not dte:
            continue
        try:
            ann = float(mid) / float(strike) * 365.0 / max(int(dte), 1) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        grade = csp_setup(
            rsi=r.get("rsi_14"), iv_rank=r.get("iv_rank"),
            iv_rank_source=("rv" if r.get("iv_rank") is not None else None),
            support_resistance=r.get("support_resistance"),
            strike=strike, spot=r.get("spot"), sma_200=r.get("sma_200"),
            days_to_earnings=r.get("days_to_earnings"),
            drawdown_pct=r.get("drawdown_pct"), config=config)
        exp = q.get("expiration") or ""
        ticket = f"SELL 1× ${float(strike):g}P exp {exp} ({dte} DTE)"
        _consider("csp", r.get("ticker"), grade, ticket, ann,
                  gates_closed, "scout candidate", strike=strike,
                  expiration=q.get("expiration"), dte=dte, premium_mid=mid)

    # Rule #48 — THE ENTRY ALGORITHM is the decision path for the spotlight:
    # no slot may green-light a ticket the canonical six-step evaluator
    # rejects. The pool entries already passed this function's own inlined
    # canonical checks (vintage, overlap, closing-today, concentration,
    # floors), so on shared inputs the evaluator agrees — a BLOCKED verdict
    # here means a step this pool did NOT run (earnings window, LT-verdict
    # gate, tenor cap, tail risk) caught the ticket. Moved to the visible
    # exclusions (rule #24), never silently dropped. Fail-open: evaluator
    # unavailable/erroring → pool unchanged (rule #19).
    try:
        from analysis.entry_algorithm import evaluate_entry as _ea_eval
    except Exception:       # pragma: no cover — import failure fails open
        _ea_eval = None
    if _ea_eval is not None:
        for key in list(pools):
            e = pools[key]
            try:
                dec = _ea_eval(
                    e["side"], e["ticker"], e.get("strike"),
                    e.get("expiration"), e.get("premium_mid"),
                    _snapshot, None, config, positions=_positions,
                    action_close_idents=closing_today,
                    dte=e.get("dte"),
                    annualized_pct=e.get("annualized_pct"))
            except Exception:
                continue    # evaluator failure never drops a slot (rule #19)
            if dec.blocked:
                pool_x = excluded if e["side"] == "csp" else excluded_cc
                x_entry = {
                    "side": e["side"], "ticker": e["ticker"],
                    "letter": e["letter"], "score": e["score"],
                    "ticket": e["ticket"], "reason": dec.primary_detail,
                }
                prev_x = pool_x.get(e["ticker"])
                if prev_x is None or x_entry["score"] > prev_x["score"]:
                    pool_x[e["ticker"]] = x_entry
                del pools[key]

    ranked = sorted(pools.values(), key=lambda e: -float(e["score"]))
    # 💎 Prime conjunction-passers (George 2026-08-14: "we should clearly
    # see all of the good entries based on this algorithm") — EVERY passer
    # across the full ranked pool (never capped at top_n; a prime entry
    # must be unmistakable even when outscored on the weighted axis).
    # Closest miss = the top-scored non-prime candidate with a MEASURED
    # missing list (rule #19 — never padded, never a fabricated reason).
    prime_entries = [e for e in ranked if e.get("prime")]
    prime_closest_miss = None
    for e in ranked:
        if not e.get("prime") and e.get("prime_missing"):
            prime_closest_miss = {
                "ticker": e["ticker"], "letter": e["letter"],
                "score": e["score"],
                "missing": list(e["prime_missing"]),
            }
            break
    return {
        "csp": [e for e in ranked if e["side"] == "csp"][:top_n],
        "cc": [e for e in ranked if e["side"] == "cc"][:top_n],
        "prime": prime_entries,
        "prime_closest_miss": prime_closest_miss,
        # Position/concentration/vintage-aware exclusions — rendered as
        # visible ⏸ lines (rule #24), never silently dropped, never in the
        # pool (so the redeploy-path A/B pool never sees them either).
        "excluded_csp": sorted(excluded.values(),
                               key=lambda e: -float(e["score"])),
        "excluded_cc": sorted(excluded_cc.values(),
                              key=lambda e: -float(e["score"])),
        "yield_floor_pct": floor_pct,
        "top_n": top_n,
    }


def render_best_setups(best: dict | None, config: dict | None = None) -> list[str]:
    """'## 🏆 Best Setups Today' — one line per setup, never padded."""
    if not best:
        return []
    csp = best.get("csp") or []
    cc = best.get("cc") or []
    floor = best.get("yield_floor_pct", 12.0)
    lines = [
        "## 🏆 Best Setups Today",
        "",
        (f"_Entry-timing quality (WHEN, not WHAT — conviction stays with "
         f"the Parkev/CP/MV reads). Graded across today's candidates, "
         f"scout, and CC-writable holdings; only names past the RSI hard "
         f"block and the {floor:.0f}% annualized yield floor. "
         f"Capacity-gated names carry ⏸ (rules #24/#41). Fewer than "
         f"{best.get('top_n', 3)} qualify → what exists is shown, never "
         f"padded._"),
        "",
    ]

    # 💎 Prime entries — George's card-strict conjunction (2026-08-14:
    # "we should clearly see all of the good entries based on this
    # algorithm"). FIRST subsection: every conjunction-passer, with the
    # same deferred/capacity tags as ever. None qualify → the closest
    # miss with its MEASURED missing list (rule #19 — never padded).
    prime = best.get("prime") or []
    lines.append("**💎 Prime entries — card-strict "
                 "(every check in its prime band):**")
    if prime:
        for e in prime:
            seg = (f"- 💎 **{e['letter']}** ({e['score']:.0f}) "
                   f"`{e['ticker']}` — {e['ticket']} · "
                   f"{e['annualized_pct']:.0f}% ann — {e['message']}")
            lines.append(seg)
            if e.get("deferred_tag"):
                lines.append(f"  - **{e['deferred_tag']}**")
    else:
        cm = best.get("prime_closest_miss")
        if cm and cm.get("missing"):
            miss = "; ".join(str(m) for m in cm["missing"])
            lines.append(f"- _none today — closest miss: "
                         f"{cm['ticker']} ({miss})_")
        else:
            lines.append("- _none today_")
    lines.append("")

    def _emit(title: str, entries: list, excluded: list | None = None) -> None:
        lines.append(title)
        if not entries and not excluded:
            lines.append("- _none qualify today_")
            lines.append("")
            return
        for e in entries:
            drivers = " · ".join(str(d) for d in (e.get("drivers") or []))
            seg = (f"- **{e['letter']}** ({e['score']:.0f}) "
                   + ("💎 " if e.get("prime") else "")
                   + f"`{e['ticker']}` — "
                   f"{e['ticket']} · {e['annualized_pct']:.0f}% ann")
            if drivers:
                seg += f" · {drivers}"
            seg += f" — {e['message']}"
            lines.append(seg)
            if e.get("deferred_tag"):
                lines.append(f"  - **{e['deferred_tag']}**")
        # Position/concentration-aware exclusions — one visible line each
        # (rule #24: never hidden), never a green-lit slot (rule #43,
        # 2026-08-14: the spotlight offered SNDK $1230P while the user held
        # that exact put, over-cap, with the action list closing it).
        for e in excluded or []:
            lines.append(f"- ⏸ {e['ticker']} {e['letter']} "
                         f"({e['score']:.0f}) — excluded: {e['reason']}")
        if not entries:
            lines.append("- _none qualify today (excluded names above)_")
        lines.append("")

    _emit("**Sell puts into weakness (CSP):**", csp,
          best.get("excluded_csp") or [])
    _emit("**Sell covered calls into strength (CC):**", cc,
          best.get("excluded_cc") or [])
    return lines


# ── Grade coverage audit (George 2026-08-12) ──────────────────────────────
#
# "Let's also add a grade to every recommendation that you're giving so
# that I know it's a good recommendation."
#
# Mirror of rsi_discipline.audit_missing_rsi: scan the RENDERED briefing
# for new-open option tickets whose card carries no Setup Grade token, so
# a future surface that composes tickets without grading them fails the
# verifier instead of shipping ungraded (the strangle-put-add bug, caught
# on the 2026-08-12 render).

# Composed NEW-OPEN option-ticket signatures. Kept tight to real ticket
# grammar so prose / context lines never false-positive:
#   "SELL 1× APP $280P exp Fri Sep 18 '26"   candidate / LT_CSP / CC cards
#   "SELL TO OPEN 1× SOFI ..."               live spread short legs
#   "Add 1× $500P exp Fri Nov 20 '26"        strangle put add
#   "BUY 1× NVDA $200C ..."                  LEAP / BTO debit tickets
# Management roll legs render as "STO N×" / "Sell-to-Open" / fenced combo
# tickets — none match these patterns by construction.
_TICKET_LINE_PATTERNS = [
    re.compile(r"\bSELL\s+\d+×"),
    re.compile(r"\bSELL TO OPEN\b"),
    re.compile(r"\bAdd\s+\d+×\s+\$\d"),
    re.compile(r"\bBUY\s+\d+×\s+[A-Z]"),
]

# Any of these within the ticket's card/block satisfies the audit:
# format_grade_note ("**Setup Grade: B** ..."), the 🏁 entry message /
# GRADE_NA_NOTE, or the B-floor demotion note.
_GRADE_TOKENS = ("Setup Grade:", "🏁", "Below setup floor")

# Blocks whose option legs are PROTECTION or position management, not
# entry timing: collar / protective-put buys, hedge adds, two-leg roll
# combos. These carry verdicts (or floors), never entry grades.
_MGMT_BLOCK_RE = re.compile(
    r"protective put|collar|hedge|BUY TO CLOSE|Buy-to-Close|ROLL ANALYSIS",
    re.I)

# Numbered action-list MANAGEMENT items (CLOSE / rolls / TP / trims /
# holds / hedges). Exempt from the grade requirement BY DESIGN — they
# carry decision tokens (⚖️ Verdict / capture % / advisor rec), not entry
# grades — but a management item with NO decision token at all is flagged
# in the second list.
_MGMT_ITEM_RE = re.compile(
    r"^\s*\d+\.\s+(?:🚨\s*)?\*\*[^*]*"
    r"\b(CLOSE|ROLL|TAKE PROFIT|TRIM|HOLD|HEDGE|REVIEW)\b")

# Decision tokens a management action item must carry somewhere in its
# block: the challenger verdict, a capture % read, a Why line, or an
# explicit advisor recommendation.
_MGMT_VERDICT_TOKENS = ("⚖️", "Verdict", "captur", "Why:", "advisor")


_NUMBERED_ITEM_RE = re.compile(r"^\s*\d+\.\s")


def _item_block(lines: list[str], i: int, max_lines: int = 24) -> tuple[int, int]:
    """A numbered action-list item's block: from the item line forward to
    the line before the next numbered item / heading / blank line, capped
    at ``max_lines`` — so a neighboring item's verdict token never bleeds
    into a bare item's window."""
    hi = i
    n = len(lines)
    while hi < n - 1 and hi - i < max_lines:
        nxt = lines[hi + 1]
        s = nxt.strip()
        if not s or s.startswith("#") or _NUMBERED_ITEM_RE.match(nxt):
            break
        hi += 1
    return i, hi


def _card_block(lines: list[str], i: int, context_lines: int) -> tuple[int, int]:
    """The contiguous card around line ``i``: expand up/down until a blank
    line or a markdown heading, capped at ``context_lines`` each way."""
    lo = i
    while (lo > 0 and i - lo < context_lines
           and lines[lo - 1].strip()
           and not lines[lo - 1].lstrip().startswith("#")):
        lo -= 1
    hi = i
    n = len(lines)
    while (hi < n - 1 and hi - i < context_lines
           and lines[hi + 1].strip()
           and not lines[hi + 1].lstrip().startswith("#")):
        hi += 1
    return lo, hi


def audit_missing_grade(md: str, context_lines: int = 8) -> dict:
    """Scan rendered briefing markdown for grade-coverage gaps.

    George (2026-08-12): "Let's also add a grade to every recommendation
    that you're giving so that I know it's a good recommendation."

    Returns ``{"new_open": [...], "management": [...]}``:

    - ``new_open`` — NEW-OPEN option ticket lines (SELL N× / SELL TO OPEN
      / strangle Add N× / BUY N× debit tickets) whose card/block carries
      no Setup Grade token (``Setup Grade:``, ``🏁``, or the ``⏸ Below
      setup floor`` demotion note). The window is the ticket's contiguous
      card, capped at ±``context_lines`` (mirroring audit_missing_rsi's
      mechanics).
    - ``management`` — numbered action-list MANAGEMENT items (CLOSE /
      EXECUTE ROLL / TAKE PROFIT / TRIM / HOLD / HEDGE / REVIEW) whose
      block carries NO decision token (⚖️ Verdict / capture % / Why: /
      advisor rec). Management lines are exempt from Setup Grades BY
      DESIGN — a roll/close/TP is a decision about an EXISTING position
      and carries a verdict, not an entry-timing grade — but a bare
      management line with neither is a rendering bug.

    Exempt from the new-open scan (not entry recommendations):
      - fenced code blocks (two-leg roll combo tickets — management);
      - italic transparency footers (lines starting with ``_``);
      - table rows (ROLL ANALYSIS candidate menus);
      - the Capital Plan / Money Plan rollups (items detailed, with
        grades, elsewhere in the briefing);
      - protection / management blocks (collar & protective-put legs,
        hedge adds, Buy-to-Close combos) — verdict-carrying, not graded.
    """
    lines = md.splitlines()
    new_open: list[str] = []
    management: list[str] = []
    in_fence = False
    in_excluded_section = False
    in_action_list = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("## "):
            in_excluded_section = ("Capital Plan" in stripped
                                   or "Money Plan" in stripped)
            in_action_list = "Action List" in stripped
        if in_excluded_section:
            continue
        if stripped.startswith("_") or stripped.startswith("|"):
            continue

        # Management-verdict check — numbered action-list items only.
        if in_action_list and _MGMT_ITEM_RE.match(line):
            lo, hi = _item_block(lines, i)
            block = " ".join(lines[lo:hi + 1])
            if not any(t in block for t in _MGMT_VERDICT_TOKENS):
                management.append(stripped)
            continue

        if not any(p.search(line) for p in _TICKET_LINE_PATTERNS):
            continue
        lo, hi = _card_block(lines, i, context_lines)
        block = " ".join(lines[lo:hi + 1])
        if _MGMT_BLOCK_RE.search(block):
            continue        # protection / roll leg — verdicts, not grades
        if not any(t in block for t in _GRADE_TOKENS):
            new_open.append(stripped)
    return {"new_open": new_open, "management": management}
