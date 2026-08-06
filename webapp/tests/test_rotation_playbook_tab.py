"""Task #22 — Actionable Rotation Playbook in the webapp's Rotations tab.

The briefing JSON's ``rotation_playbook`` dict (close all freeable winner
CSPs → conviction-ranked redeploy) must render as a "🎯 Actionable Rotation
Playbook" subsection at the TOP of the Rotations tab, above the task-#20
CSP rotations. The tab must appear even when the playbook is the ONLY
rotation flavor present.
"""

from __future__ import annotations

DATE = "2026-06-30"


def _playbook():
    """A RotationPlaybook.to_dict() exactly as the pipeline emits it."""
    return {
        "closes": [
            {
                "ticker": "GOOG", "contract": "GOOG_PUT_325_20260821",
                "strike": 325.0, "expiration": "2026-08-21", "qty": 1,
                "capture_pct": 0.4399, "remaining_extrinsic": 445.0,
                "freed_collateral": 32500.0, "realized_profit": 349.47,
                "buy_to_close_mid": 4.45, "dte_remaining": 45,
                "directive_held": False,
            },
            {
                "ticker": "PATH", "contract": "PATH_PUT_9_20261120",
                "strike": 9.0, "expiration": "2026-11-20", "qty": 10,
                "capture_pct": 0.4119, "remaining_extrinsic": 685.0,
                "freed_collateral": 9000.0, "realized_profit": 479.7,
                "buy_to_close_mid": 0.685, "dte_remaining": 136,
                "directive_held": False,
            },
        ],
        "opens": [
            {
                "ticker": "CRM", "strike": 150.0, "expiration": "2026-08-14",
                "dte": 38, "collateral_required": 15000.0, "premium": 240.0,
                "annualized_yield_pct": 15.37, "mid_price": 2.40,
                "parkev_rating": "BUY", "parkev_tier": 3,
                "parkev_conviction": "High", "parkev_age_days": 5,
                "conviction_score": 20.0,
                "setup_flags": ["pullback zone"], "warnings": [],
                "lt_verdict": None, "has_earnings_in_window": False,
                "stacks_with_held": False, "rsi": 49.0,
            },
        ],
        "total_freed": 41500.0,
        "total_collateral_deployed": 15000.0,
        "cash_cushion_kept": 26500.0,
        "total_premium_collected": 240.0,
        "total_realized_profit": 829.17,
        "weighted_yield_pct": 15.37,
        "bucket_concentrations": {"2026-08-14": 15000.0},
        "coverage_before": 0.17,
        "coverage_after": 0.18,
        "warnings": ["2 directive-held winner(s) excluded from the sweep: "
                     "AMD_PUT_420_20261218, MSFT_PUT_350_20260918 "
                     "(fable_advisor_memory.md)"],
    }


def test_playbook_renders_in_rotations_tab(client, monkeypatch):
    """Briefing JSON with rotation_playbook → the Rotations tab shows the
    playbook subsection with both phases. The fixture briefing has NO other
    rotation flavors, so this also pins that the playbook ALONE makes the
    tab appear (_rot_total counts it)."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _with_playbook(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["rotation_playbook"] = _playbook()
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _with_playbook)
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="rotations"' in body
    assert "Actionable Rotation Playbook" in body
    # Phase 1 legs with measured numbers (hard rule #19).
    assert "GOOG $325P" in body and "PATH $9P" in body
    assert "32,500" in body and "$4.45 GTC" in body
    # Phase 2 leg + conviction badge from the real score.
    assert "CRM $150P" in body
    assert "⭐⭐⭐" in body
    assert "Parkev BUY High" in body
    assert "pullback zone" in body
    # Totals + coverage estimate.
    assert "41,500" in body and "26,500" in body
    assert "0.17" in body and "0.18" in body
    # Warnings surface.
    assert "directive-held winner(s) excluded" in body


def test_playbook_closes_only_mode_renders(client, monkeypatch):
    """A closes-only playbook (no qualified opens) still renders — 'close
    for coverage' note instead of Phase 2 tickets (hard rule #24)."""
    from app import ingest

    real_load = ingest.load_briefing_json
    pb = _playbook()
    pb["opens"] = []
    pb["total_collateral_deployed"] = 0.0
    pb["cash_cushion_kept"] = pb["total_freed"]
    pb["total_premium_collected"] = 0.0
    pb["bucket_concentrations"] = {}
    pb["warnings"] = ["close for coverage, no qualified re-deployment today "
                      "— every candidate failed a discipline gate or the "
                      "conviction floor"]

    def _with_playbook(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["rotation_playbook"] = pb
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _with_playbook)
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "Actionable Rotation Playbook" in body
    assert "no qualified re-deployment today" in body
    assert "Sell-to-Open" not in body


def test_briefing_without_playbook_unchanged(client):
    """Old briefings (no rotation_playbook key) render without the
    subsection and without a 500 — the field defaults to {}."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    assert "Actionable Rotation Playbook" not in r.text
