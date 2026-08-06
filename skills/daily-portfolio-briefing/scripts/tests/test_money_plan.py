"""💰 Money Plan (2026-08-04 fix 3) — the briefing's opening panel.

George's audit of the 2026-08-04 briefing: "sometimes I am confused by
recommendations. we are looking to make money here." The plan opens the
briefing with banks / deploys / net cash / coverage-after / month-to-date
pace / blocked money — actionable-only math, honest n/a when the ledger
isn't derivable (rule #19).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.money_plan import build_money_plan  # noqa: E402

TODAY = "2026-08-04"


def _action_lines():
    return [
        "## Today's Action List — Tuesday, August 4, 2026",
        "",
        "1. **CLOSE** VRT_PUT_280_20270115 — +30% ($+2,226); "
        "buy-to-close limit $26.00",
        "   - **Why:** captured profit",
        "2. **NEW CSP** RDDT — sell $150P exp Fri Sep 11 '26 @ $4.20 mid",
        "   - **Wash-sale check:** ✅ RDDT clear",
        "3. **NEW CSP** ZZZZ — sell $50P exp Fri Sep 11 '26 @ $1.00 mid"
        "  🚫 WASH-SALE BLOCKED",
        "   - **🚫 Skip — wash-sale rule:** closed at a loss 12d ago",
        "",
    ]


def _reviews():
    return [{
        "contract": "VRT_PUT_280_20270115", "underlying": "VRT",
        "type": "PUT", "qty": -2, "strike": 280.0,
        "entry_price": 37.10, "current_mid": 25.97,
    }]


def _ideas():
    return [
        {"ticker": "RDDT", "instruction": {"x": 1}, "mid": 4.20,
         "contracts": 1, "dte": 38},
        {"ticker": "ZZZZ", "instruction": {"x": 1}, "mid": 1.00,
         "contracts": 1, "dte": 38},
    ]


def _playbook():
    return {
        "closes": [],
        "opens": [{"ticker": "NOW", "premium": 818.0, "dte": 38}],
        "coverage_before": 0.26, "coverage_after": 0.34,
    }


def _build(**overrides):
    kw = dict(
        date_str=TODAY,
        action_list_lines=_action_lines(),
        options_reviews=_reviews(),
        new_ideas=_ideas(),
        playbook=_playbook(),
        analytics={"stress_coverage": {"coverage_ratio": 0.26}},
        snapshot_data={"positions": []},
        config={},
        attribution=None,
        long_term_opportunities=[],
        aging_info=None,
    )
    kw.update(overrides)
    return build_money_plan(**kw)


def test_renders_actionable_only_math():
    """Bank counts the VRT close ($+2,226 measured from the review); deploy
    counts RDDT (action list) + NOW (playbook) but NOT the wash-sale-blocked
    ZZZZ — actionable items only."""
    lines, plan = _build()
    text = "\n".join(lines)
    assert lines[0].startswith("## 💰 Money Plan")
    assert "Bank today:** 1 close(s) → $+2,226 realized" in text
    assert "VRT $280P" in text
    assert "2 entries" in text
    assert "RDDT" in text and "NOW" in text
    assert "ZZZZ" not in text
    # 420 + 818 premium
    assert "+$1,238 premium" in text
    assert plan["total_realized"] == 2226.0
    assert plan["total_premium"] == 1238.0


def test_coverage_after_uses_playbook_projection():
    lines, plan = _build()
    assert "0.26× → ~0.34×" in "\n".join(lines)
    assert plan["coverage_after"] == 0.34


def test_mtd_na_honesty_when_ledger_not_derivable():
    """Rule #19: no snapshot history this cycle → 'n/a (ledger pending)',
    never a fabricated month-to-date number."""
    lines, plan = _build(attribution=None)
    assert "August so far:** n/a (ledger pending)" in "\n".join(lines)
    assert plan["mtd"] is None


# ── MTD = matched realized P/L, never raw cash flow (rule #43 follow-up) ──

def _snap_root(tmp_path, prev_positions, curr_positions,
               prev_day="2026-07-31", curr_day="2026-08-04"):
    """Two-day snapshot fixture; returns the current snapshot_dir."""
    root = tmp_path / "briefing_snapshots"
    for day, positions in ((prev_day, prev_positions),
                           (curr_day, curr_positions)):
        d = root / day
        d.mkdir(parents=True)
        (d / "positions.json").write_text(json.dumps(positions))
    return root / curr_day


def _short_put(sym, credit, mid, qty=-2.0):
    return {"symbol": sym, "assetType": "OPTION", "type": "PUT",
            "qty": qty, "premiumReceived": credit, "costPerShare": credit,
            "currentMid": mid}


def test_mtd_matched_realized_not_cashflow(tmp_path):
    """Observed on the real 2026-08-04 briefing: 'August so far: $-27,868
    option premium · pace $-6,967/day' — net option CASH FLOW (August
    buybacks against July credits) rendered as if it were performance,
    turning a profitable dozen-winner harvest into a five-figure negative.
    MTD must be MATCHED per-contract realized P/L: July-shaped credits
    closed in August render strongly POSITIVE."""
    # Harvest-shaped: credits collected in July, both closed in August.
    snap = _snap_root(
        tmp_path,
        prev_positions=[_short_put("LITE_PUT_700_20261218", 34.00, 0.60),
                        _short_put("META_PUT_570_20261016", 27.00, 0.35)],
        curr_positions=[])
    attribution = {"status": "ok", "periods": [{
        "name": "MTD", "start_date": "2026-07-31", "end_date": "2026-08-04",
        "buckets": {"option_premium_net": -27868.0}}]}
    lines, plan = _build(snapshot_dir=snap, attribution=attribution)
    text = "\n".join(lines)
    # (34.00-0.60)*100*2 + (27.00-0.35)*100*2 = 6,680 + 5,330 = +12,010
    assert "August so far:** $+12,010 realized on 2 closed contracts" in text
    assert "pace" in text
    # The raw cash-flow number is labeled OUT of the panel entirely.
    assert "-27,868" not in text
    assert "option premium" not in text
    assert plan["mtd"]["basis"] == "matched_realized"
    assert plan["mtd"]["realized"] == 12010.0


def test_unmatched_contracts_excluded_with_note(tmp_path):
    """A close whose entry credit isn't recoverable is EXCLUDED from the
    MTD total (never estimated) and the line notes '(N of M closes
    matched)'."""
    no_entry = {"symbol": "XYZ_PUT_100_20261218", "assetType": "OPTION",
                "type": "PUT", "qty": -1.0, "currentMid": 1.00}
    snap = _snap_root(
        tmp_path,
        prev_positions=[_short_put("LITE_PUT_700_20261218", 34.00, 0.60),
                        _short_put("META_PUT_570_20261016", 27.00, 0.35),
                        no_entry],
        curr_positions=[])
    lines, plan = _build(snapshot_dir=snap)
    text = "\n".join(lines)
    assert "$+12,010 realized" in text
    assert "(2 of 3 closes matched)" in text
    assert plan["mtd"]["matched"] == 2
    assert plan["mtd"]["closes"] == 3


def test_below_half_matched_renders_na(tmp_path):
    """Fewer than half the month's closes matched → 'n/a (ledger pending —
    matched P/L needs entry credits)' — never the raw cash-flow number,
    never a partial total dressed up as the month."""
    def _no_entry(sym):
        return {"symbol": sym, "assetType": "OPTION", "type": "PUT",
                "qty": -1.0, "currentMid": 1.00}
    snap = _snap_root(
        tmp_path,
        prev_positions=[_short_put("LITE_PUT_700_20261218", 34.00, 0.60),
                        _no_entry("A_PUT_1_20261218"),
                        _no_entry("B_PUT_1_20261218")],
        curr_positions=[])
    lines, _plan = _build(snapshot_dir=snap)
    text = "\n".join(lines)
    assert ("August so far:** n/a (ledger pending — matched P/L needs "
            "entry credits; 1 of 3 closes matched)") in text
    assert "realized on" not in text


def test_no_fabricated_totals(tmp_path):
    """Rule #19: no snapshot history → 'n/a (ledger pending)'; the
    attribution's cash-flow bucket (the source of the observed $-27,868)
    must NEVER leak into the panel as a month-to-date number."""
    attribution = {"status": "ok", "periods": [{
        "name": "MTD", "start_date": "2026-07-31", "end_date": "2026-08-04",
        "buckets": {"option_premium_net": -27868.0,
                    "realized_equity": 1200.0}}]}
    lines, plan = _build(attribution=attribution)  # no snapshot_dir
    text = "\n".join(lines)
    assert "August so far:** n/a (ledger pending)" in text
    assert "-27,868" not in text and "27,868" not in text
    assert "option premium" not in text
    assert plan["mtd"] is None
    # Unreadable root (< 2 snapshot days) → same honesty.
    lonely = tmp_path / "briefing_snapshots" / "2026-08-04"
    lonely.mkdir(parents=True)
    (lonely / "positions.json").write_text("[]")
    lines2, plan2 = _build(snapshot_dir=lonely, attribution=attribution)
    assert "n/a (ledger pending)" in "\n".join(lines2)
    assert plan2["mtd"] is None


def test_blocked_money_counts_gated_entries_and_hedge_nag():
    """Blocked-money line: gated entry count with the measured coverage
    gate, plus the fix-4 hedge standing question ('hedge undecided 29d')."""
    ltos = [
        {"kind": "SKIPPED_LT_CSP", "ticker": "MU",
         "concrete_trade": {"premium": 900.0, "dte": 30}},
        {"kind": "DEFERRED_ADD_HAS_CSP", "ticker": "AMZN"},
    ]
    aging = {"hedge_nag": {"key": "HEDGE:SPY", "days": 29},
             "aged": {"HEDGE:SPY": {"days_flagged": 29}}}
    lines, plan = _build(long_term_opportunities=ltos, aging_info=aging)
    text = "\n".join(lines)
    assert "Blocked money" in text
    assert "2 entries gated (coverage 0.26× < 0.50×)" in text
    assert "hedge undecided 29d — standing question" in text
    # MU's gated entry is measurable → monthly unlock surfaces.
    assert "biggest unlock: +$900 premium/mo" in text
    assert plan["gated_entry_count"] == 2


def test_loss_close_is_not_a_bank():
    """Observed on the real 2026-08-04 data: the PLTR $200C loss-stop CLOSE
    (realized −$4,655) dragged 'Bank today' from +$4,814 to +$159. Loss
    closes are risk actions — excluded from the bank line."""
    lines_list = _action_lines() + [
        "4. **CLOSE** PLTR_CALL_200_20261218 — Loss stop triggered: "
        "loss ratio 2.46x >= 2.0x — buy-to-close limit $11.20",
        "   - **Why:** loss stop",
    ]
    reviews = _reviews() + [{
        "contract": "PLTR_CALL_200_20261218", "underlying": "PLTR",
        "type": "CALL", "qty": -2, "strike": 200.0,
        "entry_price": 4.00, "current_mid": 9.84,
    }]
    lines, plan = _build(action_list_lines=lines_list,
                         options_reviews=reviews)
    text = "\n".join(lines)
    assert "1 close(s) → $+2,226 realized" in text
    assert "PLTR" not in text
    assert plan["total_realized"] == 2226.0


def test_empty_inputs_render_nothing():
    """Nothing measurable → no panel (fail-open, no fabricated lines)."""
    lines, plan = _build(
        action_list_lines=[], options_reviews=[], new_ideas=[],
        playbook={}, long_term_opportunities=[], analytics={})
    assert lines == []
    assert plan == {}


def test_panel_is_max_six_content_lines():
    """The plan stays scannable: header + ≤5 bullets."""
    lines, _ = _build()
    bullets = [ln for ln in lines if ln.startswith("- ")]
    assert 1 <= len(bullets) <= 5
