"""Task #20 — CSP Rotations in the webapp's Rotations tab.

The briefing JSON's ``csp_rotations`` list (close lower-yield held CSP →
open higher-yield candidate) must render as a "CSP rotations" subsection
inside the Rotations tab, ABOVE the equity swaps. The tab must appear even
when csp_rotations is the ONLY rotation flavor present.
"""

from __future__ import annotations

DATE = "2026-06-30"


def _csp_rotation_row():
    """A CSPRotation.to_dict() row exactly as the pipeline emits it."""
    return {
        "close_positions": [
            {
                "ticker": "GOOG", "strike": 325.0, "expiration": "2026-08-21",
                "qty": 1, "entry_premium": 7.95, "current_mid": 4.45,
                "days_remaining": 45, "symbol": "GOOG_PUT_325_20260821",
                "collateral": 32500.0, "capture_pct": 0.4403,
                "remaining_theta_dollars": 445.0, "remaining_yield_ann": 11.11,
                "banked_profit_dollars": 350.0,
            }
        ],
        "open_position": {
            "ticker": "MU", "strike": 830.0, "expiration": "2026-09-18",
            "dte": 73, "premium_per_share": 104.45, "premium_dollars": 10445.0,
            "collateral": 83000.0, "yield_ann": 62.92, "rsi": 43.0,
            "source_kind": "LONG_DATED_CSP", "capacity_deferred": True,
        },
        "freed_collateral": 102500.0,
        "required_collateral": 83000.0,
        "net_collateral_delta": 19500.0,
        "close_yield_ann": 16.6,
        "open_yield_ann": 62.92,
        "yield_delta_pct": 46.32,
        "score": 38445.6,
        "freed_bucket_pct": 3.0,
        "warnings": ["closing realizes ~$2,692 of short-term gains (taxable)"],
    }


def test_csp_rotations_subsection_renders_in_rotations_tab(client, monkeypatch):
    """Briefing JSON with csp_rotations → the Rotations tab shows a
    'CSP rotations' subsection with the real close/open legs. The fixture
    briefing has NO equity rotation_opportunities, so this also pins that
    csp_rotations ALONE makes the tab appear (_rot_total counts it)."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _with_csp_rotations(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["csp_rotations"] = [_csp_rotation_row()]
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _with_csp_rotations)
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="rotations"' in body
    assert "CSP rotations" in body
    # Both legs with their real strikes.
    assert "GOOG $325P" in body
    assert "MU $830P" in body
    # Real measured numbers, not boilerplate (hard rule #19).
    assert "102,500" in body and "83,000" in body
    # The warning surfaces.
    assert "short-term gains" in body


def test_briefing_without_csp_rotations_unchanged(client):
    """Old briefings (no csp_rotations key) still render without the
    subsection and without a 500 — the field defaults to []."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    assert "CSP rotations" not in r.text


def _near_miss_row():
    """A NearMissRotation.to_dict() row exactly as the pipeline emits it
    (task #21 — blocked-by-one-gate near-miss surface)."""
    return {
        "close_positions": [
            {
                "ticker": "MSFT", "strike": 350.0, "expiration": "2026-09-18",
                "qty": 1, "entry_premium": 23.43, "current_mid": 9.53,
                "days_remaining": 73, "symbol": "MSFT_PUT_350_20260918",
                "collateral": 35000.0, "capture_pct": 0.5932,
                "remaining_theta_dollars": 953.0, "remaining_yield_ann": 13.6,
                "banked_profit_dollars": 1390.0,
            }
        ],
        "open_position": {
            "ticker": "MU", "strike": 830.0, "expiration": "2026-09-18",
            "dte": 73, "premium_per_share": 104.45, "premium_dollars": 10445.0,
            "collateral": 83000.0, "yield_ann": 62.92, "rsi": 43.0,
            "source_kind": "LONG_DATED_CSP", "capacity_deferred": True,
        },
        "freed_collateral": 79500.0,
        "required_collateral": 83000.0,
        "yield_delta_pct": 49.3,
        "score": 40919.0,
        "block_reason": "directive_hold",
        "block_detail": (
            'Position MSFT_PUT_350_20260918 is held per your directive in '
            'fable_advisor_memory.md ("**MSFT_PUT_350_20260918 — same '
            'treatment as AMD.**").'
        ),
        "unblock_path": (
            "Revisit or amend the MSFT hold directive to unlock this rotation."
        ),
        "warnings": ["already short MU put(s) at $1000 — this stacks single-name risk"],
    }


def test_near_miss_rotations_render_in_rotations_tab(client, monkeypatch):
    """Task #21: a briefing with ONLY near-miss rotations (no qualified,
    no equity swaps) still shows the Rotations tab with the 🔍 near-miss
    subsection: blocked-by label, detail, and unblock path all present.
    User symptom: 'no rotations available... I still want to know what
    better rotations I could have had. It's useless.'"""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _with_near_misses(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["csp_rotation_near_misses"] = [_near_miss_row()]
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _with_near_misses)
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="rotations"' in body        # tab appears on near-miss alone
    assert "Near-miss rotations" in body
    assert "Directive hold" in body           # humanized block_reason chip
    assert "fable_advisor_memory.md" in body  # block detail
    assert "Revisit or amend the MSFT hold directive" in body
    assert "MSFT $350P" in body and "MU $830P" in body
    assert "79,500" in body and "83,000" in body
    # Machine identifier never leaks raw (hard rule #31).
    assert "directive_hold</sl-badge>" not in body
