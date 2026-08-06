"""Tests for the pre-trade validator. Each hard rule pinned by a focused test
so future refactors can't accidentally weaken the discipline."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pre_trade_validator as ptv  # noqa: E402


def _csp(**kw) -> ptv.PreTradeContext:
    """Default healthy CSP context — tests override the parts they care about."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    defaults = dict(
        ticker="CRM",
        strike=175.0,
        expiration=today + timedelta(days=35),
        option_type="PUT",
        action="SELL_OPEN",
        quantity=1,
        limit_price=3.00,
        spot=190.0,
        rsi=54,
        sma_50=185.0,
        sma_200=210.0,
        iv_rank=80,
        earnings_date=None,
        sr_payload={"supports": [{"price": 175.0, "touches": 4, "strength": 3.0}]},
        nlv=1_000_000.0,
        cash=80_000.0,
        stress_coverage=0.55,
        existing_short_puts=[],
        existing_long_puts=[],
        existing_short_calls=[],
        held_shares=0,
        obligation_by_expiration={},
        rating_tier=3,
    )
    defaults.update(kw)
    return ptv.PreTradeContext(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_healthy_csp_passes_all_checks():
    """A clean CRM CSP at support with all gates open should have zero blockers."""
    ctx = _csp()
    findings = ptv.validate_proposed_trade(ctx)
    assert not ptv.has_blockers(findings), \
        f"Expected no blockers; got: {[(f.severity, f.rule_id) for f in findings]}"


# ─────────────────────────────────────────────────────────────────────────────
# Rule 1 — Earnings window
# ─────────────────────────────────────────────────────────────────────────────

def test_earnings_inside_window_blocks():
    """The MU $960P case: earnings 9d out, expiration 67d out → BLOCK."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    ctx = _csp(
        ticker="MU",
        strike=960.0,
        expiration=today + timedelta(days=67),
        spot=1072.0,
        earnings_date=today + timedelta(days=9),
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "EARNINGS_WINDOW" in rules
    earnings = [f for f in findings if f.rule_id == "EARNINGS_WINDOW"][0]
    assert earnings.severity == ptv.SEV_BLOCK


def test_earnings_after_expiration_does_not_block():
    """Earnings AFTER expiration is fine — no binary risk inside the contract."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    ctx = _csp(
        expiration=today + timedelta(days=35),
        earnings_date=today + timedelta(days=60),
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "EARNINGS_WINDOW" not in rules


def test_no_earnings_date_does_not_block():
    """When earnings_date is None (unknown), no earnings check fires."""
    ctx = _csp(earnings_date=None)
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "EARNINGS_WINDOW" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2 — Entry gates (stress coverage)
# ─────────────────────────────────────────────────────────────────────────────

def test_entry_gates_closed_blocks():
    """Stress coverage < 0.50 must block new CSPs."""
    ctx = _csp(stress_coverage=0.11)
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "ENTRY_GATES_CLOSED") in rules


def test_entry_gates_open_at_threshold():
    """Exactly 0.50 coverage is the threshold — should NOT block."""
    ctx = _csp(stress_coverage=0.50)
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "ENTRY_GATES_CLOSED" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 3 — Cash floor
# ─────────────────────────────────────────────────────────────────────────────

def test_cash_floor_breach_blocks():
    """Cash < 5% NLV blocks new SELL_OPEN. Post-margin-call discipline."""
    ctx = _csp(nlv=1_000_000.0, cash=20_000.0)  # 2% — below floor
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "CASH_FLOOR") in rules


def test_cash_floor_above_threshold_passes():
    """Cash ≥ 5% NLV passes the gate."""
    ctx = _csp(nlv=1_000_000.0, cash=80_000.0)  # 8% — clear
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "CASH_FLOOR" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 4 — Expiration-bucket concentration
# ─────────────────────────────────────────────────────────────────────────────

def test_bucket_critical_blocks():
    """Aug 21 bucket currently $260K + new MU $96K → $356K = 35% NLV → CRITICAL."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    aug21 = today + timedelta(days=67)
    ctx = _csp(
        ticker="MU",
        strike=960.0,
        expiration=aug21,
        spot=1072.0,
        nlv=1_000_000.0,
        obligation_by_expiration={aug21: 260_000.0},
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "EXPIRATION_BUCKET_CRITICAL") in rules


def test_bucket_warning_warns():
    """Bucket landing between 20-30% NLV after adding → WARN (not BLOCK)."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    jul17 = today + timedelta(days=32)
    ctx = _csp(
        ticker="AVGO",
        strike=350.0,
        expiration=jul17,
        spot=400.0,
        nlv=1_000_000.0,
        obligation_by_expiration={jul17: 150_000.0},  # $185K projected = 18.5%, just under warn
    )
    # Adjust: make it land in 20-30% band
    ctx = _csp(
        ticker="AVGO",
        strike=350.0,
        expiration=jul17,
        spot=400.0,
        nlv=1_000_000.0,
        obligation_by_expiration={jul17: 170_000.0},  # $205K projected = 20.5% → WARN
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_WARN, "EXPIRATION_BUCKET_WARNING") in rules
    # Should NOT also fire CRITICAL
    assert not any(f.rule_id == "EXPIRATION_BUCKET_CRITICAL" for f in findings)


def test_bucket_clean_passes():
    """Small bucket addition passes both thresholds."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    sep18 = today + timedelta(days=95)
    ctx = _csp(
        ticker="SOFI",
        strike=15.0,
        expiration=sep18,
        spot=17.0,
        nlv=1_000_000.0,
        obligation_by_expiration={},  # empty bucket
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "EXPIRATION_BUCKET_CRITICAL" not in rules
    assert "EXPIRATION_BUCKET_WARNING" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 5 — Roll-from-safer detection
# ─────────────────────────────────────────────────────────────────────────────

def test_riskier_strike_warns():
    """The MU case: existing $890P at 17% OTM; proposed $960P at 10% OTM. Flag."""
    ctx = _csp(
        ticker="MU",
        strike=960.0,
        spot=1072.0,
        existing_short_puts=[{"strike": 890.0, "expiration": "2026-08-21"}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_WARN, "ROLL_UP_RISK_INCREASE") in rules


def test_lower_strike_no_warning():
    """If proposed strike is SAFER (further OTM) than existing, no warn."""
    ctx = _csp(
        ticker="MU",
        strike=800.0,
        spot=1072.0,
        existing_short_puts=[{"strike": 890.0}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "ROLL_UP_RISK_INCREASE" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 6 — Long-put cancellation
# ─────────────────────────────────────────────────────────────────────────────

def test_long_put_cancellation_blocks():
    """The META case: held LONG $570P; new short $570P would cancel the collar."""
    ctx = _csp(
        ticker="META",
        strike=570.0,
        existing_long_puts=[{"strike": 570.0}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "LONG_PUT_CANCELLATION") in rules


def test_long_put_far_strike_no_block():
    """Long $570P + new short $400P (different strike) — no cancellation."""
    ctx = _csp(
        ticker="META",
        strike=400.0,
        existing_long_puts=[{"strike": 570.0}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "LONG_PUT_CANCELLATION" not in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 7 — RSI overbought block for PUT
# ─────────────────────────────────────────────────────────────────────────────

def test_rsi_overbought_put_blocks():
    """RSI 75 + new PUT must block — thinnest premium before reversal."""
    ctx = _csp(rsi=75)
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "RSI_OVERBOUGHT_PUT") in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 8 — RSI oversold block for CALL
# ─────────────────────────────────────────────────────────────────────────────

def test_rsi_oversold_call_blocks():
    """RSI 30 + new CALL must block — would cap an oversold bounce."""
    ctx = _csp(option_type="CALL", rsi=30, strike=200.0)
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "RSI_OVERSOLD_CALL") in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 10 — Strike vs S/R proximity
# ─────────────────────────────────────────────────────────────────────────────

def test_strike_not_at_support_warns():
    """Strike well below all known supports → WARN (no anchor)."""
    ctx = _csp(
        strike=150.0,  # spot $190, supports at $175 — strike too far below
        spot=190.0,
        sr_payload={"supports": [{"price": 175.0, "touches": 3}]},
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_WARN, "STRIKE_NOT_AT_SUPPORT") in rules


def test_strike_at_support_no_warn():
    """Strike within 5% of a strong support → no warn."""
    ctx = _csp(
        strike=175.0,
        spot=190.0,
        sr_payload={"supports": [{"price": 175.0, "touches": 4}]},
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "STRIKE_NOT_AT_SUPPORT" not in rules


def test_strike_float_warn_says_no_multi_touch_within_5pct():
    """Task #31 sharpening: the float case names the precise failure —
    'no ≥2-touch support cluster within 5%', not just 'not anchored'."""
    ctx = _csp(
        strike=150.0,
        spot=190.0,
        sr_payload={"supports": [{"price": 175.0, "touches": 3}]},
    )
    findings = ptv.validate_proposed_trade(ctx)
    f = next(x for x in findings if x.rule_id == "STRIKE_NOT_AT_SUPPORT")
    assert "no ≥2-touch support cluster within 5%" in f.reason


def test_strike_single_touch_anchor_warns_with_level_named():
    """Task #31 sharpening: a strike anchored ONLY to a 1-touch level (e.g.
    the bare 52w-low) now WARNS and names the level — proximity alone is
    not an anchor (the CRWV $75P-on-$77-single-touch read)."""
    ctx = _csp(
        strike=75.0,
        spot=90.0,
        sr_payload={"supports": [{"price": 77.0, "touches": 1}]},
    )
    findings = ptv.validate_proposed_trade(ctx)
    f = next(x for x in findings if x.rule_id == "STRIKE_NOT_AT_SUPPORT")
    assert f.severity == ptv.SEV_WARN
    assert "anchored only to a 1-touch cluster at $77" in f.reason


# ─────────────────────────────────────────────────────────────────────────────
# Composite scenarios — pin the actual case-studies
# ─────────────────────────────────────────────────────────────────────────────

def test_mu_960p_aug21_scenario_blocks_with_multiple_reasons():
    """The actual MU $960P Aug 21 case the user surfaced — should block on
    MULTIPLE rules: earnings window + entry gates + roll-from-safer."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    aug21 = today + timedelta(days=67)
    ctx = _csp(
        ticker="MU",
        strike=960.0,
        expiration=aug21,
        spot=1072.0,
        rsi=65,
        earnings_date=today + timedelta(days=9),
        nlv=1_075_098.0,
        cash=84_177.0,  # 7.8% — passes cash floor
        stress_coverage=0.11,  # FAILS entry gates
        obligation_by_expiration={aug21: 317_500.0},  # CRITICAL bucket
        existing_short_puts=[{"strike": 890.0}],  # was held at safer strike
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = {f.rule_id for f in findings}
    # All four discipline failures should fire:
    assert "EARNINGS_WINDOW" in rules
    assert "ENTRY_GATES_CLOSED" in rules
    assert "EXPIRATION_BUCKET_CRITICAL" in rules
    assert "ROLL_UP_RISK_INCREASE" in rules
    # System would NOT execute this trade:
    assert ptv.has_blockers(findings)


def test_findings_sorted_blockers_first():
    """Findings list returns BLOCKs before WARNs (consistent renderer order)."""
    ctx = _csp(
        rsi=75,                              # BLOCK
        existing_short_puts=[{"strike": 200}],  # WARN (riskier)
        strike=175.0,
        spot=190.0,
    )
    findings = ptv.validate_proposed_trade(ctx)
    severities = [f.severity for f in findings]
    block_idx = severities.index(ptv.SEV_BLOCK) if ptv.SEV_BLOCK in severities else 99
    warn_idx = severities.index(ptv.SEV_WARN) if ptv.SEV_WARN in severities else 99
    assert block_idx < warn_idx


# ─────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_format_findings_md_renders_severity_emojis():
    findings = [
        ptv.TradeValidation(severity=ptv.SEV_BLOCK, reason="blocker", detail="...", rule_id="X"),
        ptv.TradeValidation(severity=ptv.SEV_WARN, reason="warner", detail="...", rule_id="Y"),
    ]
    out = ptv.format_findings_md(findings, trade_label="TEST $100P")
    rendered = "\n".join(out)
    assert "🚫" in rendered
    assert "⚠️" in rendered
    assert "**BLOCK**" in rendered
    assert "**WARN**" in rendered


def test_format_empty_silent_unless_show_passing():
    """Empty findings render nothing by default — keeps the briefing clean."""
    out = ptv.format_findings_md([], trade_label="CRM $175P", show_passing=False)
    assert out == []
    out2 = ptv.format_findings_md([], trade_label="CRM $175P", show_passing=True)
    assert any("all discipline checks pass" in line for line in out2)


# ─────────────────────────────────────────────────────────────────────────────
# Rule 11 — CC writability (NAKED CALL prevention)
# ─────────────────────────────────────────────────────────────────────────────

def test_cc_naked_call_blocks():
    """Held 100 shares, 1 existing CC + 1 new CC = 200 shares needed → NAKED."""
    ctx = _csp(
        option_type="CALL",
        strike=250.0,
        rsi=65,  # so RSI gate doesn't fire (passes the oversold-call block)
        held_shares=100,
        existing_short_calls=[{"strike": 200.0, "qty": 1}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "NAKED_CALL_EXPOSURE") in rules


def test_cc_writable_no_block():
    """700 shares + 1 existing CC + 1 new = 200 needed. 700 ≥ 200. Safe."""
    ctx = _csp(
        option_type="CALL",
        strike=230.0,
        rsi=65,
        held_shares=700,
        existing_short_calls=[{"strike": 270.0, "qty": 1}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "NAKED_CALL_EXPOSURE" not in rules


def test_cc_exact_coverage_no_block():
    """200 shares + 1 existing CC + 1 new CC = exactly covered."""
    ctx = _csp(
        option_type="CALL",
        strike=230.0,
        rsi=65,
        held_shares=200,
        existing_short_calls=[{"strike": 200.0, "qty": 1}],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "NAKED_CALL_EXPOSURE" not in rules


def test_cc_multi_contract_writability():
    """100 shares + proposing 3 new CCs → naked 200 shares."""
    ctx = _csp(
        option_type="CALL",
        strike=230.0,
        quantity=3,
        rsi=65,
        held_shares=100,
        existing_short_calls=[],
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "NAKED_CALL_EXPOSURE") in rules


# ─────────────────────────────────────────────────────────────────────────────
# Rule 12 — Limit price sanity (stale-order detection)
# ─────────────────────────────────────────────────────────────────────────────

def test_stale_limit_above_mid_warns():
    """The MU $154 case: limit way above current mid → won't fill."""
    ctx = _csp(
        limit_price=154.00,
        current_mid_hint=120.00,  # 28% below the limit
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_WARN, "STALE_LIMIT_PRICE") in rules


def test_stale_limit_below_mid_warns():
    """Limit way below mid — would fill at terrible price for SELL_OPEN."""
    ctx = _csp(
        limit_price=2.00,
        current_mid_hint=3.00,  # 33% above the limit
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_WARN, "STALE_LIMIT_PRICE") in rules


def test_limit_close_to_mid_no_warn():
    """Limit within tolerance — no warn."""
    ctx = _csp(
        limit_price=3.00,
        current_mid_hint=3.05,  # 1.6% off
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "STALE_LIMIT_PRICE" not in rules


def test_no_mid_hint_skips_check():
    """When current_mid_hint is None, Rule 12 is silent (no false positive)."""
    ctx = _csp(
        limit_price=154.00,
        current_mid_hint=None,
    )
    findings = ptv.validate_proposed_trade(ctx)
    rules = [f.rule_id for f in findings]
    assert "STALE_LIMIT_PRICE" not in rules


def test_custom_stale_threshold():
    """Config can tune the staleness threshold."""
    ctx = _csp(
        limit_price=110.0,
        current_mid_hint=100.0,  # 10% off
    )
    # Default 20% — no warn
    findings = ptv.validate_proposed_trade(ctx)
    assert "STALE_LIMIT_PRICE" not in [f.rule_id for f in findings]
    # Strict 5% — warn fires
    findings_strict = ptv.validate_proposed_trade(ctx, config={"limit_sanity_pct": 0.05})
    assert "STALE_LIMIT_PRICE" in [f.rule_id for f in findings_strict]


# ─────────────────────────────────────────────────────────────────────────────
# Rule 13 — Covered-call tier discipline (CLAUDE.md hard rule #29)
# ─────────────────────────────────────────────────────────────────────────────

_TIER_CFG = {
    "position_tiers": {
        "tier_a_core": ["NVDA", "GOOG", "MSFT", "META", "PLTR", "AMZN", "SPY", "VOO"],
        "tier_b_income": ["MU", "SMH"],
    },
    "covered_call_tiers": {
        "tier_a": {"enabled": False, "max_delta": 0.0, "min_otm_pct": 999,
                   "coverage_cap_pct": 0, "max_dte": 0, "rsi_floor": 999,
                   "roll_up_trigger": 0.92, "tax_aware_assignment_block": True},
        "tier_b": {"enabled": True, "max_delta": 0.15, "min_otm_pct": 10.0,
                   "coverage_cap_pct": 50, "max_dte": 30, "rsi_floor": 70,
                   "roll_up_trigger": 0.93, "tax_aware_assignment_block": True},
        "tier_c": {"enabled": True, "max_delta": 0.30, "min_otm_pct": 4.0,
                   "coverage_cap_pct": 100, "max_dte": 45, "rsi_floor": 60,
                   "roll_up_trigger": 0.97, "tax_aware_assignment_block": False},
    },
}


def _cc(**kw):
    """Default healthy CC context — tests override the parts they care about."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    defaults = dict(
        ticker="VRT",
        strike=140.0,
        expiration=today + timedelta(days=35),
        option_type="CALL",
        action="SELL_OPEN",
        quantity=1,
        spot=130.0,                # ~7.7% OTM at strike 140
        rsi=65,                    # passes the global ≥60 CC RSI gate
        held_shares=300,
        existing_short_calls=[],
        nlv=1_000_000.0,
        cash=80_000.0,
        sr_payload=None,
        position_tier=None,
    )
    defaults.update(kw)
    return ptv.PreTradeContext(**defaults)


def test_tier_a_cc_blocked():
    """A CC on NVDA (Tier A) — system policy is NEVER write CCs on core
    compounders. Rule 13 BLOCKs the trade regardless of how clean it looks."""
    ctx = _cc(ticker="NVDA", strike=1100.0, spot=1000.0, held_shares=700)
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "COVERED_CALL_TIER_VIOLATION") in rules
    block = [f for f in findings if f.rule_id == "COVERED_CALL_TIER_VIOLATION"][0]
    assert "Tier A" in block.reason


def test_tier_b_cc_at_high_delta_blocked():
    """A CC on MU (Tier B) at 0.25 delta — exceeds the tier-B cap of 0.15
    → BLOCK. Strategy_upgrades selects a tier-compliant strike upstream,
    but if anything bypasses that (manual entry / pending order audit),
    the validator catches it here."""
    ctx = _cc(
        ticker="MU", strike=135.0, spot=120.0,  # ~12.5% OTM — passes OTM floor
        held_shares=700, quantity=1,
        sr_payload={"proposed_delta": 0.25},    # caller's chain quote delta
    )
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "COVERED_CALL_TIER_VIOLATION") in rules
    block = [f for f in findings if f.rule_id == "COVERED_CALL_TIER_VIOLATION"][0]
    assert "delta 0.25" in block.reason


def test_tier_b_cc_strike_too_close_blocked():
    """A CC on MU (Tier B) at 5% OTM — violates the 10% floor → BLOCK."""
    ctx = _cc(
        ticker="MU", strike=126.0, spot=120.0,  # 5% OTM — fails the 10% floor
        held_shares=700, quantity=1,
    )
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "COVERED_CALL_TIER_VIOLATION") in rules


def test_tier_b_cc_too_many_contracts_blocked():
    """Tier B 50% coverage of 7 round lots = 3 contracts. Proposing 5
    contracts exceeds the cap → BLOCK."""
    ctx = _cc(
        ticker="MU", strike=135.0, spot=120.0,
        held_shares=700, quantity=5,           # 7 lots * 50% = 3 contracts max
    )
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "COVERED_CALL_TIER_VIOLATION") in rules
    block = [f for f in findings if f.rule_id == "COVERED_CALL_TIER_VIOLATION"][0]
    assert "coverage cap" in block.reason


def test_tier_c_cc_at_25_delta_passes():
    """A CC on VRT (Tier C — default) at 0.25 delta — under the 0.30 cap →
    Rule 13 does NOT fire."""
    ctx = _cc(
        ticker="VRT", strike=140.0, spot=130.0,  # ~7.7% OTM, above 4% floor
        held_shares=300, quantity=1,
        sr_payload={"proposed_delta": 0.25},
    )
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [f.rule_id for f in findings]
    assert "COVERED_CALL_TIER_VIOLATION" not in rules


def test_tier_rule_silent_without_config():
    """No `position_tiers` config AND no `ctx.position_tier` → Rule 13 stays
    silent. Backward compatible — pre-framework callers see no behavior change."""
    ctx = _cc(ticker="NVDA", strike=1100.0, spot=1000.0, held_shares=700)
    findings = ptv.validate_proposed_trade(ctx)  # no config
    rules = [f.rule_id for f in findings]
    assert "COVERED_CALL_TIER_VIOLATION" not in rules


def test_tier_rule_uses_ctx_position_tier_when_provided():
    """ctx.position_tier='A' triggers Rule 13 even without `position_tiers`
    in config — supports callers that already classify the ticker themselves."""
    ctx = _cc(
        ticker="ANYNAME", strike=1100.0, spot=1000.0, held_shares=700,
        position_tier="A",
    )
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [(f.severity, f.rule_id) for f in findings]
    assert (ptv.SEV_BLOCK, "COVERED_CALL_TIER_VIOLATION") in rules


def test_tier_a_block_is_separate_from_naked_call_block():
    """A Tier-A CC with sufficient coverage still BLOCKs on Rule 13 — the
    tier discipline fires independently of naked-call exposure (Rule 11)."""
    ctx = _cc(ticker="NVDA", strike=1100.0, spot=1000.0, held_shares=700,
              quantity=1, existing_short_calls=[])
    findings = ptv.validate_proposed_trade(ctx, config=_TIER_CFG)
    rules = [f.rule_id for f in findings]
    assert "COVERED_CALL_TIER_VIOLATION" in rules
    assert "NAKED_CALL_EXPOSURE" not in rules  # coverage is fine, just policy


# ─────────────────────────────────────────────────────────────────────────────
# Bug #23 — projected_state (rotation playbook Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

def test_cash_floor_uses_projected_when_provided():
    """Bug #23 symptom: 'every candidate hits 🚫 BLOCK (CASH_FLOOR) — 4.6% NLV
    < 5% floor', but that block is TRUE pre-close and FALSE post-Phase-1.
    With projected_state, the cash-floor gate reads the PROJECTED cash."""
    ctx = _csp(nlv=1_000_000.0, cash=46_000.0)   # 4.6% — blocks pre-close
    assert (ptv.SEV_BLOCK, "CASH_FLOOR") in [
        (f.severity, f.rule_id) for f in ptv.validate_proposed_trade(ctx)]
    # Projected post-close: 46K + 184.5K freed = 230.5K = 23% NLV → passes.
    findings = ptv.validate_proposed_trade(
        ctx, projected_state={"nlv": 1_000_000.0, "cash": 230_500.0})
    assert "CASH_FLOOR" not in [f.rule_id for f in findings]
    # And the projection can also make it WORSE — a bad projected cash blocks
    # even when the pre-close ctx cash looks fine.
    ctx2 = _csp(nlv=1_000_000.0, cash=200_000.0)
    findings2 = ptv.validate_proposed_trade(
        ctx2, projected_state={"nlv": 1_000_000.0, "cash": 30_000.0})
    assert (ptv.SEV_BLOCK, "CASH_FLOOR") in [
        (f.severity, f.rule_id) for f in findings2]


def test_entry_gates_use_projected_coverage_when_provided():
    """Projected coverage above the 0.50× floor reopens the gate that the
    stale pre-close ratio would have closed."""
    ctx = _csp(stress_coverage=0.10)              # closed pre-close
    findings = ptv.validate_proposed_trade(
        ctx, projected_state={"coverage_ratio": 0.55})
    assert "ENTRY_GATES_CLOSED" not in [f.rule_id for f in findings]
    # Missing projected coverage → gate silenced (fail-open), never falls
    # back to the stale pre-close 0.10×.
    findings2 = ptv.validate_proposed_trade(ctx, projected_state={})
    assert "ENTRY_GATES_CLOSED" not in [f.rule_id for f in findings2]


def test_earnings_window_ignores_projected_state():
    """Position-shape gates are NOT sensitive to projected state — earnings
    inside the contract window blocks no matter how much cash Phase 1 frees."""
    today = date.today()
    ctx = _csp(
        expiration=today + timedelta(days=35),
        earnings_date=today + timedelta(days=9),
    )
    findings = ptv.validate_proposed_trade(
        ctx,
        projected_state={"nlv": 1_000_000.0, "cash": 500_000.0,
                         "coverage_ratio": 2.0},
    )
    assert (ptv.SEV_BLOCK, "EARNINGS_WINDOW") in [
        (f.severity, f.rule_id) for f in findings]


def test_projected_state_none_is_byte_identical():
    """projected_state=None → current behavior, byte-identical findings."""
    ctx = _csp(nlv=1_000_000.0, cash=20_000.0, stress_coverage=0.11)
    base = ptv.validate_proposed_trade(ctx)
    with_kwarg = ptv.validate_proposed_trade(ctx, projected_state=None)
    assert [(f.severity, f.rule_id, f.reason, f.detail) for f in base] == \
           [(f.severity, f.rule_id, f.reason, f.detail) for f in with_kwarg]


def test_bucket_gate_uses_projected_map_when_provided():
    """The bucket gate reads the PROJECTED per-date obligation map — a bucket
    the Phase-1 closes empty no longer blocks; a missing map silences the
    gate rather than re-blocking on the pre-close book."""
    today = date.today()  # was pinned date(2026, 6, 15); production uses date.today() — pinned fixtures went stale (observed 2026-08-06: EARNINGS_WINDOW never fired because the fixture's earnings date was already in the past)
    exp = today + timedelta(days=35)
    ctx = _csp(expiration=exp,
               obligation_by_expiration={exp: 340_000.0})   # 34% + new → crit
    assert "EXPIRATION_BUCKET_CRITICAL" in [
        f.rule_id for f in ptv.validate_proposed_trade(ctx)]
    # Post-close the bucket is nearly empty → no block.
    findings = ptv.validate_proposed_trade(
        ctx, projected_state={"nlv": 1_000_000.0, "cash": 300_000.0,
                              "obligation_by_expiration": {exp: 40_000.0}})
    assert "EXPIRATION_BUCKET_CRITICAL" not in [f.rule_id for f in findings]
    # Projection without a map → bucket gate silent (fail-open).
    findings2 = ptv.validate_proposed_trade(
        ctx, projected_state={"nlv": 1_000_000.0, "cash": 300_000.0})
    assert "EXPIRATION_BUCKET_CRITICAL" not in [f.rule_id for f in findings2]
