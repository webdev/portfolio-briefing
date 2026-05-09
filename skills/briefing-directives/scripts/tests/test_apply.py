"""Tests for apply_to_recommendations module."""

import pytest

from apply_to_recommendations import apply_directives
from directive_store import create


class TestApplyDirectives:
    """Test apply_directives function."""

    def test_no_directives(self, sample_recommendations):
        """With no directives, candidates pass through unchanged."""
        result = apply_directives(sample_recommendations, [])
        assert len(result) == len(sample_recommendations)
        assert all(c["recommendation"] == "EXECUTE" for c in result)

    def test_suppress_drops_candidate(self, state_dir, sample_recommendations, sample_directive_suppress):
        """SUPPRESS directive drops candidate entirely."""
        directives = [create(state_dir, sample_directive_suppress)]

        result = apply_directives(sample_recommendations, directives)

        # Should have one fewer (BABA dropped)
        assert len(result) == len(sample_recommendations) - 1
        assert not any(c["ticker"] == "BABA" for c in result)

    def test_defer_changes_recommendation(self, state_dir, sample_recommendations, sample_directive_defer):
        """DEFER directive changes recommendation to DEFERRED."""
        directives = [create(state_dir, sample_directive_defer)]

        result = apply_directives(sample_recommendations, directives)

        # All candidates still present
        assert len(result) == len(sample_recommendations)

        # Find AAPL candidate
        aapl = next(c for c in result if c["ticker"] == "AAPL")
        assert aapl["recommendation"] == "DEFERRED"
        assert "deferred_reason" in aapl
        assert aapl["directive_id"] == directives[0]["directive_id"]

    def test_manual_changes_recommendation(self, state_dir, sample_recommendations, sample_directive_manual):
        """MANUAL directive changes recommendation to MANUAL."""
        directives = [create(state_dir, sample_directive_manual)]

        result = apply_directives(sample_recommendations, directives)

        # MSFT short call candidate
        msft = next(c for c in result if c["ticker"] == "MSFT")
        assert msft["recommendation"] == "MANUAL"
        assert "manual_reason" in msft

    def test_watch_only_changes_recommendation(self, state_dir, sample_recommendations, sample_directive_watch):
        """WATCH_ONLY directive changes recommendation to WATCH_ONLY."""
        directives = [create(state_dir, sample_directive_watch)]

        result = apply_directives(sample_recommendations, directives)

        # NVDA new_idea candidate
        nvda = next(c for c in result if c["ticker"] == "NVDA")
        assert nvda["recommendation"] == "WATCH_ONLY"
        assert "watch_trigger" in nvda
        assert "watch_reason" in nvda

    def test_override_adds_params(self, state_dir, sample_recommendations, sample_directive_override):
        """OVERRIDE directive adds override_params."""
        directives = [create(state_dir, sample_directive_override)]

        result = apply_directives(sample_recommendations, directives)

        # AMD candidate
        amd = next(c for c in result if c["ticker"] == "AMD")
        assert "override_params" in amd
        assert amd["override_params"]["parameter"] == "take_profit_threshold"
        assert amd["override_params"]["new_value"] == 0.80

    def test_suppress_priority_wins(self, state_dir, sample_recommendations, sample_directive_suppress, sample_directive_defer):
        """SUPPRESS takes priority (candidate dropped entirely)."""
        suppress_baba = create(state_dir, sample_directive_suppress)

        # Also create a DEFER (shouldn't matter because SUPPRESS wins)
        defer_directive = sample_directive_defer.copy()
        defer_directive["target"]["identifier"] = "BABA  260619P00100000"
        defer_baba = create(state_dir, defer_directive)

        result = apply_directives(sample_recommendations, [suppress_baba, defer_baba])

        # BABA should be dropped, not deferred
        assert not any(c["ticker"] == "BABA" for c in result)

    def test_multiple_targets_independent(self, state_dir, sample_recommendations, sample_directive_defer, sample_directive_watch):
        """Multiple directives apply independently to different targets."""
        defer = create(state_dir, sample_directive_defer)
        watch = create(state_dir, sample_directive_watch)

        result = apply_directives(sample_recommendations, [defer, watch])

        # AAPL should be DEFERRED
        aapl = next(c for c in result if c["ticker"] == "AAPL")
        assert aapl["recommendation"] == "DEFERRED"

        # NVDA should be WATCH_ONLY
        nvda = next(c for c in result if c["ticker"] == "NVDA")
        assert nvda["recommendation"] == "WATCH_ONLY"

    def test_position_scope_matches_specific(self, state_dir, sample_directive_manual):
        """Position_scope directive matches specific option positions."""
        directives = [create(state_dir, sample_directive_manual)]

        # MSFT short call candidate
        candidates = [
            {
                "ticker": "MSFT",
                "kind": "option_position",
                "symbol": "MSFT",
                "position_type": "short_call",
                "identifier": "MSFT  260516C00400000",
                "action": "CLOSE",
                "recommendation": "EXECUTE",
            }
        ]

        result = apply_directives(candidates, directives)

        assert result[0]["recommendation"] == "MANUAL"

    def test_position_scope_no_type_matches_all(self):
        """Position_scope without position_type matches any position in symbol."""
        directive_dict = {
            "type": "MANUAL",
            "target": {
                "kind": "position_scope",
                "symbol": "MSFT",
                # no position_type
            },
            "reason": "test",
            "expires": {"trigger": "open_ended"},
            "created_via": "test",
        }

        candidates = [
            {
                "ticker": "MSFT",
                "kind": "option_position",
                "symbol": "MSFT",
                "position_type": "short_call",
                "recommendation": "EXECUTE",
            },
            {
                "ticker": "MSFT",
                "kind": "option_position",
                "symbol": "MSFT",
                "position_type": "long_call",
                "recommendation": "EXECUTE",
            },
        ]

        # Create mock directive
        directive = directive_dict.copy()
        directive["directive_id"] = "dir_test_manual"
        directive["status"] = "ACTIVE"

        result = apply_directives(candidates, [directive])

        # Both should be MANUAL
        assert all(c["recommendation"] == "MANUAL" for c in result)

    def test_symbol_level_suppression_matches_new_idea(self):
        """Symbol-level SUPPRESS matches new_idea candidates."""
        directive = {
            "directive_id": "dir_test_suppress",
            "type": "SUPPRESS",
            "target": {
                "kind": "symbol",
                "symbol": "BABA",
            },
            "reason": "test",
            "expires": {"trigger": "open_ended"},
            "status": "ACTIVE",
        }

        candidates = [
            {
                "ticker": "BABA",
                "kind": "new_idea",
                "symbol": "BABA",
                "source_screener": "vcp-screener",
                "recommendation": "EXECUTE",
            }
        ]

        result = apply_directives(candidates, [directive])

        # Should be dropped
        assert len(result) == 0

    def test_new_idea_with_screener_filter(self):
        """New_idea match respects screener filter."""
        directive = {
            "directive_id": "dir_test_watch",
            "type": "WATCH_ONLY",
            "target": {
                "kind": "new_idea",
                "symbol": "NVDA",
                "source_screener": "vcp-screener",
            },
            "reason": "test",
            "expires": {"trigger": "price_above", "symbol": "NVDA", "level": 190},
            "status": "ACTIVE",
        }

        candidates = [
            {
                "ticker": "NVDA",
                "kind": "new_idea",
                "symbol": "NVDA",
                "source_screener": "vcp-screener",
                "recommendation": "EXECUTE",
            },
            {
                "ticker": "NVDA",
                "kind": "new_idea",
                "symbol": "NVDA",
                "source_screener": "earnings-trade-analyzer",  # Different screener
                "recommendation": "EXECUTE",
            },
        ]

        result = apply_directives(candidates, [directive])

        # First (vcp) should be WATCH_ONLY, second should be unchanged
        assert result[0]["recommendation"] == "WATCH_ONLY"
        assert result[1]["recommendation"] == "EXECUTE"


class TestEmptyCandidates:
    """Test edge cases with empty candidate lists."""

    def test_apply_empty_candidates(self, sample_directive_defer):
        """apply_directives handles empty candidate list."""
        directives = [sample_directive_defer.copy()]
        directives[0]["directive_id"] = "dir_test"
        directives[0]["status"] = "ACTIVE"

        result = apply_directives([], directives)

        assert result == []

    def test_apply_no_matching_directives(self):
        """No directives match any candidate."""
        directive = {
            "directive_id": "dir_test",
            "type": "DEFER",
            "target": {
                "kind": "option_position",
                "identifier": "UNKNOWN  260619P00170000",
            },
            "reason": "test",
            "expires": {"trigger": "open_ended"},
            "status": "ACTIVE",
        }

        candidates = [
            {
                "ticker": "AAPL",
                "kind": "option_position",
                "identifier": "AAPL  260619P00170000",
                "recommendation": "EXECUTE",
            }
        ]

        result = apply_directives(candidates, [directive])

        # Candidate unchanged
        assert result[0]["recommendation"] == "EXECUTE"
