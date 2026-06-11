"""Tests for rec_aging.py — Step 7.5 fill reconciliation + recommendation aging.

Covers:
1. reconcile: EXECUTED on closed position, IGNORED on unchanged, PARTIAL on
   reduced, UNVERIFIED on missing snapshot, ROLL verification (old gone AND a
   new later-dated same-underlying short present — never guess otherwise)
2. age_actions: increments on IGNORED repeats, resets on EXECUTED, fresh
   insert for new items, UNVERIFIED carries forward without escalation
3. render_stalled_panel: renders at 6+ days, silent below
4. render_action_list integration: ⏳ tag at 3 days, day-5 binary prompt,
   headline cap of 5 with "Appendix: Full Action Queue", aged items sort first
5. diff panel: recon statuses replace "likely executed" wording
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.rec_aging import (
    HEADLINE_CAP,
    action_key,
    age_actions,
    extract_actions_from_markdown,
    key_from_signature,
    reconcile,
    render_stalled_panel,
    load_state,
    save_state,
)
from analysis.briefing_diff import render_diff_panel
from render.panels import render_action_list


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _opt(symbol, qty, underlying=None, opt_type=None, strike=None, exp=None):
    parts = symbol.split("_")
    return {
        "symbol": symbol,
        "assetType": "OPTION",
        "type": opt_type or (parts[1] if len(parts) > 1 else "PUT"),
        "underlying": underlying or parts[0],
        "strike": float(strike if strike is not None else (parts[2] if len(parts) > 2 else 0)),
        "expiration": exp or (parts[3] if len(parts) > 3 else ""),
        "qty": qty,
    }


def _eq(symbol, qty):
    return {"symbol": symbol, "assetType": "EQUITY", "qty": qty}


MU_CLOSE = {"key": "CLOSE:MU_PUT_700_20261218", "kind": "CLOSE",
            "ident": "MU_PUT_700_20261218", "summary": "CLOSE MU put"}
NVDA_TRIM = {"key": "TRIM:NVDA", "kind": "TRIM", "ident": "NVDA",
             "summary": "TRIM NVDA — over cap"}
AMD_ROLL = {"key": "EXECUTE_ROLL:AMD_PUT_440_20261016", "kind": "EXECUTE_ROLL",
            "ident": "AMD_PUT_440_20261016", "summary": "Roll AMD put"}


# ─────────────────────────────────────────────────────────────────────────────
# Keys
# ─────────────────────────────────────────────────────────────────────────────

def test_action_key_normalizes_kind_and_ident():
    assert action_key("DEFENSIVE ROLL (core override)", "goog_call_450") == \
        "DEFENSIVE_ROLL:GOOG_CALL_450"
    assert key_from_signature("CLOSE|MU_PUT_700_20261218") == "CLOSE:MU_PUT_700_20261218"


def test_extract_actions_from_markdown_includes_appendix():
    md = (
        "## Today's Action List\n\n"
        "1. **CLOSE** MU_PUT_700_20261218 — +33%\n"
        "   - **Why:** captured\n"
        "2. **TRIM** NVDA — over 10% NLV\n\n"
        "### Appendix: Full Action Queue\n\n"
        "6. **HEDGE** Buy 13× SPY put $711P\n"
        "## Watch\n"
    )
    actions = extract_actions_from_markdown(md)
    keys = {a["key"] for a in actions}
    assert "CLOSE:MU_PUT_700_20261218" in keys
    assert "TRIM:NVDA" in keys
    assert "HEDGE:SPY" in keys  # appendix items count too


# ─────────────────────────────────────────────────────────────────────────────
# Reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_executed_on_closed_position():
    prev = [_opt("MU_PUT_700_20261218", -2)]
    today = []  # position gone
    statuses = reconcile([MU_CLOSE], prev, today, [])
    assert statuses["CLOSE:MU_PUT_700_20261218"] == "EXECUTED"


def test_reconcile_ignored_on_unchanged_position():
    prev = [_opt("MU_PUT_700_20261218", -2)]
    today = [_opt("MU_PUT_700_20261218", -2)]
    statuses = reconcile([MU_CLOSE], prev, today, [])
    assert statuses["CLOSE:MU_PUT_700_20261218"] == "IGNORED"


def test_reconcile_partial_on_reduced_contracts():
    prev = [_opt("MU_PUT_700_20261218", -2)]
    today = [_opt("MU_PUT_700_20261218", -1)]
    statuses = reconcile([MU_CLOSE], prev, today, [])
    assert statuses["CLOSE:MU_PUT_700_20261218"] == "PARTIAL"


def test_reconcile_unverified_on_missing_snapshot():
    statuses = reconcile([MU_CLOSE, NVDA_TRIM], None,
                         [_opt("MU_PUT_700_20261218", -2)], [])
    assert statuses["CLOSE:MU_PUT_700_20261218"] == "UNVERIFIED"
    assert statuses["TRIM:NVDA"] == "UNVERIFIED"
    # ... and missing TODAY snapshot too
    statuses = reconcile([MU_CLOSE], [_opt("MU_PUT_700_20261218", -2)], None, [])
    assert statuses["CLOSE:MU_PUT_700_20261218"] == "UNVERIFIED"


def test_reconcile_roll_executed_requires_replacement_leg():
    prev = [_opt("AMD_PUT_440_20261016", -1)]
    # Old contract gone AND a NEW later-dated same-underlying short put present
    today = [_opt("AMD_PUT_420_20270115", -1)]
    statuses = reconcile([AMD_ROLL], prev, today, [])
    assert statuses["EXECUTE_ROLL:AMD_PUT_440_20261016"] == "EXECUTED"


def test_reconcile_roll_without_replacement_is_unverified():
    """Old contract gone but no new leg — could be a close or assignment.
    Never guess EXECUTED on a roll."""
    prev = [_opt("AMD_PUT_440_20261016", -1)]
    statuses = reconcile([AMD_ROLL], prev, [], [])
    assert statuses["EXECUTE_ROLL:AMD_PUT_440_20261016"] == "UNVERIFIED"


def test_reconcile_roll_preexisting_later_leg_does_not_count():
    """A later-dated short that already existed yesterday is NOT the rolled-to
    leg — stay conservative."""
    prev = [_opt("AMD_PUT_440_20261016", -1), _opt("AMD_PUT_420_20261218", -1)]
    today = [_opt("AMD_PUT_420_20261218", -1)]
    statuses = reconcile([AMD_ROLL], prev, today, [])
    assert statuses["EXECUTE_ROLL:AMD_PUT_440_20261016"] == "UNVERIFIED"


def test_reconcile_trim_executed_on_reduction_and_ignored_when_flat():
    prev = [_eq("NVDA", 700)]
    statuses = reconcile([NVDA_TRIM], prev, [_eq("NVDA", 500)], [])
    assert statuses["TRIM:NVDA"] == "EXECUTED"
    statuses = reconcile([NVDA_TRIM], prev, [_eq("NVDA", 700)], [])
    assert statuses["TRIM:NVDA"] == "IGNORED"


def test_reconcile_hedge_executed_on_new_long_put():
    hedge = {"key": "HEDGE:SPY", "kind": "HEDGE", "ident": "SPY", "summary": "Buy SPY puts"}
    prev = [_eq("NVDA", 700)]
    today = [_eq("NVDA", 700), _opt("SPY_PUT_650_20260918", +13)]
    assert reconcile([hedge], prev, today, [])["HEDGE:SPY"] == "EXECUTED"
    assert reconcile([hedge], prev, prev, [])["HEDGE:SPY"] == "IGNORED"


# ─────────────────────────────────────────────────────────────────────────────
# Aging
# ─────────────────────────────────────────────────────────────────────────────

def test_age_actions_new_item_starts_at_day_one():
    state, aged = age_actions([MU_CLOSE], {}, {}, "2026-06-11")
    info = aged["CLOSE:MU_PUT_700_20261218"]
    assert info["days_flagged"] == 1
    assert info["first_flagged"] == "2026-06-11"
    assert not info["tier1"] and not info["stalled"]
    assert state["CLOSE:MU_PUT_700_20261218"]["days_flagged"] == 1


def test_age_actions_increments_on_ignored_repeat():
    prior = {"CLOSE:MU_PUT_700_20261218":
             {"first_flagged": "2026-06-09", "days_flagged": 2}}
    recon = {"CLOSE:MU_PUT_700_20261218": "IGNORED"}
    state, aged = age_actions([MU_CLOSE], prior, recon, "2026-06-11")
    info = aged["CLOSE:MU_PUT_700_20261218"]
    assert info["days_flagged"] == 3
    assert info["first_flagged"] == "2026-06-09"  # original date preserved
    assert info["tier1"] is True


def test_age_actions_resets_on_executed():
    prior = {"CLOSE:MU_PUT_700_20261218":
             {"first_flagged": "2026-06-05", "days_flagged": 5}}
    recon = {"CLOSE:MU_PUT_700_20261218": "EXECUTED"}
    state, aged = age_actions([MU_CLOSE], prior, recon, "2026-06-11")
    info = aged["CLOSE:MU_PUT_700_20261218"]
    assert info["days_flagged"] == 1
    assert info["first_flagged"] == "2026-06-11"


def test_age_actions_unverified_carries_forward_without_escalation():
    prior = {"CLOSE:MU_PUT_700_20261218":
             {"first_flagged": "2026-06-09", "days_flagged": 2}}
    recon = {"CLOSE:MU_PUT_700_20261218": "UNVERIFIED"}
    state, aged = age_actions([MU_CLOSE], prior, recon, "2026-06-11")
    assert aged["CLOSE:MU_PUT_700_20261218"]["days_flagged"] == 2  # no increment


def test_age_actions_prunes_dropped_items():
    prior = {"TRIM:GOOG": {"first_flagged": "2026-06-01", "days_flagged": 8}}
    state, aged = age_actions([MU_CLOSE], prior, {}, "2026-06-11")
    assert "TRIM:GOOG" not in state  # consecutiveness broken → clock restarts


# ─────────────────────────────────────────────────────────────────────────────
# Stalled panel
# ─────────────────────────────────────────────────────────────────────────────

def test_stalled_panel_renders_at_six_days():
    aged = {
        "HEDGE:SPY": {"kind": "HEDGE", "ident": "SPY",
                      "summary": "Buy 13× SPY put $711P",
                      "first_flagged": "2026-06-03", "days_flagged": 6,
                      "recon_status": "IGNORED", "stalled": True},
    }
    panel = render_stalled_panel(aged)
    text = "\n".join(panel)
    assert "## ⛔ Stalled Items" in text
    assert "HEDGE SPY" in text
    assert "IGNORED 6 DAYS" in text
    assert "2026-06-03" in text
    assert "SPY put $711P" in text  # recommendation summary carried


def test_stalled_panel_silent_below_six_days():
    aged = {"TRIM:NVDA": {"kind": "TRIM", "ident": "NVDA", "summary": "x",
                          "first_flagged": "2026-06-08", "days_flagged": 5}}
    assert render_stalled_panel(aged) == []
    assert render_stalled_panel({}) == []


# ─────────────────────────────────────────────────────────────────────────────
# Action-list integration (panels.render_action_list with aging_info)
# ─────────────────────────────────────────────────────────────────────────────

def _close_winner_review(ticker, strike=100):
    """An option review that fires the CLOSE-winner branch (≥30% captured)."""
    contract = f"{ticker}_PUT_{strike}_20260821"
    return {
        "underlying": ticker, "contract": contract, "type": "PUT",
        "qty": -1, "strike": float(strike), "expiration": "2026-08-21",
        "entry_price": 10.0, "current_mid": 5.0,  # 50% captured
        "days_to_expiry": 45, "recommendation": "HOLD",
        "rationale": "winner", "matrix_cell_id": "PUT_NORMAL_DEEP_OTM_ABOVE_50",
    }


SIX_TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def _render(aging_info):
    reviews = [_close_winner_review(t) for t in SIX_TICKERS]
    return render_action_list([], reviews, [], analytics=None,
                              snapshot_data=None, date_str="2026-06-11",
                              aging_info=aging_info)


def test_action_list_cap_five_with_appendix():
    aging_info = {"today": "2026-06-11", "state": {}, "reconciliation": {}}
    lines = _render(aging_info)
    text = "\n".join(lines)
    assert "### Appendix: Full Action Queue" in text
    headline = text.split("### Appendix: Full Action Queue")[0]
    import re
    headline_items = re.findall(r"^\d+\.\s+\*\*", headline, re.MULTILINE)
    assert len(headline_items) == HEADLINE_CAP
    appendix = text.split("### Appendix: Full Action Queue")[1]
    assert re.search(r"^\d+\.\s+\*\*CLOSE\*\* FFF_PUT", appendix, re.MULTILINE)
    # aging_info mutated with exports for JSON sidecar + state persistence
    assert len(aging_info["actions_export"]) == 6
    assert all(a["recon_status"] == "NEW" for a in aging_info["actions_export"])
    assert len(aging_info["updated_state"]) == 6


def test_action_list_no_appendix_when_under_cap():
    aging_info = {"today": "2026-06-11", "state": {}, "reconciliation": {}}
    reviews = [_close_winner_review(t) for t in SIX_TICKERS[:3]]
    lines = render_action_list([], reviews, [], analytics=None,
                               snapshot_data=None, date_str="2026-06-11",
                               aging_info=aging_info)
    assert "### Appendix: Full Action Queue" not in "\n".join(lines)


def test_action_list_tags_three_day_ignored_and_promotes_to_top():
    # FFF would normally render LAST (and fall into the appendix); at 3 days
    # ignored it must carry the ⏳ tag and count within the top 5 first.
    key = "CLOSE:FFF_PUT_100_20260821"
    aging_info = {
        "today": "2026-06-11",
        "state": {key: {"first_flagged": "2026-06-09", "days_flagged": 2}},
        "reconciliation": {key: "IGNORED"},
    }
    lines = _render(aging_info)
    text = "\n".join(lines)
    assert "⏳ IGNORED 3 DAYS" in text
    headline = text.split("### Appendix: Full Action Queue")[0]
    assert "FFF_PUT_100_20260821" in headline
    first_item = next(l for l in lines if l.strip().startswith("1."))
    assert "FFF_PUT_100_20260821" in first_item


def test_action_list_day_five_binary_prompt():
    key = "CLOSE:AAA_PUT_100_20260821"
    aging_info = {
        "today": "2026-06-11",
        "state": {key: {"first_flagged": "2026-06-06", "days_flagged": 4}},
        "reconciliation": {key: "IGNORED"},
    }
    lines = _render(aging_info)
    text = "\n".join(lines)
    assert "⏳ IGNORED 5 DAYS" in text
    assert "DECISION REQUIRED" in text
    assert "file a directive" in text
    assert "will not silently repeat" in text


def test_action_list_legacy_behavior_without_aging_info():
    lines = render_action_list([], [_close_winner_review(t) for t in SIX_TICKERS],
                               [], analytics=None, snapshot_data=None,
                               date_str="2026-06-11")
    text = "\n".join(lines)
    assert "### Appendix: Full Action Queue" not in text  # no cap without aging
    assert "⏳" not in text


def test_stalled_item_flagged_through_render():
    key = "CLOSE:AAA_PUT_100_20260821"
    aging_info = {
        "today": "2026-06-11",
        "state": {key: {"first_flagged": "2026-06-05", "days_flagged": 5}},
        "reconciliation": {key: "IGNORED"},
    }
    _render(aging_info)
    assert aging_info["aged"][key]["stalled"] is True
    panel = render_stalled_panel(aging_info["aged"])
    assert "## ⛔ Stalled Items" in "\n".join(panel)


# ─────────────────────────────────────────────────────────────────────────────
# Diff panel wording
# ─────────────────────────────────────────────────────────────────────────────

YESTERDAY_MD = (
    "## Today's Action List\n\n"
    "1. **CLOSE** PLTR_PUT_135_20260605 — +30%\n"
    "2. **TRIM** NVDA — over cap\n"
    "## Watch\n"
)
TODAY_MD = (
    "## Today's Action List\n\n"
    "1. **TRIM** NVDA — over cap\n"
    "## Watch\n"
)


def test_diff_panel_uses_recon_status_when_available():
    recon = {"CLOSE:PLTR_PUT_135_20260605": "EXECUTED"}
    text = "\n".join(render_diff_panel(TODAY_MD, YESTERDAY_MD, recon_status=recon))
    assert "✅ EXECUTED" in text
    assert "likely executed" not in text


def test_diff_panel_unverified_and_legacy_fallback():
    recon = {"CLOSE:PLTR_PUT_135_20260605": "UNVERIFIED"}
    text = "\n".join(render_diff_panel(TODAY_MD, YESTERDAY_MD, recon_status=recon))
    assert "❔ UNVERIFIED" in text
    # No recon at all → legacy wording preserved
    text = "\n".join(render_diff_panel(TODAY_MD, YESTERDAY_MD))
    assert "likely executed or position closed" in text


# ─────────────────────────────────────────────────────────────────────────────
# State persistence round-trip
# ─────────────────────────────────────────────────────────────────────────────

def test_state_round_trip(tmp_path):
    path = tmp_path / "rec_aging.yaml"
    state = {"TRIM:NVDA": {"first_flagged": "2026-06-01", "days_flagged": 4,
                           "last_status": "IGNORED", "summary": "TRIM NVDA"}}
    save_state(path, state, updated="2026-06-11")
    loaded = load_state(path)
    assert loaded == state
    assert load_state(tmp_path / "missing.yaml") == {}


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
