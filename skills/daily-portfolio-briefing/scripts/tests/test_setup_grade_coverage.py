"""Setup Grade coverage sweep — grade on EVERY new-open recommendation.

George (2026-08-12): "Let's also add a grade to every recommendation that
you're giving so that I know it's a good recommendation."

``analysis/setup_grade.py::audit_missing_grade`` scans the RENDERED
briefing for new-open option tickets (SELL N× / SELL TO OPEN / strangle
Add N× / BUY N× debits) whose card carries no Setup Grade token, plus
numbered action-list MANAGEMENT items that carry no decision token
(⚖️ Verdict / capture % / Why: / advisor rec). Management lines are
exempt from Setup Grades BY DESIGN — they carry verdicts, not entry
grades. Wired into steps/aggregate.py as the '🏁 Setup Grade Coverage'
panel (mirror of the RSI Coverage Check).

Real-render smoke: the delivered ``briefing_full_2026-08-12.md`` (fixture)
had exactly ONE straggler — the covered-strangle put add rendered with no
grade — which the strangle grading in steps/strategy_upgrades.py now
fixes; with the grade line inserted the sweep is 0/0.
"""

from pathlib import Path

from analysis.setup_grade import GRADE_NA_NOTE, audit_missing_grade

FIXTURE = Path(__file__).parent / "fixtures" / "briefing_full_2026-08-12.md"

_GRADE_LINE = ("**Setup Grade: B** (68/100) · RSI 41 prime · RVr 79 ✓ — "
               "🏁 Entry: B — good setup; RSI 41 prime. Enter per plan.")
_FLOOR_NOTE = ("⏸ Below setup floor — C (54): RSI 50 mid-range ✗ · IVr 22 "
               "thin ✗ — actionable at B (65).")


def _card(*lines: str) -> str:
    return "\n".join(["## Income Opportunities", "", *lines, ""])


# ── New-open tickets ─────────────────────────────────────────────────────


def test_ungraded_sell_ticket_flagged():
    """George: "add a grade to every recommendation" — a composed SELL
    ticket with no Setup Grade token anywhere in its card is flagged."""
    md = _card(
        "**🎯 CANDIDATE · `ABC` · $105.00**",
        "  - RSI 44 · RVrank 74",
        "  - SELL 1× ABC $100P exp **Fri Sep 18 '26** (37 DTE, monthly) "
        "· mid $2.10 · _Live E*TRADE chain_",
    )
    result = audit_missing_grade(md)
    assert len(result["new_open"]) == 1
    assert "SELL 1× ABC $100P" in result["new_open"][0]
    assert result["management"] == []


def test_graded_ticket_clean():
    md = _card(
        "**🎯 CANDIDATE · `ABC` · $105.00**",
        f"  - {_GRADE_LINE}",
        "  - SELL 1× ABC $100P exp **Fri Sep 18 '26** (37 DTE, monthly) "
        "· mid $2.10 · _Live E*TRADE chain_",
    )
    result = audit_missing_grade(md)
    assert result["new_open"] == []


def test_below_floor_note_clean():
    """A B-floor demotion IS grade coverage — the ticket was graded and
    demoted, not left ungraded."""
    md = _card(
        "**ABC — 200 shares** ⏸ BELOW SETUP FLOOR",
        "  - SELL 2× ABC $108C exp Fri Sep 18 '26 (37 DTE, monthly)",
        f"  - **{_FLOOR_NOTE}**",
    )
    assert audit_missing_grade(md)["new_open"] == []


def test_grade_na_note_clean():
    """Fail-open verify note (rule #19) counts as coverage — the surface
    consulted the grader and rendered the honest n/a."""
    md = _card(
        "**LEAP — NVDA**",
        "  - BUY 1× NVDA $200C exp Fri Jan 15 '27",
        f"  - {GRADE_NA_NOTE}",
    )
    assert audit_missing_grade(md)["new_open"] == []


def test_strangle_add_ticket_requires_grade():
    """The 2026-08-12 straggler shape: 'Add 1× $500P exp Fri Nov 20 '26
    (100d) @ $17.12 mid (δ-0.20)' with no grade in the card → flagged."""
    md = _card(
        "**SMH — 1.0x $710.0C exp 2026-11-20 → add 1x $500P** ✅ OK",
        "  - **RSI:** RSI 54 — neutral",
        "  - Add 1× $500P exp Fri Nov 20 '26 (100d) @ $17.12 mid (δ-0.20)",
        "  - New premium: $1,712 (12% ann on $50,000)",
    )
    result = audit_missing_grade(md)
    assert len(result["new_open"]) == 1
    assert "Add 1× $500P" in result["new_open"][0]


# ── Exemptions ───────────────────────────────────────────────────────────


def test_roll_combo_in_code_fence_exempt():
    """Two-leg roll combo tickets are POSITION MANAGEMENT — the fenced
    'SELL TO OPEN' leg never needs an entry grade."""
    md = _card(
        "  **IF ROLLING anyway, prefer B:**",
        "",
        "```",
        "BUY TO CLOSE   1 × ZS  Fri Sep 11 '26  $190 CALL   Limit $11.50  GTC",
        "SELL TO OPEN   1 × ZS  Fri Sep 18 '26  $190 CALL   Limit $10.45  GTC",
        "NET CREDIT TARGET: $35",
        "```",
    )
    assert audit_missing_grade(md)["new_open"] == []


def test_collar_protective_put_leg_exempt():
    """Collar / protective-put buys are hedges (management), not entry
    recommendations — exempt by design even in ticket grammar."""
    md = _card(
        "**MSFT — 241 shares, +$29,674 unrealized (+33%)**",
        "  - RSI: RSI 72 🔴 overbought (protective put — shown for context)",
        "  - BUY 2× MSFT $440P exp Fri Sep 11 '26 (30d) @ $1.54 mid (δ-0.09)",
        "  - Floor: $440 (11% below current)",
    )
    assert audit_missing_grade(md)["new_open"] == []


def test_italic_footer_and_table_rows_exempt():
    md = _card(
        "| C | 1× $190C Jun 16 '28 @ $60.50 | SELL 1× extension row |",
        "_📉 SELL 1× SPY $700P rejected by trade-validator — footer only_",
    )
    assert audit_missing_grade(md)["new_open"] == []


def test_capital_plan_rollup_exempt():
    md = "\n".join([
        "## 💰 Capital Plan", "",
        "- SELL 1× ABC $100P exp Fri Sep 18 '26 — rollup of a rec graded "
        "above", "",
    ])
    assert audit_missing_grade(md)["new_open"] == []


def test_grade_token_outside_card_does_not_count():
    """A 🏁 token in a DIFFERENT card (separated by a blank line) must not
    satisfy an ungraded ticket — the window is the ticket's own card."""
    md = _card(
        "**`XYZ` graded card**",
        f"  - {_GRADE_LINE}",
        "",
        "**`ABC` ungraded card**",
        "  - SELL 1× ABC $100P exp Fri Sep 18 '26 (37 DTE) · mid $2.10",
    )
    result = audit_missing_grade(md)
    assert len(result["new_open"]) == 1
    assert "ABC" in result["new_open"][0]


# ── Management lines (exempt from grades; must carry verdicts) ───────────


def test_management_close_with_verdict_clean():
    """Management lines carry decision tokens, not Setup Grades — a CLOSE
    with a ⚖️ Verdict (or capture %) is fully compliant ungraded."""
    md = "\n".join([
        "## Today's Action List — Wednesday, August 12, 2026", "",
        "1. **CLOSE** SMH_CALL_710_20261120 — +35% ($+702); buy-to-close "
        "limit $13.89",
        "   - Yield: **12.0%** ann. on collateral (35% of max profit "
        "captured).",
        "   - **Why:** close-at-50% rule.", "",
    ])
    result = audit_missing_grade(md)
    assert result["management"] == []
    assert result["new_open"] == []


def test_bare_management_line_flagged():
    """George's 'every recommendation' intent: a management item with NO
    decision token (no ⚖️ Verdict, no capture %, no Why:, no advisor rec)
    renders bare → flagged in the second list, never the first."""
    md = "\n".join([
        "## Today's Action List — Wednesday, August 12, 2026", "",
        "1. **CLOSE** ABC_PUT_100_20261218 — buy-to-close limit $1.25",
        "   - Delta -0.46 (~46% ITM probability)", "",
    ])
    result = audit_missing_grade(md)
    assert len(result["management"]) == 1
    assert "**CLOSE** ABC_PUT_100_20261218" in result["management"][0]
    assert result["new_open"] == []


def test_management_verdict_never_bleeds_from_neighbor_item():
    """Adjacent items are contiguous (no blank line) — a bare item must
    not pass on the NEXT item's ⚖️ Verdict."""
    md = "\n".join([
        "## Today's Action List", "",
        "1. **CLOSE** ABC_PUT_100_20261218 — buy-to-close limit $1.25",
        "2. **EXECUTE ROLL** XYZ_PUT_50_20261218 — roll down-and-out",
        "   - **⚖️ Verdict: ROLL, don't close** — extrinsic is IV-pumped.", "",
    ])
    result = audit_missing_grade(md)
    assert len(result["management"]) == 1
    assert "ABC_PUT_100" in result["management"][0]


def test_management_item_with_ticket_text_not_in_new_open():
    """A numbered management item mentioning a SELL N× leg is management —
    exempt from the grade requirement (it needs a verdict instead)."""
    md = "\n".join([
        "## Today's Action List", "",
        "1. **TAKE PROFIT** ABC — close then SELL 1× ABC $95P re-entry per "
        "roll table",
        "   - **⚖️ Verdict: TAKE PROFIT** — 78% captured.", "",
    ])
    result = audit_missing_grade(md)
    assert result["new_open"] == []
    assert result["management"] == []


# ── Real-render smoke (delivered 2026-08-12 briefing) ────────────────────


def test_delivered_2026_08_12_only_straggler_was_strangle_add():
    """Sweeping the DELIVERED briefing_full_2026-08-12.md (rendered before
    the strangle grading landed) finds exactly one ungraded new-open
    ticket — the covered-strangle put add 'Add 1× $500P exp Fri Nov 20
    '26 (100d) @ $17.12 mid (δ-0.20)' — and zero bare management lines.
    Every other surface (candidates, income, LT_CSP, CC / index CC, best
    setups) already carried its grade."""
    result = audit_missing_grade(FIXTURE.read_text())
    assert len(result["new_open"]) == 1
    assert "Add 1× $500P" in result["new_open"][0]
    assert result["management"] == []


def test_delivered_2026_08_12_clean_with_strangle_grade_fix():
    """With the fix (steps/strategy_upgrades.py grades the strangle put
    add; render/strategy_upgrades_panel.py renders setup_grade_line inside
    the card) the same render sweeps 0/0."""
    md = FIXTURE.read_text().replace(
        "  - Add 1× $500P exp Fri Nov 20 '26 (100d) @ $17.12 mid (δ-0.20)",
        f"  - {_GRADE_LINE}\n"
        "  - Add 1× $500P exp Fri Nov 20 '26 (100d) @ $17.12 mid (δ-0.20)",
    )
    result = audit_missing_grade(md)
    assert result["new_open"] == []
    assert result["management"] == []


def test_strangle_panel_output_passes_sweep_when_graded():
    """End-to-end: the FIXED strangle renderer's own output (grade line
    inside the card) passes the sweep."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = {
        "type": "covered_strangle", "underlying": "SMH",
        "rsi_14": 54.0, "rsi_tag": "RSI 54", "rsi_note": "neutral",
        "rsi_decision": "keep", "rsi_badge": None, "rsi_blocked": False,
        "current_calls": "1.0x $710.0C exp 2026-11-20",
        "proposed": {"action": "SELL_TO_OPEN", "qty": 1, "strike": 500.0,
                     "expiration": "2026-11-20", "dte": 100, "delta": -0.2,
                     "premium_per_contract": 17.12, "total_premium": 1712.0},
        "yield_annualized": 0.12, "collateral_required": 50000.0,
        "concentration_check": {"current_pct": 4.0, "post_action_pct": 8.0,
                                "blocked": False, "reason": None},
        "combined_income": {"calls": 1322.0, "puts": 1712.0, "total": 3035.0},
        "rationale": "strangle",
        "setup_grade": "B", "setup_grade_score": 68.0,
        "setup_grade_message": "🏁 Entry: B — good setup.",
        "setup_grade_line": _GRADE_LINE,
    }
    md = "\n".join(render_strategy_upgrades([up]))
    assert _GRADE_LINE in md
    assert audit_missing_grade(md)["new_open"] == []


# ── Spread composer (live-mode tickets inherit the CSP's grade) ──────────


def _spread_idea(**kw):
    base = {
        "instruction": "sell put", "ticker": "CGNX", "strike": 55.0,
        "expiration": "2026-09-18", "dte": 37, "mid": 1.23,
        "premium": 123.0, "collateral": 5500.0, "annualized_pct": 22.0,
        "setup_grade": "B", "setup_grade_line": _GRADE_LINE,
    }
    base.update(kw)
    return base


def _spread_chain():
    return {"CGNX_2026-09-18": {
        "underlying": "CGNX", "expiration": "2026-09-18",
        "puts": [{"strike": 55.0, "bid": 1.15, "ask": 1.31},
                 {"strike": 45.0, "bid": 0.40, "ask": 0.50}],
    }}


def test_spread_live_ticket_inherits_and_renders_grade():
    """A live-mode spread ticket is a NEW open — the short leg IS the
    graded CSP entry, so the two-leg order carries its grade and passes
    the sweep."""
    from analysis import spread_composer as spc
    cfg = {"spread": {"enabled": True, "mode": "live", "width_target": 10,
                      "min_credit_pct_of_width": 0.05}}
    composed = spc.compose_spreads([_spread_idea()], _spread_chain(), cfg)
    assert composed["spreads"], composed
    assert composed["spreads"][0]["setup_grade_line"] == _GRADE_LINE
    md = "\n".join(spc.render_spreads_section(composed, cfg))
    assert "SELL TO OPEN" in md
    assert _GRADE_LINE in md
    assert audit_missing_grade(md)["new_open"] == []


def test_spread_live_ticket_ungraded_renders_na_note():
    """Rule #19 fail direction: no inherited grade → the live ticket
    renders the honest verify-manually note (never silently ungraded) and
    still passes the sweep."""
    from analysis import spread_composer as spc
    cfg = {"spread": {"enabled": True, "mode": "live", "width_target": 10,
                      "min_credit_pct_of_width": 0.05}}
    idea = _spread_idea()
    del idea["setup_grade"], idea["setup_grade_line"]
    composed = spc.compose_spreads([idea], _spread_chain(), cfg)
    md = "\n".join(spc.render_spreads_section(composed, cfg))
    assert "SELL TO OPEN" in md
    assert GRADE_NA_NOTE in md
    assert audit_missing_grade(md)["new_open"] == []


def test_spread_reference_mode_unchanged_no_ticket_no_grade_needed():
    """Reference (paper-watch) mode composes no order legs — nothing for
    the sweep to flag, and no grade line is forced into the watch card."""
    from analysis import spread_composer as spc
    cfg = {"spread": {"enabled": True, "mode": "reference",
                      "width_target": 10, "min_credit_pct_of_width": 0.05}}
    composed = spc.compose_spreads([_spread_idea()], _spread_chain(), cfg)
    md = "\n".join(spc.render_spreads_section(composed, cfg))
    assert "SELL TO OPEN" not in md
    assert audit_missing_grade(md)["new_open"] == []
