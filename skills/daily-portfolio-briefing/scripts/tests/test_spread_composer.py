"""Task #42 — put credit spreads, reference-first pilot.

Pins: spread math (net credit / max loss / BP / credit%-of-width /
annualized-on-BP), the zero-bid long-leg skip, the credit%-of-width floor,
Tier A/B exclusion, the paper ledger append + estimated close-out, the
reference-mode render, and the Money Plan informational rollup line.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import spread_composer as spc  # noqa: E402
from render.money_plan import build_money_plan  # noqa: E402


def _idea(ticker="SOFI", strike=145.0, mid=3.20, dte=32,
          expiration="2026-09-18", **kw):
    d = {
        "ticker": ticker, "instruction": "SELL_TO_OPEN", "type": "PUT",
        "strike": strike, "expiration": expiration, "dte": dte,
        "mid": mid, "bid": mid - 0.10, "premium": mid * 100,
        "collateral": strike * 100, "annualized_pct": 27.0,
        "yield_pct": round(mid / strike * 100, 2),
    }
    d.update(kw)
    return d


def _chain(ticker="SOFI", expiration="2026-09-18", rows=None):
    puts = rows if rows is not None else [
        {"strike": 115.0, "bid": 0.55, "ask": 0.75},
        {"strike": 120.0, "bid": 0.80, "ask": 1.00},
        {"strike": 125.0, "bid": 0.82, "ask": 1.02},
        {"strike": 135.0, "bid": 1.60, "ask": 1.90},
        {"strike": 145.0, "bid": 3.10, "ask": 3.30},
    ]
    return {f"{ticker}_{expiration}": {
        "underlying": ticker, "expiration": expiration, "puts": puts,
    }}


def test_spread_math_from_chain():
    """Card math: short $145 @ $3.20 mid, long snapped to $125 ($0.92 mid)
    → $2.28 credit, $20 width, $1,772 max loss = BP, credit 11.4%... which
    is BELOW the 15% floor — so relax the floor to prove the raw math."""
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    s = spc.compose_spread(_idea(), _chain(), cfg)
    assert s["status"] == "ok"
    assert s["short_strike"] == 145.0
    assert s["long_strike"] == 125.0          # nearest to 145 − $20 target
    assert s["width"] == 20.0
    assert s["net_credit_ps"] == 2.28          # 3.20 − (0.82+1.02)/2
    assert s["credit"] == 228.0
    assert s["max_loss"] == 1772.0
    assert s["buying_power"] == 1772.0
    assert abs(s["credit_pct_of_width"] - 0.114) < 0.001
    # annualized on BP: (228 / 1772) × 365/32 ≈ 146.8%
    assert 140 < s["annualized_on_bp_pct"] < 155


def test_credit_floor_skips_thin_spreads():
    """Default 15% floor: the 11.4%-of-width spread above is SKIPPED with
    the measured value in the reason (rule #24 — never silent)."""
    s = spc.compose_spread(_idea(), _chain(), {})
    assert s["status"] == "skipped"
    assert "11.4%" in s["reason"] and "15%" in s["reason"]


def test_zero_bid_long_leg_skips():
    """A zero-bid long leg is fiction — skip with reason, never price it."""
    rows = [
        {"strike": 125.0, "bid": 0.0, "ask": 0.90},
        {"strike": 120.0, "bid": 0, "ask": 0},
    ]
    s = spc.compose_spread(_idea(), _chain(rows=rows), {})
    assert s["status"] == "skipped"
    assert "zero-bid" in s["reason"]


def test_no_chain_skips():
    s = spc.compose_spread(_idea(ticker="XYZ"), {}, {})
    assert s["status"] == "skipped"
    assert "chain" in s["reason"]


def test_high_priced_name_widens_to_10pct():
    """$950 short strike → target width max($20, 10%) = $95, so the long
    leg lands near $855, not $930."""
    rows = [
        {"strike": 855.0, "bid": 8.0, "ask": 9.0},
        {"strike": 930.0, "bid": 20.0, "ask": 21.0},
    ]
    idea = _idea(ticker="MU", strike=950.0, mid=24.0)
    s = spc.compose_spread(idea, _chain(ticker="MU", rows=rows), {})
    assert s["status"] == "ok"
    assert s["long_strike"] == 855.0
    assert s["width"] == 95.0


def test_low_priced_name_width_capped_at_30pct():
    """$17 short strike → the $20 default width is nonsense (wider than
    the strike); width caps at 30% of strike = $5.10, long leg lands near
    $12 — real bug caught on the 2026-08-06 SOFI chain."""
    rows = [
        {"strike": 12.0, "bid": 0.30, "ask": 0.40},
        {"strike": 14.0, "bid": 0.55, "ask": 0.65},
    ]
    idea = _idea(ticker="SOFI", strike=17.0, mid=1.20)
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    s = spc.compose_spread(idea, _chain(rows=rows), cfg)
    assert s["status"] == "ok"
    assert s["long_strike"] == 12.0
    assert s["width"] == 5.0


def test_tier_a_b_excluded_from_spreads():
    """Spreads are for income names — Tier A/B candidates are excluded and
    named in the exclusion note."""
    config = {"position_tiers": {"tier_a_core": ["NVDA"],
                                 "tier_b_income": ["MU"]},
              "spread": {"min_credit_pct_of_width": 0.10}}
    ideas = [_idea(ticker="NVDA"), _idea(ticker="MU"), _idea(ticker="SOFI")]
    chains = _chain(ticker="SOFI")
    out = spc.compose_spreads(ideas, chains, config)
    assert [s["ticker"] for s in out["spreads"]] == ["SOFI"]
    assert any("NVDA (Tier A)" in t for t in out["tier_excluded"])
    assert any("MU (Tier B)" in t for t in out["tier_excluded"])


def test_non_actionable_ideas_ignored():
    """rsi_wait / rsi_blocked / capacity_blocked / watch-only ideas never
    get a spread variant — the composer only sees gate-passed candidates."""
    ideas = [
        _idea(rsi_wait=True),
        _idea(ticker="AA", rsi_blocked=True, instruction=None),
        _idea(ticker="BB", capacity_blocked=True),
        _idea(ticker="CC", instruction=None),
    ]
    out = spc.compose_spreads(ideas, _chain(), {})
    assert out["spreads"] == [] and out["skips"] == []


def test_render_reference_mode_card():
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    out = spc.compose_spreads([_idea()], _chain(), cfg)
    lines = spc.render_spreads_section(out, cfg)
    md = "\n".join(lines)
    assert "📐 SPREADS — reference (paper-watch)" in md
    assert "SOFI $145P/$125P" in md
    assert "$228 credit / $1,772 max loss" in md
    assert "credit 11.4% of width" in md
    assert "vs" in md and "$14,500 BP" in md
    # reference mode renders NO order ticket
    assert "SELL TO OPEN" not in md


def test_render_live_mode_has_two_leg_ticket():
    cfg = {"spread": {"min_credit_pct_of_width": 0.10, "mode": "live"}}
    out = spc.compose_spreads([_idea()], _chain(), cfg)
    md = "\n".join(spc.render_spreads_section(out, cfg))
    assert "SELL TO OPEN" in md and "BUY TO OPEN" in md
    assert "net credit $2.28/sh" in md


def test_paper_ledger_append_dedupe_and_closeout(tmp_path):
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    s = spc.compose_spread(_idea(), _chain(), cfg)
    path = tmp_path / "spread_paper_ledger.json"
    r1 = spc.update_paper_ledger(path, [s], "2026-08-06")
    assert r1["added"] == 1 and r1["open"] == 1
    # Same day re-run: deduped.
    r2 = spc.update_paper_ledger(path, [s], "2026-08-06")
    assert r2["added"] == 0 and r2["open"] == 1
    # After expiry with a spot: estimated settle, labeled.
    r3 = spc.update_paper_ledger(path, [], "2026-09-21",
                                 spot_by_ticker={"SOFI": 150.0})
    assert r3["closed"] == 1 and r3["open"] == 0
    entry = spc.load_ledger(path)["entries"][0]
    assert entry["status"] == "expired_estimate"
    assert entry["est_pnl"] == 228.0          # fully OTM → keep the credit
    assert "estimate" in entry["settle_note"]


def test_paper_ledger_closeout_itm_estimate(tmp_path):
    """Spot between the strikes at check → intrinsic reduces the P&L."""
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    s = spc.compose_spread(_idea(), _chain(), cfg)
    path = tmp_path / "ledger.json"
    spc.update_paper_ledger(path, [s], "2026-08-06")
    spc.update_paper_ledger(path, [], "2026-09-21",
                            spot_by_ticker={"SOFI": 135.0})
    entry = spc.load_ledger(path)["entries"][0]
    # intrinsic = (145−135) − 0 = $10 → pnl = (2.28 − 10) × 100
    assert entry["est_pnl"] == -772.0


def test_paper_ledger_no_spot_stays_open(tmp_path):
    """No spot this cycle → never a fabricated settle; entry stays open."""
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    s = spc.compose_spread(_idea(), _chain(), cfg)
    path = tmp_path / "ledger.json"
    spc.update_paper_ledger(path, [s], "2026-08-06")
    r = spc.update_paper_ledger(path, [], "2026-09-21")
    assert r["closed"] == 0 and r["open"] == 1


def test_spread_reference_summary_only_counts_composed():
    """Gated entries without a real composed long leg NEVER count toward
    the Money-Plan rollup (no extrapolation)."""
    cfg = {"spread": {"min_credit_pct_of_width": 0.10}}
    gated = [
        {"kind": "SKIPPED_LT_CSP", "concrete_trade": _idea()},
        {"kind": "DEFERRED_ADD_HAS_CSP",
         "concrete_trade": _idea(ticker="NOCHAIN")},
    ]
    out = spc.spread_reference_summary(gated, _chain(), cfg)
    assert out["count"] == 1
    assert out["spread_bp"] == 1772.0
    assert out["csp_collateral"] == 14500.0


def test_money_plan_carries_spread_reference_line():
    """The blocked-money bullet carries the informational spread-mode BP
    comparison when a rollup is provided."""
    gated_idea = {"ticker": "SOFI", "kind": "SKIPPED_RSI",
                  "source": "recommendation_list_capacity_blocked",
                  "capacity_blocked": True, "instruction": None,
                  "rationale": "skipped: capacity"}
    lines, plan = build_money_plan(
        date_str="2026-08-06", action_list_lines=[],
        options_reviews=[], new_ideas=[gated_idea], playbook=None,
        analytics=None, snapshot_data=None, config=None,
        spread_reference={"count": 1, "spread_bp": 1772.0,
                          "csp_collateral": 14500.0},
    )
    md = "\n".join(lines)
    assert "spread-mode would run today's 1 gated entry at ~$1,772 BP " \
           "vs $14,500 cash-secured" in md
    assert plan["spread_reference"]["spread_bp"] == 1772.0


def test_money_plan_no_line_without_rollup():
    gated_idea = {"ticker": "SOFI", "kind": "SKIPPED_RSI",
                  "capacity_blocked": True,
                  "instruction": None, "rationale": "skipped"}
    lines, _plan = build_money_plan(
        date_str="2026-08-06", action_list_lines=[],
        options_reviews=[], new_ideas=[gated_idea], playbook=None,
        analytics=None, snapshot_data=None, config=None,
    )
    assert "spread-mode" not in "\n".join(lines)
