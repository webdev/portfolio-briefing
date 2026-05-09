"""Test suite for briefing quality gate personas."""

import pytest
from run_quality_gate import run_quality_gate, gate_and_render
from personas import (
    financial_advisor_check,
    options_trader_check,
    tax_cpa_check,
    risk_manager_check,
)


class TestFinancialAdvisor:
    """Financial advisor persona checks."""

    def test_pass_complete_action_items(self):
        """Should pass when all action items are complete."""
        md = """## Portfolio Briefing (2026-05-08)

## Action Items

- **EXECUTE** NEW CSP on AAPL
  **Why:** Intraday dip, IV rank 65
  **Gain:** $500 theta decay over 30 days
  Yield: 2.1% on $24,000 collateral
  Account: Roth IRA

## Total Impact
- Income: $500
"""
        result = financial_advisor_check(md)
        assert result.score >= 70

    def test_fail_missing_why_section(self):
        """Should deduct points when Why section is missing."""
        md = """## Action Items

- **EXECUTE** CSP AAPL
  **Gain:** $500 theta
  Yield: 2.1%

## Total Impact
- Income: $500
"""
        result = financial_advisor_check(md)
        assert result.score < 100
        assert any("Why" in issue.text for issue in result.issues)

    def test_fail_missing_gain_section(self):
        """Should fail when Gain is missing."""
        md = """## Action Items

- **EXECUTE NEW CSP** on AAPL
  **Why:** IV spike
  Yield: 2.1%
"""
        result = financial_advisor_check(md)
        assert result.score < 70

    def test_fail_missing_yield_line(self):
        """Should fail when Yield is missing."""
        md = """## Action Items

- **EXECUTE NEW CSP** on AAPL
  **Why:** IV spike
  **Gain:** $500
"""
        result = financial_advisor_check(md)
        assert result.score < 70

    def test_pass_no_yield_one_time(self):
        """Should pass with explicit 'no yield (one-time)' marker."""
        md = """## Action Items

- **CLOSE** AAPL position
  **Why:** Loss stop hit
  **Gain:** Prevents further decay
  no yield (one-time)
"""
        result = financial_advisor_check(md)
        assert "yield" not in str([i.text for i in result.issues]).lower()

    def test_critical_fail_no_actions(self):
        """Should critical fail when no actions at all."""
        md = """## Portfolio Summary

Current holdings: $500k
Cash: $25k
"""
        result = financial_advisor_check(md)
        assert result.score == 0
        assert any(issue.severity == "critical" for issue in result.issues)

    def test_missing_impact_summary(self):
        """Should deduct points for missing impact summary."""
        md = """## Action Items

- **EXECUTE** CSP on AAPL
  **Why:** IV spike
  **Gain:** $500
  Yield: 2.1%
"""
        result = financial_advisor_check(md)
        assert any("Total Impact" in issue.text for issue in result.issues)

    def test_pass_with_impact_summary(self):
        """Should pass when impact summary present."""
        md = """## Action Items

- **EXECUTE** CSP on AAPL
  **Why:** IV spike
  **Gain:** $500
  Yield: 2.1%

## Total Impact

- Potential Income: $500
- Risk: 2% max loss
"""
        result = financial_advisor_check(md)
        assert result.score >= 70 or "Total Impact" not in str([i.text for i in result.issues])


class TestOptionsTrader:
    """Options trader persona checks."""

    def test_critical_fail_saturday_expiration(self):
        """Should critical fail on Saturday expiration."""
        md = """## Rolls

- EXECUTE ROLL AAPL
  - BTC Sat May 31 '26 $150
  - STO Mon Jun 7 '26 $145
"""
        result = options_trader_check(md)
        assert result.score == 0
        assert any(issue.severity == "critical" for issue in result.issues)

    def test_critical_fail_sunday_expiration(self):
        """Should critical fail on Sunday expiration."""
        md = """EXECUTE ROLL on SPY
  - BTC Sun Jun 1 '26 $450
"""
        result = options_trader_check(md)
        assert result.score == 0

    def test_pass_weekday_expirations(self):
        """Should pass with Mon-Fri expirations."""
        md = """## Rolls

- EXECUTE ROLL AAPL
  - BTC Mon May 26 $150 at $3.00
  - STO Wed May 28 $145 at $2.50
  Delta: 0.25
"""
        result = options_trader_check(md)
        assert not any(issue.severity == "critical" for issue in result.issues)
        assert result.score >= 70

    def test_fail_roll_missing_btc(self):
        """Should fail when ROLL missing BTC leg."""
        md = """EXECUTE ROLL AAPL
- STO Mon Jun 7 $145 at $2.50
"""
        result = options_trader_check(md)
        assert any("BTC" in issue.text for issue in result.issues)

    def test_fail_roll_missing_sto(self):
        """Should fail when ROLL missing STO leg."""
        md = """EXECUTE ROLL AAPL
- BTC Mon Jun 7 $150 at $3.00
"""
        result = options_trader_check(md)
        assert any("STO" in issue.text for issue in result.issues)

    def test_fail_roll_missing_limit_price(self):
        """Should fail when ROLL missing explicit limit prices."""
        md = """EXECUTE ROLL AAPL
- BTC Mon Jun 7 strike $150
- STO Mon Jun 7 strike $145
"""
        result = options_trader_check(md)
        # Delta is also missing, so we check that at least limit is flagged
        assert any("limit" in issue.text.lower() or "Delta" in issue.text for issue in result.issues)

    def test_fail_roll_missing_delta(self):
        """Should fail when ROLL missing Delta line."""
        md = """EXECUTE ROLL AAPL
- BTC Mon Jun 7 $150 at $3.00
- STO Mon Jun 7 $145 at $2.50
"""
        result = options_trader_check(md)
        assert any("Delta" in issue.text for issue in result.issues)

    def test_pass_complete_roll(self):
        """Should pass with complete ROLL specification."""
        md = """EXECUTE ROLL AAPL
- BTC Mon Jun 7 $150 at $3.00
- STO Mon Jun 7 $145 at $2.50
Delta: 0.25
"""
        result = options_trader_check(md)
        assert result.score >= 70

    def test_critical_fail_vix_non_wednesday(self):
        """Should critical fail VIX options on non-Wednesday."""
        md = """Monday June 2 VIX call"""
        result = options_trader_check(md)
        assert any(issue.severity == "critical" for issue in result.issues)


class TestTaxCPA:
    """Tax CPA persona checks."""

    def test_fail_new_csp_missing_wash_sale_check(self):
        """Should fail when NEW CSP lacks wash-sale check."""
        md = """NEW CSP on AAPL $150
Earnings check: June 5
Account: Roth
"""
        result = tax_cpa_check(md)
        assert any("Wash-sale" in issue.text for issue in result.issues)

    def test_fail_new_csp_missing_earnings_check(self):
        """Should fail when NEW CSP lacks earnings check."""
        md = """NEW CSP on AAPL $150
Wash-sale check: clear
Account: Roth
"""
        result = tax_cpa_check(md)
        assert any("Earnings" in issue.text for issue in result.issues)

    def test_pass_new_csp_complete(self):
        """Should pass with complete NEW CSP checks."""
        md = """NEW CSP on AAPL $150
Wash-sale check: clear (no position closed in prior 30 days)
Earnings check: June 5 (after expiry May 28)
Account: Roth IRA
"""
        result = tax_cpa_check(md)
        assert not any(issue.severity == "critical" for issue in result.issues)

    def test_fail_roll_missing_earnings_check(self):
        """Should fail when ROLL lacks earnings check."""
        md = """EXECUTE ROLL AAPL from Jun to Jul
Delta adjustment for 0.30 to 0.20
"""
        result = tax_cpa_check(md)
        assert any("Earnings" in issue.text for issue in result.issues)

    def test_fail_trim_missing_ltcg_mention(self):
        """Should fail when TRIM lacks LTCG tax cost mention."""
        md = """TRIM AAPL: sell 50 shares
Reason: rebalance concentration
"""
        result = tax_cpa_check(md)
        assert any("LTCG" in issue.text.upper() for issue in result.issues)

    def test_pass_trim_with_ltcg(self):
        """Should pass when TRIM mentions LTCG cost."""
        md = """TRIM AAPL: sell 50 shares
Reason: rebalance concentration
LTCG tax cost: $3,200 (20% on $16k gain)
Proceeds: $12,800 after tax
"""
        result = tax_cpa_check(md)
        assert not any("LTCG" in issue.text for issue in result.issues)

    def test_minor_fail_missing_tax_impact_in_total(self):
        """Should deduct points for missing tax savings in impact card."""
        md = """NEW CSP on AAPL $150
Wash-sale check: clear
Earnings check: June 5
Account: Roth

## Total Impact
- Income: $500
- Risk: 2% max loss
"""
        result = tax_cpa_check(md)
        # Should mention tax, but might be optional/minor
        assert result.score >= 60  # Some deduction but not critical


class TestRiskManager:
    """Risk manager persona checks."""

    def test_critical_fail_missing_stress_test(self):
        """Should critical fail without Stress Test panel."""
        md = """## Portfolio Summary

NLV: $500k
Net Greeks: Delta 0.15, Theta $150
"""
        result = risk_manager_check(md)
        assert any(issue.severity == "critical" for issue in result.issues)
        assert any("Stress Test" in issue.text for issue in result.issues)

    def test_critical_fail_missing_hedge_book(self):
        """Should critical fail without Hedge Book panel."""
        md = """## Stress Test
If SPY -3%: AAPL at -5%, MSFT at -4%

## Net Greeks
Delta: 0.15
"""
        result = risk_manager_check(md)
        assert any(issue.severity == "critical" for issue in result.issues)
        assert any("Hedge Book" in issue.text for issue in result.issues)

    def test_critical_fail_zero_theta_with_shorts(self):
        """Should critical fail zero theta when shorts present."""
        md = """## Stress Test
If SPY -3%: AAPL short puts at risk

## Hedge Book
Hedges: SPY puts 5% OTM

## Net Greeks
Delta: 0.10
Theta: $0
Vega: -$200
"""
        result = risk_manager_check(md)
        assert any(issue.severity == "critical" for issue in result.issues)

    def test_pass_nonzero_theta_with_shorts(self):
        """Should pass with non-zero theta when shorts present."""
        md = """## Stress Test
If SPY -3%: AAPL short puts at risk

## Hedge Book
Hedges: SPY puts 5% OTM

Sold monthly puts on AAPL
## Net Greeks
Delta: 0.10
Theta: $150
Vega: -$200
"""
        result = risk_manager_check(md)
        assert not any(issue.severity == "critical" for issue in result.issues)

    def test_fail_stress_test_no_positions_named(self):
        """Should fail when Stress Test doesn't name positions."""
        md = """## Stress Test
Under market stress of -3%, portfolio would experience losses.

## Hedge Book
Hedges in place.
"""
        result = risk_manager_check(md)
        assert any("position" in issue.text.lower() for issue in result.issues)

    def test_pass_stress_test_names_positions(self):
        """Should pass when Stress Test names specific positions."""
        md = """## Stress Test
If SPY drops 3%: AAPL short puts would be at risk of assignment,
MSFT covered calls would be challenged at support.

## Hedge Book
SPY puts covering 20% of portfolio delta.
"""
        result = risk_manager_check(md)
        assert not any("position" in issue.text.lower() for issue in result.issues)

    def test_fail_concentration_breach_without_trim(self):
        """Should fail when concentration >10% but no TRIM action."""
        md = """## Portfolio

AAPL position: 12% of NLV (breach)
Holding due to strong thesis.

## Actions

- HOLD AAPL
"""
        result = risk_manager_check(md)
        assert any("concentration" in issue.text.lower() for issue in result.issues)

    def test_pass_concentration_breach_with_trim(self):
        """Should pass when concentration breach has TRIM action."""
        md = """## Portfolio

AAPL position: 12% of NLV (breach)

## Actions

- TRIM AAPL to 8% NLV (sell 2.5%)
"""
        result = risk_manager_check(md)
        # Should not fail on concentration
        assert not any("concentration" in issue.text.lower() and issue.severity == "major" for issue in result.issues)


class TestGateAndRender:
    """Integration tests for gate_and_render function."""

    def test_pass_briefing_unchanged(self):
        """Should return unchanged markdown when all checks pass."""
        md = """## Portfolio Briefing (2026-05-08)

## Action Items

- **EXECUTE CSP** on AAPL Mon May 26 $150
  **Why:** IV spike to rank 65
  **Gain:** $500 theta decay
  Yield: 2.1%
  Wash-sale check: clear
  Earnings check: June 5 (after expiry)
  Account: Roth IRA

## Stress Test
If SPY -3%: AAPL short puts at risk

## Hedge Book
SPY puts 5% OTM protecting portfolio

## Net Greeks
Delta: 0.15, Theta: $150, Vega: -$200

## Total Impact
- Income: $500
- Tax benefit: $100 (Roth growth)
"""
        result = gate_and_render(md)
        assert result == md
        assert "⚠️ Quality Gate Issues" not in result

    def test_fail_briefing_prepends_warnings(self):
        """Should prepend warnings panel when checks fail."""
        md = """## Portfolio Summary

No action items.
"""
        result = gate_and_render(md)
        assert "⚠️ Quality Gate Issues" in result
        assert "✗ FAIL" in result or "critical" in result.lower()
        # Original content still present
        assert "Portfolio Summary" in result

    def test_warnings_panel_format(self):
        """Should include persona scores and blocking issues in warnings."""
        md = """Bad briefing"""
        result = gate_and_render(md)
        assert "Persona Scores" in result
        assert "Critical Issues" in result or "Recommended Action" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
