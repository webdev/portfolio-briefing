"""Diff module tests + /diff route tests."""

from __future__ import annotations

from app import diff


# ─── Pure-function tests for the differ ──────────────────────────────


def _make_briefing(date, **kwargs):
    base = {"date": date, "nlv": 1000000, "cash": 50000, "regime": "RISK_ON",
            "equity_reviews": [], "options_reviews": [], "actions": []}
    base.update(kwargs)
    return base


def test_diff_scalar_deltas():
    a = _make_briefing("2026-01-01", nlv=900000, cash=100000, regime="RISK_OFF")
    b = _make_briefing("2026-01-02", nlv=950000, cash=90000, regime="RISK_ON")
    out = diff.diff_briefings(a, b)
    s = out["scalars"]
    assert s["nlv"]["delta"] == 50000
    assert s["nlv"]["pct"] == 50000 / 900000
    assert s["cash"]["delta"] == -10000
    assert s["regime"]["changed"] is True
    assert s["regime"]["a"] == "RISK_OFF"
    assert s["regime"]["b"] == "RISK_ON"


def test_diff_scalar_handles_missing_nlv():
    a = _make_briefing("2026-01-01", nlv=None)
    b = _make_briefing("2026-01-02", nlv=950000)
    out = diff.diff_briefings(a, b)
    assert out["scalars"]["nlv"]["a"] is None
    assert out["scalars"]["nlv"]["delta"] is None  # None - 950000 stays None


def test_diff_positions_added_removed_changed():
    pa = [
        {"symbol": "NVDA", "assetType": "EQUITY", "qty": 500, "price": 180},
        {"symbol": "AMZN", "assetType": "EQUITY", "qty": 100, "price": 220},
        {"symbol": "DELETE_ME", "assetType": "EQUITY", "qty": 10, "price": 5},
    ]
    pb = [
        {"symbol": "NVDA", "assetType": "EQUITY", "qty": 500, "price": 200},  # unchanged qty
        {"symbol": "AMZN", "assetType": "EQUITY", "qty": 150, "price": 240},  # qty change
        {"symbol": "NEW_TICKER", "assetType": "EQUITY", "qty": 25, "price": 50},
    ]
    out = diff.diff_briefings(
        _make_briefing("a"), _make_briefing("b"),
        positions_a=pa, positions_b=pb,
    )
    added = [p["symbol"] for p in out["positions"]["added"]]
    removed = [p["symbol"] for p in out["positions"]["removed"]]
    changed = [p["symbol"] for p in out["positions"]["changed"]]
    assert added == ["NEW_TICKER"]
    assert removed == ["DELETE_ME"]
    assert changed == ["AMZN"]
    amz = next(p for p in out["positions"]["changed"] if p["symbol"] == "AMZN")
    assert amz["qty_a"] == 100
    assert amz["qty_b"] == 150
    assert amz["qty_delta"] == 50


def test_diff_actions_new_completed_pending():
    a = _make_briefing("a", actions=[
        {"key": "CLOSE:X", "kind": "CLOSE", "ident": "X", "summary": "close X"},
        {"key": "HEDGE:Y", "kind": "HEDGE", "ident": "Y", "summary": "hedge Y"},
    ])
    b = _make_briefing("b", actions=[
        {"key": "CLOSE:X", "kind": "CLOSE", "ident": "X", "summary": "close X"},
        {"key": "ROLL:Z", "kind": "ROLL", "ident": "Z", "summary": "roll Z"},
    ])
    out = diff.diff_briefings(a, b)
    new_keys = [r["key"] for r in out["actions"]["new"]]
    completed = [r["key"] for r in out["actions"]["completed"]]
    pending = [r["key"] for r in out["actions"]["still_pending"]]
    assert new_keys == ["ROLL:Z"]
    assert completed == ["HEDGE:Y"]
    assert pending == ["CLOSE:X"]


def test_diff_parkev_tier_change():
    recs_a = {"META": {"rating_tier": 3, "raw_recommendation": "Buy", "conviction": "Medium"}}
    recs_b = {"META": {"rating_tier": 4, "raw_recommendation": "Top 12 Stock", "conviction": "High"}}
    out = diff.diff_briefings(
        _make_briefing("a"), _make_briefing("b"),
        recs_a=recs_a, recs_b=recs_b,
    )
    chg = out["parkev"]["changed"]
    assert len(chg) == 1
    assert chg[0]["ticker"] == "META"
    assert chg[0]["prior"]["tier"] == 3
    assert chg[0]["current"]["tier"] == 4
    assert chg[0]["current"]["conviction"] == "High"


def test_diff_parkev_conviction_only_change():
    recs_a = {"NVDA": {"rating_tier": 4, "conviction": "Medium"}}
    recs_b = {"NVDA": {"rating_tier": 4, "conviction": "High"}}
    out = diff.diff_briefings(
        _make_briefing("a"), _make_briefing("b"),
        recs_a=recs_a, recs_b=recs_b,
    )
    assert len(out["parkev"]["changed"]) == 1


def test_diff_parkev_unchanged_yields_no_change():
    recs = {"NVDA": {"rating_tier": 4, "conviction": "High"}}
    out = diff.diff_briefings(
        _make_briefing("a"), _make_briefing("b"),
        recs_a=recs, recs_b=recs,
    )
    assert out["parkev"]["changed"] == []


def test_diff_parkev_new_and_dropped_coverage():
    recs_a = {"NVDA": {"rating_tier": 4, "conviction": "High"}}
    recs_b = {
        "NVDA": {"rating_tier": 4, "conviction": "High"},
        "MSFT": {"rating_tier": 3, "conviction": "Medium"},
    }
    out = diff.diff_briefings(
        _make_briefing("a"), _make_briefing("b"),
        recs_a=recs_a, recs_b=recs_b,
    )
    assert [c["ticker"] for c in out["parkev"]["new_coverage"]] == ["MSFT"]
    assert out["parkev"]["dropped_coverage"] == []


# ─── Route tests via TestClient ──────────────────────────────────────


def test_diff_route_renders(client):
    r = client.get("/diff/2026-05-10/2026-06-30")
    assert r.status_code == 200
    html = r.text
    assert "Diff" in html
    # NLV went 950k → 1.0M
    assert "1,000,000" in html or "950,000" in html
    # NVDA appears in both — should not be in added/removed but in actions
    assert "Action queue diff" in html


def test_diff_route_404_on_missing_date(client):
    r = client.get("/diff/2099-01-01/2026-06-30")
    assert r.status_code == 404


def test_diff_yesterday_latest_redirects(client):
    r = client.get("/diff/yesterday/latest", follow_redirects=False)
    assert r.status_code in (302, 307)
    # Should redirect to the two-most-recent fixture dates
    assert "/diff/2026-05-10/2026-06-30" in r.headers["location"]


def test_diff_fragment_renders(client):
    r = client.get("/fragment/diff/2026-05-10/2026-06-30")
    assert r.status_code == 200
    # Fragment shouldn't include the page chrome (no <header>)
    assert "<header" not in r.text
    assert "Action queue diff" in r.text
