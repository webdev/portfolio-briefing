"""Tests for directive_store module."""

import json
from pathlib import Path

import pytest
import yaml

from directive_store import (
    create,
    get,
    list as list_directives,
    transition,
    find_matching,
)


class TestCreate:
    """Test directive creation."""

    def test_create_defer(self, state_dir, sample_directive_defer):
        """Create a DEFER directive."""
        directive = create(state_dir, sample_directive_defer)
        assert directive["directive_id"].startswith("dir_")
        assert directive["status"] == "ACTIVE"
        assert directive["type"] == "DEFER"
        assert "created_at" in directive

    def test_create_manual(self, state_dir, sample_directive_manual):
        """Create a MANUAL directive."""
        directive = create(state_dir, sample_directive_manual)
        assert directive["type"] == "MANUAL"

    def test_create_override(self, state_dir, sample_directive_override):
        """Create an OVERRIDE directive."""
        directive = create(state_dir, sample_directive_override)
        assert directive["type"] == "OVERRIDE"
        assert directive["parameter"] == "take_profit_threshold"
        assert directive["new_value"] == 0.80

    def test_create_watch(self, state_dir, sample_directive_watch):
        """Create a WATCH_ONLY directive."""
        directive = create(state_dir, sample_directive_watch)
        assert directive["type"] == "WATCH_ONLY"

    def test_create_suppress(self, state_dir, sample_directive_suppress):
        """Create a SUPPRESS directive."""
        directive = create(state_dir, sample_directive_suppress)
        assert directive["type"] == "SUPPRESS"

    def test_create_saves_file(self, state_dir, sample_directive_defer):
        """Verify file is saved."""
        directive = create(state_dir, sample_directive_defer)
        directive_path = Path(state_dir) / "active" / f"{directive['directive_id']}.yaml"
        assert directive_path.exists()

        # Verify file content
        with open(directive_path) as f:
            saved = yaml.safe_load(f)
        assert saved["directive_id"] == directive["directive_id"]

    def test_create_updates_index(self, state_dir, sample_directive_defer):
        """Verify index is updated."""
        directive = create(state_dir, sample_directive_defer)
        index_path = Path(state_dir) / "index.yaml"
        assert index_path.exists()

        with open(index_path) as f:
            index = yaml.safe_load(f)
        assert directive["directive_id"] in index

    def test_create_invalid_type(self, state_dir):
        """Reject invalid directive type."""
        invalid = {
            "type": "INVALID",
            "target": {"kind": "symbol", "symbol": "AAPL"},
            "reason": "test",
            "expires": {"trigger": "open_ended"},
        }
        with pytest.raises(ValueError, match="Invalid type"):
            create(state_dir, invalid)

    def test_create_missing_fields(self, state_dir):
        """Reject directive missing required fields."""
        incomplete = {
            "type": "DEFER",
            "target": {"kind": "symbol", "symbol": "AAPL"},
            # missing reason and expires
        }
        with pytest.raises(ValueError, match="missing required fields"):
            create(state_dir, incomplete)

    def test_create_override_without_params(self, state_dir):
        """OVERRIDE must have parameter and new_value."""
        invalid_override = {
            "type": "OVERRIDE",
            "target": {"kind": "option_position", "identifier": "AAPL  260619P00170000"},
            "reason": "test",
            "expires": {"trigger": "open_ended"},
            # missing parameter and new_value
        }
        with pytest.raises(ValueError, match="OVERRIDE.*parameter.*new_value"):
            create(state_dir, invalid_override)


class TestTransition:
    """Test status transitions."""

    def test_transition_active_to_expired(self, state_dir, sample_directive_defer):
        """Transition ACTIVE → EXPIRED."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        updated = transition(state_dir, directive_id, "EXPIRED", "earnings passed")
        assert updated["status"] == "EXPIRED"
        assert len(updated["status_history"]) == 2

    def test_transition_active_to_overridden(self, state_dir, sample_directive_defer):
        """Transition ACTIVE → OVERRIDDEN."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        updated = transition(state_dir, directive_id, "OVERRIDDEN", "user changed mind")
        assert updated["status"] == "OVERRIDDEN"

    def test_transition_active_to_resolved(self, state_dir, sample_directive_defer):
        """Transition ACTIVE → RESOLVED."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        updated = transition(state_dir, directive_id, "RESOLVED", "position closed")
        assert updated["status"] == "RESOLVED"

    def test_transition_moves_file(self, state_dir, sample_directive_defer):
        """Verify file moves between subdirs."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        old_path = Path(state_dir) / "active" / f"{directive_id}.yaml"
        assert old_path.exists()

        transition(state_dir, directive_id, "EXPIRED", "test")

        assert not old_path.exists()
        new_path = Path(state_dir) / "expired" / f"{directive_id}.yaml"
        assert new_path.exists()

    def test_transition_invalid_status(self, state_dir, sample_directive_defer):
        """Reject invalid status."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        with pytest.raises(ValueError, match="Invalid status"):
            transition(state_dir, directive_id, "INVALID", "test")

    def test_transition_terminal_is_terminal(self, state_dir, sample_directive_defer):
        """Terminal status → non-terminal is invalid."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        transition(state_dir, directive_id, "EXPIRED", "test")

        with pytest.raises(ValueError, match="Terminal statuses are final"):
            transition(state_dir, directive_id, "ACTIVE", "test")

    def test_transition_nonexistent(self, state_dir):
        """Reject transition of nonexistent directive."""
        with pytest.raises(ValueError, match="Directive not found"):
            transition(state_dir, "dir_nonexistent", "EXPIRED", "test")


class TestList:
    """Test directive listing."""

    def test_list_empty(self, state_dir):
        """List on empty state returns empty list."""
        directives = list_directives(state_dir)
        assert directives == []

    def test_list_all(self, state_dir, sample_directive_defer, sample_directive_manual):
        """List all directives."""
        create(state_dir, sample_directive_defer)
        create(state_dir, sample_directive_manual)

        directives = list_directives(state_dir)
        assert len(directives) == 2

    def test_list_filter_by_status(self, state_dir, sample_directive_defer):
        """List filter by status."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        # Initially ACTIVE
        active = list_directives(state_dir, status="ACTIVE")
        assert len(active) == 1

        # After transition
        transition(state_dir, directive_id, "EXPIRED", "test")
        active = list_directives(state_dir, status="ACTIVE")
        assert len(active) == 0

        expired = list_directives(state_dir, status="EXPIRED")
        assert len(expired) == 1


class TestGet:
    """Test fetching a single directive."""

    def test_get_exists(self, state_dir, sample_directive_defer):
        """Fetch an existing directive."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        fetched = get(state_dir, directive_id)
        assert fetched is not None
        assert fetched["directive_id"] == directive_id

    def test_get_nonexistent(self, state_dir):
        """Fetch nonexistent directive returns None."""
        fetched = get(state_dir, "dir_nonexistent")
        assert fetched is None

    def test_get_after_transition(self, state_dir, sample_directive_defer):
        """Fetch directive after transition."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        transition(state_dir, directive_id, "EXPIRED", "test")

        fetched = get(state_dir, directive_id)
        assert fetched["status"] == "EXPIRED"


class TestFindMatching:
    """Test finding directives that match a target."""

    def test_find_exact_option_position(self, state_dir, sample_directive_defer):
        """Find directive matching exact option position."""
        directive = create(state_dir, sample_directive_defer)

        target = {
            "kind": "option_position",
            "identifier": "AAPL  260619P00170000",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 1
        assert matches[0]["directive_id"] == directive["directive_id"]

    def test_find_broader_position_scope(self, state_dir, sample_directive_manual):
        """Find directive with broader position_scope."""
        directive = create(state_dir, sample_directive_manual)

        # Directive matches all MSFT short calls
        target = {
            "kind": "option_position",
            "symbol": "MSFT",
            "position_type": "short_call",
            "identifier": "MSFT  260516C00400000",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 1

    def test_find_symbol_level(self, state_dir, sample_directive_suppress):
        """Find symbol-level SUPPRESS directive."""
        directive = create(state_dir, sample_directive_suppress)

        # SUPPRESS on BABA matches any BABA target
        target = {
            "kind": "new_idea",
            "symbol": "BABA",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 1

    def test_find_no_match(self, state_dir, sample_directive_defer):
        """Find with no matching directive."""
        create(state_dir, sample_directive_defer)

        target = {
            "kind": "option_position",
            "identifier": "MSFT  260516C00400000",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 0

    def test_find_inactive_not_matched(self, state_dir, sample_directive_defer):
        """Expired directives not matched."""
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]

        transition(state_dir, directive_id, "EXPIRED", "test")

        target = {
            "kind": "option_position",
            "identifier": "AAPL  260619P00170000",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 0

    def test_find_multiple_matches(self, state_dir, sample_directive_manual):
        """Find multiple matching directives."""
        # MANUAL on MSFT short calls
        manual = create(state_dir, sample_directive_manual)

        # Another DEFER on same symbol
        defer_msft = {
            "type": "DEFER",
            "target": {
                "kind": "position_scope",
                "symbol": "MSFT",
            },
            "reason": "Deferring all MSFT positions",
            "expires": {
                "trigger": "time_elapsed",
                "until_date": "2026-05-20",
            },
            "created_via": "test",
        }
        defer = create(state_dir, defer_msft)

        target = {
            "kind": "option_position",
            "symbol": "MSFT",
            "position_type": "short_call",
            "identifier": "MSFT  260516C00400000",
        }

        matches = find_matching(state_dir, target)
        assert len(matches) == 2  # Both MANUAL and DEFER match


class TestIntegration:
    """Integration tests: create, list, transition, find."""

    def test_lifecycle_full(self, state_dir, sample_directive_defer):
        """Full directive lifecycle."""
        # Create
        directive = create(state_dir, sample_directive_defer)
        directive_id = directive["directive_id"]
        assert directive["status"] == "ACTIVE"

        # Find before expiry
        target = directive["target"]
        matches = find_matching(state_dir, target)
        assert len(matches) == 1

        # Transition
        transition(state_dir, directive_id, "EXPIRED", "trigger fired")

        # Find after expiry (no match because not ACTIVE)
        matches = find_matching(state_dir, target)
        assert len(matches) == 0

        # List expired
        expired = list_directives(state_dir, status="EXPIRED")
        assert len(expired) == 1
