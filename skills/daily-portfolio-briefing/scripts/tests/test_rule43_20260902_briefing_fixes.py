"""Rule #43 bug-fix-on-sight — three defects observed in the real
2026-09-02 briefing (/…/briefings/briefing_2026-09-02.md), each fixed the
same session with regression tests quoting the observed output.

BUG A — FIRED stall exits are not banked. Action item #2 read
'🏇 **EXIT — STALL FIRED (directive)** SNDK_PUT_1030_20261120 — +34%
captured — [today: SNDK -2.5% (red-day stall ≤ -2%) — STALL TRIGGER
FIRED, exit per directive]' with a real ticket ('BUY TO CLOSE 1× SNDK
$1030P — mid $27.75'), yet the Money Plan read '**Bank today:** 1
close(s) → $+160 realized (AVGO $340P)' and 'Net option cash today
(mid-fills): −$595 buyback', and the Capital Plan showed 'Collateral
freed: $34,000' — the SNDK fired exit was missing everywhere. Root
cause: the ``_directive_ride`` demotion (rule #51 — correct while
RIDING) was never cleared when the stall FIRED.

BUG B — Capital Plan Tier 1 CRITICAL filled with $0-impact rows while
the real close sat in Tier 2. Observed Tier 1: 'EXIT NFLX — REVIEW NFLX
— consider exit — net cash +$0' (also SOXL / AMAT / NBIS); Tier 2:
'CLOSE AVGO — locks $+160, frees $34,000 — net cash +$33,375'.

BUG C — dangling arrow in the header: '**🎓 Entry quality: last 10 avg
D (38) → · last entry NVDA $195P — D (RSI 54 late-band)**' — the '→ ·'
renders with nothing after the arrow.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_ledger  # noqa: E402
from analysis.net_option_cash import (  # noqa: E402
    compute_net_option_cash,
    demoted_close_idents,
)
from render.money_plan import build_money_plan  # noqa: E402
from render.panels import render_action_list  # noqa: E402

TODAY = "2026-09-02"
_SNDK = "SNDK_PUT_1030_20261120"
_MELI = "MELI_PUT_1460_20270617"


def _plan_module():
    target = (Path(__file__).resolve().parents[3]
              / "capital-planner" / "scripts" / "plan.py")
    spec = importlib.util.spec_from_file_location(
        "capital_planner_plan_20260902", target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capital_planner_plan_20260902"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Fixtures — the SNDK fired stall + the MELI still-riding control ───────

def _sndk_directive():
    return {
        "id": "sndk_put_1030_gtc_path",
        "status": "ACTIVE",
        "type": "OVERRIDE",
        "target": {"identifier": _SNDK},
        "filed": "2026-09-01",
        "reason": ("George 2026-09-01 — GTC-at-50% path on the SNDK "
                   "$1030P."),
        "exit_conditions": {
            "gtc_capture": 0.50, "gtc_limit_price": 22.05,
            "red_day_pct": -2.0, "rsi_stall": 70,
            "max_capture": 0.85, "earnings_min_capture": 0.50,
        },
    }


def _sndk_review():
    """Economics matching the observed card: '+34% captured', ticket
    'BUY TO CLOSE 1× SNDK $1030P — exit per directive (mid $27.75, …)',
    realized ≈ +$1,425, collateral $103,000."""
    return {
        "underlying": "SNDK", "contract": _SNDK, "type": "PUT",
        "qty": -1.0, "strike": 1030.0, "expiration": "2026-11-20",
        "current_mid": 27.75, "entry_price": 42.00, "days_to_expiry": 79,
        "recommendation": "OVERRIDE",
        "rationale": "User directive GTC-at-50% path",
        "matrix_cell_id": "DIRECTIVE_OVERRIDE",
        "directive": _sndk_directive(), "_directive_ride": True,
    }


def _meli_directive():
    return {
        "id": "meli_put_1460_momentum_ride",
        "status": "ACTIVE", "type": "OVERRIDE",
        "target": {"identifier": _MELI}, "filed": "2026-08-25",
        "reason": "George 2026-08-25 — momentum ride on the MELI $1460P.",
        "exit_conditions": {
            "gtc_capture": 0.75, "gtc_limit_price": 24.60,
            "gtc_stretch_capture": 0.85, "red_day_pct": -2.0,
            "rsi_stall": 70, "max_capture": 0.85,
            "earnings_min_capture": 0.75,
        },
    }


def _meli_review():
    return {
        "underlying": "MELI", "contract": _MELI, "type": "PUT",
        "qty": -1.0, "strike": 1460.0, "expiration": "2027-06-17",
        "current_mid": 24.60, "entry_price": 36.30, "days_to_expiry": 288,
        "recommendation": "OVERRIDE",
        "rationale": "User directive momentum ride",
        "matrix_cell_id": "DIRECTIVE_OVERRIDE",
        "directive": _meli_directive(), "_directive_ride": True,
    }


def _snap(sndk_day=-2.5, meli_day=+0.5):
    """Observed day moves: 'SNDK -2.5% (red-day stall ≤ -2%)' fires;
    'MELI +0.5% · RSI 58 … — no exit trigger fired' rides on."""
    return {
        "quotes": {
            "SNDK": {"last": round(100.0 * (1 + sndk_day / 100.0), 2)},
            "MELI": {"last": round(2000.0 * (1 + meli_day / 100.0), 2)},
        },
        "technicals": {
            "SNDK": {"spot": 100.0, "rsi_14": 60.0},
            "MELI": {"spot": 2000.0, "rsi_14": 58.0},
        },
        "positions": [], "chains": {}, "iv_ranks": {},
        "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": []},
        "balance": {"accountValue": 1_088_968, "cash": 10_679},
    }


def _render(reviews, snap):
    return render_action_list([], reviews, [], None, snap, date_str=TODAY)


def _money_plan(lines, reviews):
    return build_money_plan(
        date_str=TODAY,
        action_list_lines=lines,
        options_reviews=reviews,
        new_ideas=[],
        playbook={},
        analytics={"stress_coverage": {"coverage_ratio": 0.01}},
        snapshot_data={"positions": []},
        config={},
        attribution=None,
        long_term_opportunities=[],
        aging_info=None,
    )


# ── BUG A — a FIRED stall banks like any actionable close ─────────────────

def test_fired_stall_renders_realized_dollars_and_clears_demotion():
    """Observed: '🏇 **EXIT — STALL FIRED (directive)**
    SNDK_PUT_1030_20261120 — +34% captured — [today: SNDK -2.5% (red-day
    stall ≤ -2%) — STALL TRIGGER FIRED, exit per directive]'. The fired
    headline must carry the measured realized dollars and the review's
    ``_directive_ride`` demotion must be cleared (a fired stall is the
    directive's own actionable exit)."""
    rev = _sndk_review()
    lines = _render([rev], _snap())
    md = "\n".join(lines)
    assert "🏇 **EXIT — STALL FIRED (directive)** " + _SNDK in md
    assert "STALL TRIGGER FIRED, exit per directive" in md
    assert "+34% captured ($+1,425)" in md
    assert "BUY TO CLOSE 1× SNDK $1030P" in md
    assert rev["_directive_ride"] is False
    assert rev["_directive_stall_fired"] is True
    assert _SNDK not in demoted_close_idents([rev])


def test_fired_stall_banks_in_money_plan():
    """Observed Money Plan: '**Bank today:** 1 close(s) → $+160 realized
    (AVGO $340P)' — SNDK's fired exit (+$1,425 realized) was missing.
    After the fix the fired stall banks like any actionable close."""
    rev = _sndk_review()
    lines = _render([rev], _snap())
    mp_lines, plan = _money_plan(lines, [rev])
    text = "\n".join(mp_lines)
    assert "SNDK $1030P" in text
    assert plan["total_realized"] == 1425.0
    assert any(b["ident"] == _SNDK for b in plan["banks"])


def test_fired_stall_counts_as_buyback_in_net_option_cash():
    """Observed: 'Net option cash today (mid-fills): −$595 buyback' —
    SNDK's ~$2,775 buyback (mid $27.75 × 100) was missing from the shared
    net-option-cash computation."""
    rev = _sndk_review()
    lines = _render([rev], _snap())
    noc = compute_net_option_cash(lines, [rev])
    sndk = [c for c in noc["components"]
            if _SNDK in (c.get("label") or "")]
    assert len(sndk) == 1
    assert sndk[0]["group"] == "buybacks"
    assert sndk[0]["cash"] == -2775.0
    assert noc["total_buybacks"] == -2775.0


def test_fired_stall_reaches_capital_plan_with_freed_collateral():
    """Observed Capital Plan: 'Collateral freed: $34,000' — the SNDK
    fired exit's $103,000 freed and ~$2,775 buyback never reached the
    plan (the '(directive)' parenthetical broke the verb parse). After
    the fix the fired stall is a CLOSE-family action: Tier 1 (≥30%
    capture), frees $103,000, costs $2,775."""
    mod = _plan_module()
    rev = _sndk_review()
    lines = _render([rev], _snap())
    actions = mod.extract_actions_from_action_list(lines)
    closes = [a for a in actions if a.kind == "CLOSE" and a.ticker == "SNDK"]
    assert len(closes) == 1
    a = closes[0]
    assert a.cash_in == 103_000.0
    assert a.cash_out == 2_775.0
    assert a.tier == 1


def test_still_riding_item_stays_unbanked_the_meli_control():
    """Regression pin (rule #51 unchanged): the SAME briefing's item #5
    '🏇 **RIDING (directive)** MELI_PUT_1460_20270617 — +32% captured —
    … [today: MELI +0.5% · RSI 58 · 32% captured — no exit trigger
    fired]' must stay unbanked everywhere while riding."""
    rev = _meli_review()
    lines = _render([rev], _snap())
    md = "\n".join(lines)
    assert "🏇 **RIDING (directive)** " + _MELI in md
    assert "no exit trigger fired" in md
    assert rev["_directive_ride"] is True
    assert _MELI in demoted_close_idents([rev])
    mp_lines, plan = _money_plan(lines, [rev])
    # A riding-only cycle has nothing to bank — the panel either omits
    # itself entirely or renders no MELI bank.
    assert not plan.get("banks")
    assert "MELI" not in "\n".join(
        ln for ln in mp_lines if "Bank today" in ln)
    noc = compute_net_option_cash(lines, [rev])
    assert all(_MELI not in (c.get("label") or "")
               for c in noc["components"])


def test_fired_and_riding_together_bank_only_the_fired_one():
    """The full observed pair — SNDK fired + MELI riding — banks exactly
    the fired exit."""
    sndk, meli = _sndk_review(), _meli_review()
    lines = _render([sndk, meli], _snap())
    _, plan = _money_plan(lines, [sndk, meli])
    idents = {b["ident"] for b in plan["banks"]}
    assert idents == {_SNDK}


# ── BUG B — $0-impact REVIEW rows never above a cash-freeing CLOSE ────────

def _avgo_close_block():
    """The observed AVGO card (briefing_2026-09-02.md action item #1)."""
    return [
        "1. **CLOSE** AVGO_PUT_340_20260911 — +21% ($+160); "
        "buy-to-close limit $6.25",
        "   - **Gain:** Locks $+160 profit and frees $34,000 cash "
        "collateral; redeploy that collateral into a fresh "
        "higher-premium opportunity.",
    ]


def _exit_ops():
    return [
        {"kind": "EXIT", "ticker": t,
         "concrete_trade": f"REVIEW {t} — consider exit"}
        for t in ("NFLX", "SOXL", "AMAT", "NBIS")
    ]


def test_zero_cash_review_rows_never_outrank_a_cash_freeing_close():
    """Observed: Tier 1 = 'EXIT NFLX — REVIEW NFLX — consider exit — net
    cash +$0' (and SOXL / AMAT / NBIS) while Tier 2 = 'CLOSE AVGO — locks
    $+160, frees $34,000 — net cash +$33,375'. A zero-cash-impact
    REVIEW/consider-exit row must never outrank an action that frees
    collateral or banks premium."""
    mod = _plan_module()
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_088_968, "cash": 10_679},
        positions=[],
        long_term_opportunities=_exit_ops(),
        action_list_lines=_avgo_close_block(),
        coverage_ratio=0.01,
    )
    closes = [a for a in plan.actions if a.kind == "CLOSE"]
    exits = [a for a in plan.actions if a.kind == "LT_EXIT"]
    assert closes and exits
    close_tier = min(a.tier for a in closes)
    assert all(a.tier >= close_tier for a in exits)
    # The plan's own ordering puts the cash-freeing close FIRST.
    order = [(a.kind, a.ticker) for a in plan.actions]
    assert order.index(("CLOSE", "AVGO")) < order.index(("LT_EXIT", "NFLX"))


def test_demoted_review_rows_stay_visible_with_the_reason():
    """Rule #24 — never hide: the demoted advisory rows still render in
    the Capital Plan markdown, below the CLOSE, with the demotion
    reason."""
    mod = _plan_module()
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_088_968, "cash": 10_679},
        positions=[],
        long_term_opportunities=_exit_ops(),
        action_list_lines=_avgo_close_block(),
        coverage_ratio=0.01,
    )
    md = "\n".join(mod.format_capital_plan_md(plan))
    assert "EXIT NFLX" in md
    assert "CLOSE AVGO" in md
    assert md.index("CLOSE AVGO") < md.index("EXIT NFLX")
    assert "advisory ($0 cash impact)" in md
    # The observed contradiction cannot recur: no $0 advisory EXIT sits in
    # a tier ABOVE the cash-freeing close.
    exits = [a for a in plan.actions if a.kind == "LT_EXIT"]
    for a in exits:
        assert "ranked below cash-freeing actions" in a.tier_reason


def test_exit_rows_keep_tier_1_when_no_cash_action_exists():
    """Without any cash-freeing action the thesis-broken EXIT keeps its
    Tier 1 urgency (the demotion is relative, not absolute)."""
    mod = _plan_module()
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_000_000, "cash": 50_000},
        positions=[],
        long_term_opportunities=_exit_ops()[:1],
        action_list_lines=[],
        coverage_ratio=0.60,
    )
    exits = [a for a in plan.actions if a.kind == "LT_EXIT"]
    assert exits and exits[0].tier == 1


# ── BUG C — no dangling '→ ·' in the entry-quality header ────────────────

_CFG = {"entry_scorecard": {"enabled": True},
        "setup_grade": {"enabled": True}}


def _entry(und, strike, date, score, letter):
    return {
        "id": f"{und}|PUT|{strike:g}|2026-12-18|{date}",
        "contract": {"underlying": und, "type": "PUT", "strike": strike,
                     "expiration": "2026-12-18"},
        "symbol": f"{und}_PUT_{strike:g}_20261218",
        "label": f"{und} ${strike:g}P",
        "entry_date": date, "before_archive": False, "side": "short_put",
        "qty": 1.0, "premium": 5.0, "premium_source": "costPerShare",
        "grade": {"letter": letter, "score": score,
                  "drivers": ["RSI 54 late-band"], "hard_blocked": False,
                  "message": ""},
        "conditions": {"rsi": 54.0, "iv_rank": 70.0, "iv_source": "rv"},
        "iv_source": "rv", "seeded": True, "roll": False,
        "status": "open", "outcome": None,
    }


def test_flat_trend_renders_no_dangling_arrow():
    """Observed: '**🎓 Entry quality: last 10 avg D (38) → · last entry
    NVDA $195P — D (RSI 54 late-band)**' — the flat trend token rendered
    a dangling '→ ·' with nothing after the arrow. Flat (or missing)
    trend → no arrow at all."""
    entries = [
        _entry("NVDA", 195, "2026-09-01", 38.0, "D"),   # this week
        _entry("MU", 950, "2026-08-24", 39.0, "D"),     # prior week
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, TODAY, _CFG)
    assert sc["trend"] is not None and sc["trend"]["arrow"] == "→"
    dl = sc["digest_line"]
    assert "→" not in dl
    assert "→ ·" not in dl
    assert dl.startswith("🎓 Entry quality: last 10 avg D (38)")
    assert "last entry NVDA $195P — D" in dl


def test_missing_trend_renders_no_arrow():
    """A single graded entry (no prior-week window) → no trend, no arrow,
    no dangling glyph (rule #19 — never a fabricated trend)."""
    entries = [_entry("NVDA", 195, "2026-09-01", 38.0, "D")]
    sc = entry_ledger.compute_scorecard({"entries": entries}, TODAY, _CFG)
    assert sc["trend"] is None
    assert "→" not in sc["digest_line"]


def test_directional_trend_still_renders_its_arrow():
    """A REAL directional trend keeps its arrow — the fix drops only the
    dangling flat glyph."""
    entries = [
        _entry("NVDA", 195, "2026-09-01", 31.0, "D"),   # this week
        _entry("MU", 950, "2026-08-24", 45.0, "C"),     # prior week
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, TODAY, _CFG)
    assert sc["trend"]["arrow"] == "↘"
    assert "↘" in sc["digest_line"]
