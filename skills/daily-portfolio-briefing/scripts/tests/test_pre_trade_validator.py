"""Tests for the pre-trade validator. Each hard rule pinned by a focused test
so future refactors can't accidentally weaken the discipline."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pre_trade_validator as ptv  # noqa: E402


def _csp(**kw) -> ptv.PreTradeContext:
    """Default healthy CSP context — tests override the parts they care about."""
    today = date(2026, 6, 15)
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
    today = date(2026, 6, 15)
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
    today = date(2026, 6, 15)
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
    today = date(2026, 6, 15)
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
    today = date(2026, 6, 15)
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
    today = date(2026, 6, 15)
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


# ─────────────────────────────────────────────────────────────────────────────
# Composite scenarios — pin the actual case-studies
# ─────────────────────────────────────────────────────────────────────────────

def test_mu_960p_aug21_scenario_blocks_with_multiple_reasons():
    """The actual MU $960P Aug 21 case the user surfaced — should block on
    MULTIPLE rules: earnings window + entry gates + roll-from-safer."""
    today = date(2026, 6, 15)
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
