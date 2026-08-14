"""THE ENTRY ALGORITHM — the canonical six-step evaluator every new-open
recommendation passes (CLAUDE.md hard rule #48).

George (2026-08-14): "Let's make sure we definitely encode this in the
recommendation: the exact algorithm that ensures that the entry is as good
as possible. I definitely don't want a coin-flip algorithm. It really needs
to work, so use all the right steps to validate entries."

This module is a CONDUCTOR, not a new brain: every check is delegated to
the existing single-source-of-truth module and called in one fixed order.
Nothing is reimplemented here — a divergence between a surface and this
evaluator means the surface skipped a canonical step.

The six steps, in order (each one's findings recorded pass or fail, so the
decision is auditable end to end):

  STEP 1 — DATA FRESHNESS   vintage_guard.resolve_new_open_rsi (rule #47):
                            RSI + spot resolve to verified live values;
                            an unverifiable UP-move on a put side is a
                            hard data block (rule #44 fail-safe).
  STEP 2 — HARD BLOCKS      ordered: RSI hard block for the side
                            (rsi_discipline.hook, index-CC thresholds
                            honored); earnings inside the contract
                            (earnings_guard, unknown date → WARN per
                            earnings_unknown); held-put 5% strike overlap
                            (put_overlap_check, rule #40); projected
                            obligation-inclusive name concentration vs the
                            tier cap (position_tiers, rule #16 math);
                            LT-verdict broken-trend gate (lt_verdict_gate,
                            rule #39; CC side gets the secular-uptrend
                            wait); tail-risk list (config, when present);
                            new-open tenor cap (roll.max_action_tenor_days,
                            core ×3 — rule #45); contract being closed by
                            today's action list (rule #43, the SNDK case).
  STEP 3 — PAYMENT FLOORS   delivered yield ≥ playbook_min_annualized_yield
                            (12%) and premium ≥ 0.5% of collateral (rule
                            #44); iv_honesty gap-inflated-rank note; the
                            effective vol source labeled (chain_iv).
  STEP 4 — SETUP GRADE      setup_grade on the RESOLVED inputs (vintage
                            RSI/spot, measured IVr, S/R, trend, context);
                            unverified vintages cap the DISPLAYED grade
                            (never A/B on unverified RSI — rule #46).
  STEP 5 — B FLOOR          setup_grade.below_actionable_floor on the
                            measured grade → WAIT with the graded reason.
  STEP 6 — BOOK GATES       capacity_gate (stress coverage floor, rule
                            #41) → WAIT with the measured ratio; sector
                            conviction context (sector_exposure) as an
                            annotation, never a block.

Verdicts: ENTER (all steps pass) · WAIT (floors / B floor / capacity /
extended-band demotions — the full ticket may render, tagged, never
green-lit) · BLOCKED (a step-1/2 hard block — never render actionable).

Fail directions are inherited from the underlying single sources (rule
#19): missing data fails OPEN at every step that fails open today (no NLV
→ no concentration block; no RSI with no measurable drift → no RSI block;
no premium → floors unmeasured), and fails SAFE exactly where the sources
fail safe (stale up-move on a put side; unverified vintage → no A/B).

Enforcement: ``audit_conformance`` re-runs the evaluator over every
green-lit new-open ticket in the RENDERED briefing and flags any ticket
the algorithm would not mark ENTER — the '🧮 Entry Algorithm Conformance'
panel in steps/aggregate.py. A new surface MUST call ``evaluate_entry``,
never re-derive the steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

try:  # pipeline import context (scripts/ on sys.path)
    from analysis import rsi_discipline as _rsi_mod
    from analysis import setup_grade as _sg_mod
except ImportError:  # pragma: no cover — direct-script context
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from analysis import rsi_discipline as _rsi_mod
    from analysis import setup_grade as _sg_mod


SIDE_CSP = "csp"
SIDE_CC = "cc"

VERDICT_ENTER = "ENTER"
VERDICT_WAIT = "WAIT"
VERDICT_BLOCKED = "BLOCKED"

# Default new-open tenor cap mirrors roll.max_action_tenor_days (rule #45);
# core-union names get the same ×3 allowance as every other tenor surface.
DEFAULT_TENOR_CAP_DAYS = 120
CORE_TENOR_MULTIPLIER = 3

# Delivered-yield floors (rule #44) — single source of truth is the
# rotation_playbook config block; these are its in-code defaults.
_DEFAULT_MIN_ANN_YIELD = 0.12
_DEFAULT_MIN_PREM_PCT = 0.005

_STEP_NAMES = {
    1: "data_freshness",
    2: "hard_blocks",
    3: "payment_floors",
    4: "setup_grade",
    5: "actionable_floor",
    6: "book_gates",
}

_CLOSE_IDENT_RE = re.compile(
    r"^([A-Z.]{1,6})_(?:PUT|CALL)_(\d+(?:\.\d+)?)(?:_\d{6,8})?$")


@dataclass
class EntryDecision:
    """The canonical entry algorithm's decision for one proposed new open.

    ``ordered_reasons`` carries EVERY step's findings in evaluation order —
    pass, warn, wait, block, or n/a — so the decision is auditable. The
    primary reason is the FIRST block (or, absent blocks, the first wait).
    """

    verdict: str                    # ENTER | WAIT | BLOCKED
    side: str                       # csp | cc
    ticker: str
    strike: float | None
    expiration: str | None
    premium_mid: float | None
    grade: dict | None
    ordered_reasons: list[dict] = field(default_factory=list)
    one_line: str = ""
    rsi_resolution: dict | None = None
    # 💎 STEP 4½ — George's card-strict prime conjunction (2026-08-14:
    # "we should clearly see all of the good entries based on this
    # algorithm"). ANNOTATION ONLY — never changes the verdict; a
    # non-prime B is still ENTER when everything else passes. PRIME is
    # the "best possible entry" flag on top of the six steps.
    prime: bool = False

    @property
    def blocked(self) -> bool:
        return self.verdict == VERDICT_BLOCKED

    @property
    def entered(self) -> bool:
        return self.verdict == VERDICT_ENTER

    @property
    def primary(self) -> dict | None:
        """The finding that decided a non-ENTER verdict (first block, else
        first wait); None on ENTER."""
        for status in ("block", "wait"):
            for f in self.ordered_reasons:
                if f.get("status") == status:
                    return f
        return None

    @property
    def primary_detail(self) -> str:
        p = self.primary
        return str(p.get("detail") or "") if p else ""


def _find(step: int, check: str, status: str, detail: str) -> dict:
    return {"step": step, "name": _STEP_NAMES.get(step, str(step)),
            "check": check, "status": status, "detail": detail}


def _norm_side(side: str) -> str:
    s = (side or "").lower()
    if s in (SIDE_CSP, "put"):
        return SIDE_CSP
    if s in (SIDE_CC, "call"):
        return SIDE_CC
    raise ValueError(f"entry_algorithm: unknown side {side!r} "
                     f"(expected csp/put or cc/call)")


def _parse_exp(expiration) -> tuple[date | None, str | None]:
    """(date, iso) from an ISO string / date / datetime. None on failure —
    steps needing the expiration record n/a (fail-open, rule #19)."""
    if expiration is None:
        return None, None
    if isinstance(expiration, datetime):
        d = expiration.date()
        return d, d.isoformat()
    if isinstance(expiration, date):
        return expiration, expiration.isoformat()
    s = str(expiration).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d, d.isoformat()
        except ValueError:
            continue
    # Pretty forms: "Fri Sep 18 '26" / "Sep 18 '26".
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+'(\d{2})", s)
    if m:
        try:
            d = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %y").date()
            return d, d.isoformat()
        except ValueError:
            pass
    return None, None


def _closing_strikes(action_close_idents) -> dict[str, list[float]]:
    """{TICKER: [strikes]} from contract idents the action list closes today
    (same ident grammar as setup_grade.closing_today_from_action_lines)."""
    out: dict[str, list[float]] = {}
    for ident in (action_close_idents or set()):
        m = _CLOSE_IDENT_RE.match(str(ident or "").upper())
        if m:
            out.setdefault(m.group(1), []).append(float(m.group(2)))
    return out


def _tail_risk_tickers(config: dict | None) -> set[str]:
    """The configured tail-risk name list (wheelhouz gate 5 pattern). This
    repo carries no default list — absent config → empty set (n/a)."""
    raw = (config or {}).get("tail_risk")
    if isinstance(raw, dict):
        raw = raw.get("tickers")
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(t).upper() for t in raw if t}


def _tenor_cap_days(ticker: str, config: dict | None) -> int:
    try:
        cap = int(((config or {}).get("roll") or {})
                  .get("max_action_tenor_days", DEFAULT_TENOR_CAP_DAYS))
    except (TypeError, ValueError):
        cap = DEFAULT_TENOR_CAP_DAYS
    try:
        from analysis.position_tiers import core_union
        core = core_union(config or {})
    except Exception:
        core = {str(t).upper() for t in
                ((config or {}).get("core_positions") or [])}
    if (ticker or "").upper() in core:
        cap *= CORE_TENOR_MULTIPLIER
    return cap


def evaluate_entry(
    side: str,
    ticker: str,
    strike,
    expiration,
    premium_mid,
    snapshot_data: dict | None,
    analytics,
    config: dict | None,
    positions: list | None = None,
    action_close_idents=None,
    *,
    contracts: int = 1,
    dte=None,
    annualized_pct=None,
    parkev_rec: dict | None = None,
    sector_pcts: dict | None = None,
    as_of: date | None = None,
) -> EntryDecision:
    """Run the canonical six-step entry algorithm on one proposed new open.

    Args (positional, per the canonical signature):
        side: "csp"/"put" or "cc"/"call" — the new-open wheel side.
        ticker / strike / expiration / premium_mid: the proposed contract
            (expiration ISO string or date; any may be None — the steps
            that need them record n/a and fail open, rule #19).
        snapshot_data: the briefing snapshot (technicals / quotes /
            positions / balance / earnings_calendar / chain_iv / iv_ranks).
        analytics: anything capacity_gate.coverage_ratio_from understands
            (analytics dict / GateState / StressCoverage) — step 6.
        config: briefing.yaml config dict.
        positions: overrides snapshot_data["positions"] when provided.
        action_close_idents: contract idents today's action list closes
            (setup_grade.closing_today_from_action_lines output).

    Keyword-only conveniences: ``contracts`` (concentration projection,
    default 1), ``dte`` (overrides the expiration-derived day count —
    pass the measured chain DTE for determinism), ``annualized_pct``
    (pre-measured delivered yield when premium/dte aren't at hand),
    ``parkev_rec`` (full third-party rec dict for the LT-gate override),
    ``sector_pcts`` (assignment-adjusted sector % map), ``as_of``.

    Returns an :class:`EntryDecision` — deterministic and pure: same
    inputs, same decision, byte-identical ``ordered_reasons``.
    """
    sd = snapshot_data or {}
    s = _norm_side(side)
    tk = (ticker or "").upper()
    today = as_of or date.today()
    positions = positions if positions is not None else (sd.get("positions") or [])
    technicals = sd.get("technicals") or {}
    tech = technicals.get(tk) or technicals.get(ticker) or {}
    tech = tech if isinstance(tech, dict) else {}
    th = _rsi_mod.load_thresholds(config)

    try:
        strike_f = float(strike) if strike is not None else None
    except (TypeError, ValueError):
        strike_f = None
    try:
        premium_f = float(premium_mid) if premium_mid is not None else None
    except (TypeError, ValueError):
        premium_f = None

    exp_date, exp_iso = _parse_exp(expiration)
    dte_days = None
    try:
        dte_days = int(dte) if dte is not None else None
    except (TypeError, ValueError):
        dte_days = None
    if dte_days is None and exp_date is not None:
        dte_days = (exp_date - today).days
    if exp_date is None and dte_days is not None and dte_days > 0:
        # A measured DTE without an ISO expiration still lets the
        # earnings/tenor checks run on a derived-from-real-data date.
        exp_date = today + timedelta(days=dte_days)
        exp_iso = exp_date.isoformat()

    reasons: list[dict] = []

    # ── STEP 1 — DATA FRESHNESS (rule #47) ────────────────────────────────
    res = None
    try:
        from analysis.vintage_guard import resolve_new_open_rsi
        res = resolve_new_open_rsi(tk, technicals, sd.get("quotes") or {},
                                   positions, config)
    except Exception:
        res = None      # guard failure → fail-open (rule #19)
    rsi_val = tech.get("rsi_14")
    spot_val = tech.get("spot")
    vintage_capped = False
    if isinstance(res, dict):
        rsi_val = res.get("rsi")
        if res.get("live_spot") is not None:
            spot_val = res.get("live_spot")
        status = res.get("status")
        move = res.get("move_pct")
        move_s = f"{move:+.1f}%" if isinstance(move, (int, float)) else "n/a"
        if status in (None, "fresh"):
            reasons.append(_find(1, "vintage", "pass",
                                 "RSI/spot vintage verified this cycle"))
        elif status == "live":
            reasons.append(_find(
                1, "vintage", "pass",
                f"drift {move_s} — RSI recomputed live "
                f"({res.get('note') or 'Wilder, live price as current bar'})"))
        elif status == "stale":
            if s == SIDE_CSP and isinstance(move, (int, float)) and move > 0:
                reasons.append(_find(
                    1, "vintage", "block",
                    f"stale RSI on a {move_s} up-move — live RSI not "
                    f"computable; new puts excluded (rule #44 fail-safe)"))
            else:
                vintage_capped = True
                reasons.append(_find(
                    1, "vintage", "warn",
                    f"stale RSI (drift {move_s}, live RSI not computable) — "
                    f"grade capped, favourable reads withheld (rule #46)"))
        elif status == "unverified":
            vintage_capped = True
            reasons.append(_find(
                1, "vintage", "warn",
                "no live quote this cycle — RSI vintage unverifiable; "
                "grade capped, favourable reads withheld (rule #46)"))
    else:
        reasons.append(_find(1, "vintage", "n/a",
                             "vintage guard unavailable — fail-open"))

    # ── STEP 2 — HARD BLOCKS (ordered) ────────────────────────────────────
    # 2a — the side's RSI hard block (index-CC thresholds honored).
    th_side = th
    if s == SIDE_CC:
        try:
            from analysis.position_tiers import (is_index_cc_ticker,
                                                 index_cc_rsi_thresholds)
            if is_index_cc_ticker(tk, config):
                th_side = index_cc_rsi_thresholds(th, config)
        except Exception:
            th_side = th
    rv = _rsi_mod.hook("put" if s == SIDE_CSP else "call", rsi_val, th_side)
    if rv.removed:
        reasons.append(_find(2, "rsi_hard_block", "block", rv.reason))
    else:
        reasons.append(_find(
            2, "rsi_hard_block", "pass",
            rv.tag if rsi_val is not None
            else "RSI unmeasured — no RSI gate applied (fail-open)"))
        # Extended-band / mid-range demotions (rules #43/#11) — WAIT.
        if s == SIDE_CSP:
            wait = _rsi_mod.put_extended_wait(rsi_val, th_side)
            if wait:
                reasons.append(_find(2, "rsi_extended_wait", "wait", wait))
        elif rsi_val is not None:
            a = _rsi_mod.assess(rsi_val, "call", th_side)
            if a.zone == "caution":
                reasons.append(_find(
                    2, "rsi_midrange_wait", "wait",
                    f"⏸ {a.reason} (mid-range CC writes wait for strength "
                    f"— rule #11)"))

    # 2b — earnings inside the contract (unknown date → WARN, rule #43).
    cal = sd.get("earnings_calendar") or {}
    if exp_iso is None:
        reasons.append(_find(2, "earnings_window", "n/a",
                             "no expiration to test — fail-open"))
    else:
        exempt = False
        try:
            from analysis.earnings_unknown import is_earnings_exempt
            exempt = is_earnings_exempt(tk, config)
        except Exception:
            exempt = False
        if exempt:
            reasons.append(_find(2, "earnings_window", "pass",
                                 "basket/ETF — no earnings print"))
        elif not cal.get(tk) and not cal.get(ticker):
            reasons.append(_find(
                2, "earnings_window", "warn",
                "earnings date unknown — verify the print is outside the "
                "contract before opening (EARNINGS_DATE_UNKNOWN)"))
        else:
            try:
                from analysis.earnings_guard import (check_earnings_conflict,
                                                     format_new_open_block)
                chk = check_earnings_conflict(tk if cal.get(tk) else ticker,
                                              exp_iso, cal, today)
            except Exception:
                chk = None
            if chk is None:
                reasons.append(_find(2, "earnings_window", "n/a",
                                     "earnings check unavailable — fail-open"))
            elif chk.get("spans_expiration"):
                reasons.append(_find(
                    2, "earnings_window", "block",
                    format_new_open_block(tk, chk, exp_iso)))
            else:
                reasons.append(_find(
                    2, "earnings_window", "pass",
                    chk.get("message") or "earnings clear of the contract"))

    # 2c — held-put 5% strike overlap (rule #40; CSP only).
    if s == SIDE_CSP and strike_f is not None:
        try:
            from analysis.put_overlap_check import check_strike_overlap
            held = _sg_mod._held_short_put_strikes(positions)
            ov = check_strike_overlap(tk, strike_f, held)
        except Exception:
            ov = {"overlap": False}
        if ov.get("overlap"):
            reasons.append(_find(
                2, "held_put_overlap", "block",
                f"you already hold a {tk} "
                f"${float(ov['existing_strike']):g}P within 5% of this "
                f"strike — the same trade, not a new one (rule #40)"))
        else:
            reasons.append(_find(2, "held_put_overlap", "pass",
                                 "no held short put within the 5% band"))
    else:
        reasons.append(_find(2, "held_put_overlap", "n/a",
                             "CC side / no strike — overlap not applicable"))

    # 2d — projected obligation-inclusive name concentration (rule #16 math;
    #      CSP only — CC writes are share-backed).
    if s == SIDE_CSP and strike_f is not None:
        nlv = None
        try:
            nlv = float((sd.get("balance") or {}).get("accountValue") or 0) or None
        except (TypeError, ValueError):
            nlv = None
        proj = None
        if nlv:
            try:
                from analysis.position_tiers import (
                    projected_name_concentration, size_warning_line)
                proj = projected_name_concentration(
                    tk, strike_f, max(int(contracts or 1), 1), nlv,
                    positions, config)
            except Exception:
                proj = None
        if proj is None:
            reasons.append(_find(2, "name_concentration", "n/a",
                                 "NLV/strike unmeasured — fail-open"))
        elif proj.over:
            reasons.append(_find(
                2, "name_concentration", "block",
                size_warning_line(proj)
                or f"projected {proj.pct:.1f}% of NLV over the "
                   f"{proj.cap_label}"))
        else:
            reasons.append(_find(
                2, "name_concentration", "pass",
                f"projected {proj.pct:.1f}% of NLV within the "
                f"{proj.cap_label}"))
    else:
        reasons.append(_find(2, "name_concentration", "n/a",
                             "CC side / no strike — obligation unchanged"))

    # 2e — LT-verdict broken-trend gate (rule #39; CC → secular-uptrend wait).
    if s == SIDE_CSP:
        try:
            from analysis.lt_verdict_gate import check_lt_verdict_gate
            gate = check_lt_verdict_gate(tk, sd, parkev_rec)
        except Exception:
            gate = {"pass": True, "warning": None}
        if not gate.get("pass", True):
            reasons.append(_find(2, "lt_verdict", "block",
                                 gate.get("reason") or "LT verdict gate"))
        elif gate.get("warning"):
            reasons.append(_find(2, "lt_verdict", "warn", gate["warning"]))
        else:
            reasons.append(_find(
                2, "lt_verdict", "pass",
                f"LT verdict `{gate.get('verdict') or 'unmeasured'}` — "
                f"no broken-trend condition"))
    else:
        try:
            from analysis.lt_verdict_gate import cc_secular_uptrend_wait
            cc_wait = cc_secular_uptrend_wait(tk, sd)
        except Exception:
            cc_wait = None
        if cc_wait:
            reasons.append(_find(2, "lt_verdict", "wait", cc_wait))
        else:
            reasons.append(_find(2, "lt_verdict", "pass",
                                 "no secular-uptrend cap conflict"))

    # 2f — tail-risk list (config-driven; absent config → n/a).
    tail = _tail_risk_tickers(config)
    if not tail:
        reasons.append(_find(2, "tail_risk", "n/a",
                             "no tail-risk list configured"))
    elif s == SIDE_CSP and tk in tail:
        reasons.append(_find(
            2, "tail_risk", "block",
            f"{tk} is on the tail-risk list — discrete-event names are "
            f"not eligible for new put-sales"))
    else:
        reasons.append(_find(2, "tail_risk", "pass",
                             "not a tail-risk name"))

    # 2g — new-open tenor cap (rule #45 discipline; core ×3).
    if dte_days is None:
        reasons.append(_find(2, "tenor_cap", "n/a",
                             "no expiration/DTE to test — fail-open"))
    else:
        cap = _tenor_cap_days(tk, config)
        if dte_days > cap:
            reasons.append(_find(
                2, "tenor_cap", "block",
                f"{dte_days}d tenor exceeds the {cap}d new-open cap "
                f"(roll.max_action_tenor_days; core ×3)"))
        else:
            reasons.append(_find(2, "tenor_cap", "pass",
                                 f"{dte_days}d within the {cap}d cap"))

    # 2h — contract being closed by today's action list (rule #43, SNDK).
    closing = _closing_strikes(action_close_idents)
    if s == SIDE_CSP and strike_f is not None and closing:
        try:
            from analysis.put_overlap_check import check_strike_overlap
            cv = check_strike_overlap(tk, strike_f, closing)
        except Exception:
            cv = {"overlap": False}
        if cv.get("overlap"):
            reasons.append(_find(
                2, "closing_today", "block",
                "the action list closes this same/near-strike contract "
                "today — don't re-open what the briefing tells you to "
                "buy back"))
        else:
            reasons.append(_find(2, "closing_today", "pass",
                                 "not being closed by today's action list"))
    else:
        reasons.append(_find(2, "closing_today",
                             "pass" if not closing else "n/a",
                             "no close-list conflict"))

    # ── STEP 3 — PAYMENT FLOORS (rule #44) ────────────────────────────────
    rp = (config or {}).get("rotation_playbook") \
        if isinstance(config, dict) else None
    rp = rp if isinstance(rp, dict) else {}
    try:
        min_ann = float(rp.get("playbook_min_annualized_yield",
                               _DEFAULT_MIN_ANN_YIELD))
    except (TypeError, ValueError):
        min_ann = _DEFAULT_MIN_ANN_YIELD
    try:
        min_prem_pct = float(rp.get("playbook_min_premium_pct_of_collateral",
                                    _DEFAULT_MIN_PREM_PCT))
    except (TypeError, ValueError):
        min_prem_pct = _DEFAULT_MIN_PREM_PCT
    ann_pct = None
    prem_pct = None
    if premium_f and strike_f and dte_days and premium_f > 0 \
            and strike_f > 0 and dte_days > 0:
        prem_pct = premium_f / strike_f
        ann_pct = prem_pct * 365.0 / dte_days * 100.0
    elif annualized_pct is not None:
        try:
            ann_pct = float(annualized_pct)
        except (TypeError, ValueError):
            ann_pct = None
    if ann_pct is None:
        reasons.append(_find(3, "yield_floor", "n/a",
                             "premium/DTE unmeasured — floors not testable "
                             "(fail-open)"))
    elif ann_pct < min_ann * 100.0 or \
            (prem_pct is not None and prem_pct < min_prem_pct):
        if ann_pct < min_ann * 100.0:
            why = f"{ann_pct:.1f}% ann < {min_ann * 100:.0f}% floor"
        else:
            why = (f"{prem_pct * 100:.2f}% of collateral < "
                   f"{min_prem_pct * 100:.1f}% floor")
        reasons.append(_find(
            3, "yield_floor", "wait",
            f"⏸ premium too thin — {why}; not income (rule #44)"))
    else:
        reasons.append(_find(3, "yield_floor", "pass",
                             f"delivered {ann_pct:.1f}% ann ≥ "
                             f"{min_ann * 100:.0f}% floor"))

    iv_rank, iv_src = _sg_mod.effective_iv(tk, sd)
    if iv_rank is None:
        reasons.append(_find(3, "vol_source", "n/a",
                             "no vol rank measured this cycle"))
    else:
        lbl = ("chain IVr (true implied)" if iv_src == "chain"
               else "RVr (realized-vol proxy)")
        reasons.append(_find(3, "vol_source", "pass",
                             f"vol source: {lbl} {float(iv_rank):.0f}"))
    try:
        from analysis.iv_honesty import (claimed_fat_but_thin,
                                         detect_recent_gap, honest_iv_text,
                                         load_iv_gap_config)
        if claimed_fat_but_thin(iv_rank, ann_pct, config):
            gap = detect_recent_gap(
                tech, load_iv_gap_config(config)["gap_threshold_pct"])
            reasons.append(_find(3, "iv_honesty", "warn",
                                 honest_iv_text(iv_rank, gap, ann_pct)))
    except Exception:
        pass

    # ── STEP 4 — SETUP GRADE on the RESOLVED inputs ───────────────────────
    grade_raw = None
    try:
        grade_raw = _sg_mod.grade_for_new_open(
            tk, s, snapshot_data=sd, strike=strike_f, spot=spot_val,
            rsi=rsi_val, thresholds=th, config=config)
    except Exception:
        grade_raw = None
    grade = grade_raw
    if grade_raw is None:
        reasons.append(_find(4, "setup_grade", "n/a",
                             _sg_mod.GRADE_NA_NOTE))
    elif grade_raw.get("hard_blocked") or grade_raw.get("letter") == "—":
        # Defensive — step 2a fires first on the same resolved RSI.
        reasons.append(_find(4, "setup_grade", "block",
                             grade_raw.get("message") or "RSI hard block"))
    else:
        if vintage_capped:
            try:
                grade = _sg_mod._cap_unverified_grade(grade_raw, config)
            except Exception:
                grade = grade_raw
        detail = _sg_mod.format_grade_note(grade, max_drivers=2) \
            or f"grade {grade.get('letter')}"
        reasons.append(_find(4, "setup_grade", "pass", detail))

    # ── STEP 4½ — 💎 PRIME conjunction (annotation ONLY, never a gate) ────
    # George (2026-08-14): "strengthen our algorithm by essentially
    # validating that, for CSP, our [RSI] between 35 and 45, [IV] rank is
    # greater than 60, support under strike — basically all the tight
    # algorithm... we should clearly see all of the good entries based on
    # this algorithm." The card-strict conjunction rides in the findings
    # with each non-prime component's measured value vs target (rule #19);
    # it NEVER changes the verdict — a non-prime B is still ENTER.
    prime_flag = False
    if grade is None:
        reasons.append(_find(4, "prime_conjunction", "n/a",
                             "ungraded — prime conjunction unmeasured"))
    elif grade.get("hard_blocked") or grade.get("letter") == "—":
        reasons.append(_find(4, "prime_conjunction", "n/a",
                             "RSI hard block — prime not applicable"))
    else:
        prime_flag = bool(grade.get("prime"))
        p_missing = [str(m) for m in (grade.get("prime_missing") or [])]
        if prime_flag:
            reasons.append(_find(
                4, "prime_conjunction", "pass",
                "💎 PRIME — every check in its prime band "
                "(card-strict conjunction)"))
        elif p_missing:
            reasons.append(_find(
                4, "prime_conjunction", "pass",
                "not prime — " + "; ".join(p_missing)))
        else:
            reasons.append(_find(4, "prime_conjunction", "n/a",
                                 "prime conjunction unmeasured on this "
                                 "grade"))

    # ── STEP 5 — B FLOOR (George 2026-08-12) ──────────────────────────────
    # The floor decides on the MEASURED grade; when the only failure is the
    # rule-#46 unverified cap, the ticket keeps rendering (fail-open, the
    # canonical unverified treatment) but carries the capped-grade warning.
    below, note = _sg_mod.below_actionable_floor(grade_raw, config)
    if below:
        reasons.append(_find(5, "actionable_floor", "wait", note))
    elif grade is not None and grade is not grade_raw \
            and grade.get("rsi_unverified"):
        reasons.append(_find(
            5, "actionable_floor", "warn",
            f"grade capped at {grade.get('letter')} — "
            f"{_sg_mod.UNVERIFIED_RSI_NOTE} (rule #46); not A/B eligible"))
    elif grade_raw is None:
        reasons.append(_find(5, "actionable_floor", "n/a",
                             "ungraded — floor not testable (fail-open)"))
    else:
        reasons.append(_find(
            5, "actionable_floor", "pass",
            f"grade {grade_raw.get('letter')} "
            f"({float(grade_raw.get('score') or 0):.0f}) clears the floor"))

    # ── STEP 6 — BOOK GATES ───────────────────────────────────────────────
    try:
        from analysis.capacity_gate import (capacity_deferred_tag,
                                            coverage_ratio_from,
                                            min_coverage_ratio)
        tag = capacity_deferred_tag(analytics, config)
        ratio = coverage_ratio_from(analytics)
    except Exception:
        tag, ratio = None, None
    if tag:
        reasons.append(_find(6, "capacity", "wait", tag))
    elif ratio is None:
        reasons.append(_find(6, "capacity", "n/a",
                             "stress coverage unmeasured — fail-open "
                             "(rule #41)"))
    else:
        try:
            floor = min_coverage_ratio(config)
        except Exception:
            floor = 0.50
        reasons.append(_find(6, "capacity", "pass",
                             f"stress coverage {ratio:.2f}× ≥ "
                             f"{floor:.2f}× floor"))
    # Sector conviction — annotation only, never a block (by design).
    sector_note = None
    try:
        from analysis import sector_exposure as _sx
        pcts = sector_pcts
        if pcts is None and isinstance(analytics, dict):
            exp = analytics.get("sector_exposure")
            if exp is not None:
                pcts = _sx.assignment_pcts(exp)
        if pcts:
            _delta, sector_note = _sx.sector_conviction_adjustment(
                tk, pcts, config)
    except Exception:
        sector_note = None
    if sector_note:
        reasons.append(_find(6, "sector_context", "warn"
                             if "over" in sector_note else "pass",
                             sector_note))
    else:
        reasons.append(_find(6, "sector_context", "n/a",
                             "sector context unmeasured / feature off"))

    # ── Verdict ───────────────────────────────────────────────────────────
    verdict = VERDICT_ENTER
    if any(f["status"] == "block" for f in reasons):
        verdict = VERDICT_BLOCKED
    elif any(f["status"] == "wait" for f in reasons):
        verdict = VERDICT_WAIT

    dec = EntryDecision(
        verdict=verdict, side=s, ticker=tk, strike=strike_f,
        expiration=exp_iso, premium_mid=premium_f, grade=grade,
        ordered_reasons=reasons, rsi_resolution=res, prime=prime_flag)
    dec.one_line = _one_line(dec, ann_pct)
    return dec


def _ticket_frag(dec: EntryDecision) -> str:
    if dec.strike is None:
        return dec.ticker
    suffix = "P" if dec.side == SIDE_CSP else "C"
    frag = f"{dec.ticker} ${dec.strike:g}{suffix}"
    if dec.expiration:
        frag += f" exp {dec.expiration}"
    return frag


def _one_line(dec: EntryDecision, ann_pct) -> str:
    frag = _ticket_frag(dec)
    p = dec.primary
    if dec.verdict == VERDICT_BLOCKED:
        return (f"⛔ BLOCKED — {frag}: {p.get('detail')} "
                f"(step {p.get('step')}: {p.get('check')})")
    if dec.verdict == VERDICT_WAIT:
        return (f"⏸ WAIT — {frag}: {p.get('detail')} "
                f"(step {p.get('step')}: {p.get('check')})")
    g = dec.grade or {}
    letter = g.get("letter")
    score = g.get("score")
    if letter and score is not None:
        badge = " 💎 PRIME" if dec.prime else ""
        head = f"✅ ENTER — {letter}{badge} ({float(score):.0f}) {frag}"
    else:
        head = f"✅ ENTER — {frag} · {_sg_mod.GRADE_NA_NOTE}"
    if ann_pct is not None:
        head += f" · {float(ann_pct):.0f}% ann"
    return head + " — all six steps pass"


# ─────────────────────────────────────────────────────────────────────────
# Conformance verifier — no surface may green-light what the algorithm
# rejects (the enforcement half of rule #48).
# ─────────────────────────────────────────────────────────────────────────

# A green-lit new-open SHORT-premium ticket line: a SELL marker plus a
# $<strike>P/C token. BUY debit tickets (LEAPs, protection) are out of
# scope — the algorithm evaluates short-premium opens.
_SELL_MARK_RE = re.compile(r"\bSELL(?:\s+TO\s+OPEN)?\b|\bSell-to-Open\b",
                           re.I)
_STRIKE_TOKEN_RE = re.compile(r"\$(\d+(?:\.\d+)?)([PC])\b")
_TICKER_BEFORE_STRIKE_RE = re.compile(
    r"\b\d+×\s+([A-Z][A-Z0-9]{0,4})\s+\$\d")
_TICKER_BACKTICK_RE = re.compile(r"`([A-Z][A-Z0-9]{0,4})`")
_TICKER_BOLD_RE = re.compile(r"\*\*([A-Z][A-Z0-9]{0,4})\*\*")
_PREMIUM_RE = re.compile(r"(?:@|mid)\s*\$(\d+(?:\.\d+)?)", re.I)
_DTE_RE = re.compile(r"\((\d+)\s*DTE\)")
_ISO_IN_LINE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Markers that mean the ticket is ALREADY demoted / not green-lit.
_NOT_GREEN_MARKERS = ("⏸", "⛔", "🚫", "Deferred", "excluded:",
                      "Below setup floor", "not actionable")
# Non-ticker ALLCAPS tokens the loose ticker regexes may catch.
_NON_TICKERS = frozenset({
    "SELL", "BUY", "OPEN", "CLOSE", "STO", "BTC", "DTE", "RSI", "CSP",
    "CC", "OTM", "ITM", "ATM", "NLV", "IVR", "RVR", "EXP", "ANN", "PUT",
    "CALL", "LEAP", "GTC", "WAIT", "HOLD", "TRIM", "ROLL", "NEW", "LT",
})


def _line_ticker(line: str, block: list[str]) -> str | None:
    """High-confidence ticker for a ticket line: '1× TK $' on the line
    itself, else a backtick/bold token on the line, else the nearest such
    token looking BACK through the card block. None → skip (never guess)."""
    m = _TICKER_BEFORE_STRIKE_RE.search(line)
    if m and m.group(1) not in _NON_TICKERS:
        return m.group(1)
    for rx in (_TICKER_BACKTICK_RE, _TICKER_BOLD_RE):
        for tok in rx.findall(line):
            if tok not in _NON_TICKERS:
                return tok
    for prev in reversed(block):
        if prev is line:
            continue
        for rx in (_TICKER_BACKTICK_RE, _TICKER_BOLD_RE,
                   _TICKER_BEFORE_STRIKE_RE):
            for tok in rx.findall(prev):
                if tok not in _NON_TICKERS:
                    return tok
    return None


def audit_conformance(
    full_md: str,
    snapshot_data: dict | None,
    analytics,
    config: dict | None,
    action_close_idents=None,
    as_of: date | None = None,
) -> list[dict]:
    """Re-run the canonical algorithm over every GREEN-LIT new-open ticket
    in the rendered briefing; return the tickets it would NOT mark ENTER.

    Green-lit = a SELL ticket line with a $<strike>P/C token whose card
    block carries no demotion marker (⏸ / ⛔ / 🚫 / Deferred / excluded) and
    that sits outside management blocks, tables, fenced combos, footers,
    and the Capital/Money Plan rollups (the same exemption grammar as the
    Setup Grade coverage audit). Zero offenders on a healthy render.

    Each offender: {"line", "ticker", "verdict", "reason", "step",
    "check"}. Fail directions are the evaluator's own — an unparseable
    ticket is SKIPPED (never guessed at), an unknown ticker evaluates
    fail-open and stays quiet (rule #19).
    """
    if not full_md:
        return []
    lines = full_md.splitlines()
    offenders: list[dict] = []
    seen: set[tuple] = set()
    in_fence = False
    in_excluded_section = False
    section_demoted = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            head = stripped
            in_excluded_section = ("Capital Plan" in head
                                   or "Money Plan" in head)
            # Wait/exclusion sections render full tickets BY DESIGN
            # (rule #24) — those are not green-lit surfaces.
            section_demoted = any(mk in head for mk in (
                "⏸", "⛔", "On Deck", "Shown for reference", "Excluded",
                "Held back", "wait for", "Skipped"))
            continue
        if in_excluded_section or section_demoted:
            continue
        if stripped.startswith("_") or stripped.startswith("|"):
            continue
        if not _SELL_MARK_RE.search(line):
            continue
        sm = _STRIKE_TOKEN_RE.search(line)
        if not sm:
            continue
        lo, hi = _sg_mod._card_block(lines, i, 8)
        block = lines[lo:hi + 1]
        block_text = " ".join(block)
        if _sg_mod._MGMT_BLOCK_RE.search(block_text):
            continue        # roll leg / protection — management, not entry
        if any(mk in block_text for mk in _NOT_GREEN_MARKERS):
            continue        # already demoted/tagged — not green-lit
        ticker = _line_ticker(line, block)
        if not ticker:
            continue        # never guess a ticker (rule #19)
        strike = float(sm.group(1))
        side = SIDE_CSP if sm.group(2) == "P" else SIDE_CC
        key = (ticker, side, strike)
        if key in seen:
            continue
        seen.add(key)
        exp = None
        m_iso = _ISO_IN_LINE_RE.search(line)
        if m_iso:
            exp = m_iso.group(0)
        else:
            _d, exp = _parse_exp(line)
        dte = None
        m_dte = _DTE_RE.search(line)
        if m_dte:
            dte = int(m_dte.group(1))
        premium = None
        m_prem = _PREMIUM_RE.search(line)
        if m_prem:
            premium = float(m_prem.group(1))
        try:
            dec = evaluate_entry(
                side, ticker, strike, exp, premium, snapshot_data,
                analytics, config,
                action_close_idents=action_close_idents,
                dte=dte, as_of=as_of)
        except Exception:
            continue        # evaluator failure never breaks the ship
        if not dec.entered:
            p = dec.primary or {}
            offenders.append({
                "line": stripped[:160],
                "ticker": ticker,
                "verdict": dec.verdict,
                "reason": dec.primary_detail,
                "step": p.get("step"),
                "check": p.get("check"),
            })
    return offenders


def render_conformance_panel(offenders: list[dict]) -> list[str]:
    """The '🧮 Entry Algorithm Conformance' panel lines."""
    if not offenders:
        return [
            ("_✅ Entry Algorithm conformance: every green-lit new-open "
             "ticket passes the canonical six-step evaluator (rule #48)._"),
            "",
        ]
    lines = [
        "## 🧮 Entry Algorithm Conformance",
        "",
        (f"_{len(offenders)} green-lit new-open ticket(s) would NOT pass "
         f"the canonical six-step entry algorithm (rule #48) — do NOT "
         f"place these as rendered:_"),
    ]
    for o in offenders[:10]:
        lines.append(
            f"- `{o['line'][:120]}` → **{o['verdict']}** "
            f"(step {o.get('step')}: {o.get('check')}) — {o['reason']}")
    lines.append("")
    return lines
