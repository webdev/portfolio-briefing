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


# ── The two public scorers ────────────────────────────────────────────────


def csp_setup(*, rsi=None, iv_rank=None, iv_rank_source=None,
              support_resistance=None, strike=None, spot=None,
              sma_200=None, lt_verdict=None, day_change_pct=None,
              days_to_earnings=None, drawdown_pct=None,
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
                  thresholds=thresholds, config=config)


def cc_setup(*, rsi=None, iv_rank=None, iv_rank_source=None,
             support_resistance=None, strike=None, spot=None,
             sma_200=None, lt_verdict=None, day_change_pct=None,
             days_to_earnings=None, drawdown_pct=None,
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
                  thresholds=thresholds, config=config)


def _grade(side: str, *, rsi, iv_rank, iv_rank_source, support_resistance,
           strike, spot, sma_200, lt_verdict, day_change_pct,
           days_to_earnings, drawdown_pct, thresholds, config) -> dict:
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

    return {
        "side": side, "score": round(score, 1), "letter": letter,
        "hard_blocked": False, "iv_source": iv_rank_source,
        "drivers": drivers, "missing": missing, "components": components,
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
    head = f"**Setup Grade: {letter}**"
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
    fn = csp_setup if side in (_SIDE_CSP, "put") else cc_setup
    grade = fn(
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


def collect_best_setups(*, new_ideas=None, long_term_opportunities=None,
                        strategy_upgrades=None, scout_results=None,
                        snapshot_data=None, capacity_tag=None,
                        gates_closed: bool = False,
                        config: dict | None = None) -> dict:
    """Top-N CSP + top-N CC setups across the graded universe.

    Filters (per spec): the side's RSI hard block ('—' letters excluded)
    and the delivered annualized-yield floor. Capacity-gated names STAY,
    carrying the ⏸ tag (rules #24/#41). Never padded — fewer than N
    qualify → show what exists."""
    cfg = load_setup_grade_config(config)
    top_n = int((cfg.get("spotlight") or {}).get("top_n", 3) or 3)
    floor_pct = _yield_floor_pct(config)
    pools: dict[str, dict] = {}   # (side, ticker) → best entry

    def _consider(side, ticker, grade, ticket, ann_pct, deferred, source):
        if not grade or grade.get("letter") in ("—", "n/a", None):
            return
        if grade.get("score") is None:
            return
        # Actionable floor (George 2026-08-12): a spotlit ticket must never
        # carry the floor demotion elsewhere — one voice. Below-floor
        # setups never occupy a 🏆 slot.
        _below_fl, _ = below_actionable_floor(grade, config)
        if _below_fl:
            return
        if ann_pct is None or float(ann_pct) < floor_pct:
            return  # delivered-yield floor (rule #44) — not income
        key = f"{side}:{(ticker or '').upper()}"
        entry = {
            "side": side, "ticker": (ticker or "").upper(),
            "letter": grade["letter"], "score": grade["score"],
            "ticket": ticket, "annualized_pct": round(float(ann_pct), 1),
            "drivers": list(grade.get("drivers") or [])[:2],
            "message": grade.get("message") or "",
            "deferred_tag": (capacity_tag if deferred and capacity_tag
                             else None),
            "deferred": bool(deferred),
            "source": source,
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
                 "message": idea.get("setup_grade_message") or ""}
        strike = idea.get("strike")
        exp = idea.get("expiration_pretty") or idea.get("expiration") or ""
        ticket = (f"SELL ${strike:g}P {exp}".strip()
                  if strike else (idea.get("instruction") or "")[:60])
        _consider("csp", idea.get("ticker"), grade, ticket,
                  idea.get("annualized_pct"),
                  bool(idea.get("capacity_blocked")), "income opportunity")

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
                  "long-term opportunity")

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
                 "message": up.get("setup_grade_message") or ""}
        if not grade["drivers"] and up.get("setup_grade_line"):
            # drivers ride inside the composed line; keep it short
            grade["drivers"] = []
        _consider("cc", up.get("underlying"), grade, ticket,
                  up.get("est_annualized_pct"), False, "covered call")

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
                  gates_closed, "scout candidate")

    ranked = sorted(pools.values(), key=lambda e: -float(e["score"]))
    return {
        "csp": [e for e in ranked if e["side"] == "csp"][:top_n],
        "cc": [e for e in ranked if e["side"] == "cc"][:top_n],
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

    def _emit(title: str, entries: list) -> None:
        lines.append(title)
        if not entries:
            lines.append("- _none qualify today_")
            lines.append("")
            return
        for e in entries:
            drivers = " · ".join(str(d) for d in (e.get("drivers") or []))
            seg = (f"- **{e['letter']}** ({e['score']:.0f}) `{e['ticker']}` — "
                   f"{e['ticket']} · {e['annualized_pct']:.0f}% ann")
            if drivers:
                seg += f" · {drivers}"
            seg += f" — {e['message']}"
            lines.append(seg)
            if e.get("deferred_tag"):
                lines.append(f"  - **{e['deferred_tag']}**")
        lines.append("")

    _emit("**Sell puts into weakness (CSP):**", csp)
    _emit("**Sell covered calls into strength (CC):**", cc)
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
