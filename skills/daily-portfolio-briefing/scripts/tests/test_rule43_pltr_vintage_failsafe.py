"""Rule #43 batch — PLTR vintage-guard fail-open hole + Tier A cap miss
(CLAUDE.md hard rules #46 + #29; observed 2026-08-04 rerun).

Observed card:

    PULLBACK CSP PLTR — sell $145P ... RSI 48 🟢 pullback ...
    Trade-validator: ✅ GOOD TRADE ...
    ⚠️ Concentration check: Assignment would push PLTR to ~12.6% NLV
    (over 10% cap)

Reality: PLTR exploded +29% TODAY (spot ~$163 from the E*TRADE positions
feed — the same number the card's "-11% below spot" used); RSI 48 was
computed through YESTERDAY's close ($125.65); real live RSI ≈ 70-75 (hard
block). The run log showed only 22/36 symbols got yfinance quotes — PLTR's
quote was missing, so the vintage guard had no drift reference and FAILED
OPEN, letting the stale 🟢 badge and GOOD-TRADE verdict render. And the
concentration text used the legacy 10% cap on a Tier A name whose cap is 22%.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pre_trade_validator as ptv  # noqa: E402
from analysis import vintage_guard as vg  # noqa: E402
from render import panels  # noqa: E402
from render.panels import render_action_list  # noqa: E402


# PLTR-shaped fixture: yesterday's close 125.65, today +29% to 163.30.
# 15 closes so wilder_rsi_live has period+1 prices; the +$37.65 final bar
# pushes the live RSI deep into the 70+ hard-block zone.
_PLTR_CLOSES = [118.0, 119.2, 120.1, 119.5, 121.0, 122.3, 121.8, 123.0,
                124.1, 123.5, 125.0, 124.2, 126.0, 125.4, 125.65]

_PLTR_TECH = {
    "PLTR": {
        "spot": 125.65,          # the technicals' reference close
        "rsi_14": 48.0,          # the stale "RSI 48 🟢 pullback" read
        "sma_200": 110.0,
        "recent_closes": _PLTR_CLOSES,
    }
}

_PLTR_POSITIONS = [
    {"symbol": "PLTR", "assetType": "EQUITY", "qty": 500, "price": 163.30},
]


def _pltr_snap(quotes=None, config_extra=None, tech=None):
    cfg = {
        "core_positions": [],
        "position_tiers": {"tier_a_core": ["PLTR"]},
        "accounts": [],
    }
    cfg.update(config_extra or {})
    return {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": quotes or {},          # PLTR quote MISSING (22/36 cycle)
        "technicals": tech if tech is not None else _PLTR_TECH,
        "positions": _PLTR_POSITIONS,
        "earnings_calendar": {},
        "recommendations_list": [],
        "_config": cfg,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1a — broker-quote fallback gives the guard a drift reference
# ─────────────────────────────────────────────────────────────────────────────

def test_vintage_guard_broker_quote_fallback():
    """PLTR's yfinance quote was missing, but the E*TRADE positions payload
    carried the live-ish price ($163.30 — the same number the card's
    '-11% below spot' math used). The guard must derive the drift reference
    from it instead of silently skipping the name."""
    flags = vg.compute_flags({}, _PLTR_TECH, None, positions=_PLTR_POSITIONS)
    assert "PLTR" in flags
    f = flags["PLTR"]
    assert not f.get("unverified")
    assert f["price_source"] == "broker_position"
    assert abs(f["move_pct"] - 30.0) < 1.0  # (163.30-125.65)/125.65 ≈ +30%
    # Legacy 3-arg call (no positions) keeps the old fail-open behavior.
    assert vg.compute_flags({}, _PLTR_TECH, None) == {}


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1b — truly unverifiable vintage → no favourable badge survives
# ─────────────────────────────────────────────────────────────────────────────

def test_unverifiable_vintage_no_favorable_badge():
    """When NEITHER a quote nor a broker position price exists, the vintage
    is unverifiable — 'RSI 48 🟢 pullback' and '✅ RSI favourable' must be
    downgraded to '⚠ unverified (no live quote this cycle) — do not trust
    the favourable read' (promote → keep+caution, never a 🟢 badge)."""
    flags = vg.compute_flags({}, _PLTR_TECH, None, positions=[])
    assert flags.get("PLTR", {}).get("unverified") is True

    card = "\n".join([
        "### 💎 8. LONG DATED CSP · `PLTR`",
        "",
        "- **Triggers:** ✅ RSI favourable · RSI 48; third-party BUY",
        "- **Yield/Cost:** premium $930 · RSI 48 🟢 pullback",
    ])
    out, stats = vg.annotate_briefing(card, flags)
    assert "✅ RSI favourable" not in out
    assert "🟢 pullback" not in out
    assert "⚠ unverified (no live quote this cycle)" in out
    assert "do not trust the favourable read" in out
    assert stats["rsi_tagged"] >= 1
    foot = vg.footer(flags, None)
    assert foot and "PLTR" in foot and "favourable RSI badges withheld" in foot


def test_resolver_statuses():
    """resolve_new_open_rsi: broker fallback recomputes a live RSI in the
    hard-block zone for the PLTR shape; no price source at all → unverified;
    quiet verified name → fresh with the snapshot value."""
    r = vg.resolve_new_open_rsi("PLTR", _PLTR_TECH, quotes={},
                                positions=_PLTR_POSITIONS)
    assert r["status"] == "live" and r["verified"]
    assert r["rsi"] is not None and r["rsi"] >= 70.0
    assert r["price_source"] == "broker_position"

    r2 = vg.resolve_new_open_rsi("PLTR", _PLTR_TECH, quotes={}, positions=[])
    assert r2["status"] == "unverified" and not r2["verified"]
    assert r2["rsi"] == 48.0  # snapshot value kept, but flagged untrusted

    quiet = {"PLTR": dict(_PLTR_TECH["PLTR"], spot=163.0)}
    r3 = vg.resolve_new_open_rsi("PLTR", quiet, quotes={"PLTR": {"last": 163.3}},
                                 positions=_PLTR_POSITIONS)
    assert r3["status"] == "fresh" and r3["rsi"] == 48.0


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1c — the PULLBACK CSP surface blocks on the LIVE RSI
# ─────────────────────────────────────────────────────────────────────────────

def _render_pltr(monkeypatch, snap):
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 145.0, "mid": 3.60,
                                          "expiration": "2026-09-04"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "PLTR", "price": 163.30, "weight": 0.08,
                       "pl_pct": 0.4, "recommendation": "HOLD", "qty": 500}]
    return "\n".join(render_action_list(
        equity_reviews, [], [], analytics={},
        snapshot_data=snap, date_str=date.today().isoformat()))


def test_pltr_shape_live_rsi_blocks_pullback_csp(monkeypatch):
    """The observed 'PULLBACK CSP PLTR — sell $145P ... RSI 48 🟢 pullback'
    must NOT render as an actionable card: the broker-fallback price ($163.30
    vs the $125.65 technicals close) triggers the vintage check, the live
    Wilder RSI recomputes ≥70, and the put-sale hard block demotes the idea
    to the transparency footer quoting the LIVE value."""
    md = _render_pltr(monkeypatch, _pltr_snap())
    assert "**CSP — PAID-TO-WAIT** PLTR — sell" not in md  # no actionable card
    assert "CSP — PAID-TO-WAIT PLTR blocked" in md          # rule #24 footer
    assert "overbought" in md
    assert "recomputed" in md                             # live-RSI evidence
    assert "RSI 48 🟢 pullback" not in md


def test_verified_fresh_favored_band_unchanged(monkeypatch):
    """Regression: a verified-fresh favoured-band candidate renders exactly as
    before — numbered actionable card, no unverified/pre-gap tag.

    Chase-guard alignment (rule #44): the quiet-name fixture now carries a
    QUIET close series at the current price level (spot +0.4% vs the last
    technicals close, ~flat over 5 sessions). The original fixture reused the
    gap-day closes (125s) against the $163.30 equity-review spot, which the
    now-present chase guard correctly reads as a +30% vertical and blocks —
    that's the OTHER tests' scenario, not this regression's intent."""
    quiet_closes = [161.8, 162.0, 162.4, 161.9, 162.2, 162.6, 162.1, 162.8,
                    163.0, 162.5, 162.9, 162.3, 163.1, 162.6, 162.65]
    quiet_tech = {"PLTR": {"spot": 162.65, "rsi_14": 48.0, "sma_200": 110.0,
                           "recent_closes": quiet_closes}}
    snap = _pltr_snap(quotes={"PLTR": {"last": 163.30}},  # +0.4% vs close
                      tech=quiet_tech)
    md = _render_pltr(monkeypatch, snap)
    assert "**CSP — PAID-TO-WAIT** PLTR — sell $145P" in md
    assert "unverified" not in md
    assert "pre-gap" not in md
    assert "stale RSI" not in md
    assert "chase guard" not in md


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1d — the validator receives the live/recomputed RSI
# ─────────────────────────────────────────────────────────────────────────────

def test_validator_gets_live_rsi():
    """The observed card carried 'Trade-validator: ✅ GOOD TRADE' next to the
    stale RSI 48. build_context_from_snapshot must resolve ctx.rsi through
    the vintage guard so RSI_OVERBOUGHT_PUT fires on the live ≥70 value."""
    snap = _pltr_snap()
    ctx = ptv.build_context_from_snapshot(
        snap, ticker="PLTR", strike=145.0,
        expiration=date.today().replace(year=date.today().year + 1),
        option_type="PUT", action="SELL_OPEN", quantity=1,
    )
    assert ctx.rsi is not None and ctx.rsi >= 70.0        # live, not 48
    findings = ptv.validate_proposed_trade(ctx, snap["_config"])
    assert any(f.rule_id == "RSI_OVERBOUGHT_PUT" and f.severity == ptv.SEV_BLOCK
               for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — Tier A concentration text on the PULLBACK CSP card
# ─────────────────────────────────────────────────────────────────────────────

def test_pullback_csp_tier_a_cap_22(monkeypatch):
    """The observed card said '⚠️ Concentration check: Assignment would push
    PLTR to ~12.6% NLV (over 10% cap)' on a Tier A name whose cap is 22% —
    the same run's Risk Alerts said 'within Tier A bounds'. 12.6% on Tier A
    must render 'within Tier A bounds (cap 22%)' with NO warning."""
    # Fresh quote so the RSI gate doesn't demote the card; equity qty large
    # enough that assignment lands ~12.6% NLV.
    snap = _pltr_snap(quotes={"PLTR": {"last": 126.1}})
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 145.0, "mid": 3.60,
                                          "expiration": "2026-09-04"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "PLTR", "price": 126.1, "weight": 0.11,
                       "pl_pct": 0.4, "recommendation": "HOLD", "qty": 880}]
    md = "\n".join(render_action_list(
        equity_reviews, [], [], analytics={},
        snapshot_data=snap, date_str=date.today().isoformat()))
    assert "**CSP — PAID-TO-WAIT** PLTR" in md
    assert "over 10% cap" not in md
    assert "within Tier A bounds" in md and "cap 22%" in md
    assert "⚠️ Concentration check" not in md              # info line, not warn


def test_pullback_csp_tier_a_breach_warns_at_tier_cap(monkeypatch):
    """Past the Tier A cap the warning fires — against the 22% cap, never the
    legacy 10%."""
    snap = _pltr_snap(quotes={"PLTR": {"last": 126.1}})
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 145.0, "mid": 3.60,
                                          "expiration": "2026-09-04"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "PLTR", "price": 126.1, "weight": 0.21,
                       "pl_pct": 0.4, "recommendation": "HOLD", "qty": 1750}]
    md = "\n".join(render_action_list(
        equity_reviews, [], [], analytics={},
        snapshot_data=snap, date_str=date.today().isoformat()))
    assert "over Tier A cap 22%" in md
    assert "over 10% cap" not in md


# ─────────────────────────────────────────────────────────────────────────────
# Rule #46(e) — quote-fetch coverage surfaced in the Live-Data policy panel
# ─────────────────────────────────────────────────────────────────────────────

def _police():
    policer_dir = (Path(__file__).resolve().parents[3]
                   / "live-data-policer" / "scripts")
    sys.path.insert(0, str(policer_dir))
    import police
    return police


def test_quote_coverage_header_line():
    """The run log showed 'only 22/36 symbols got yfinance quotes' but the
    briefing said nothing. Coverage < 90% must surface the count in the
    Live-Data policy panel; ≥ 90% (or missing counts) stays silent."""
    from datetime import datetime
    police = _police()
    now = datetime.now().isoformat()

    def snap(requested, fetched):
        return {"data_provenance": {
            "positions": {"source": "etrade_live", "fetched_at": now, "fresh": True},
            "broker_positions": {"source": "etrade_live", "fetched_at": now, "fresh": True},
            "quotes": {"source": "yfinance", "fetched_at": now, "fresh": True,
                       "requested": requested, "fetched": fetched},
            "chains": {"source": "etrade_live", "fetched_at": now, "fresh": True},
            "iv_ranks": {"source": "yfinance_252d", "fetched_at": now, "fresh": True},
            "earnings_calendar": {"source": "yfinance", "fetched_at": now, "fresh": True},
        }}

    r = police.police_data_freshness(snap(36, 22))
    assert r.verdict == "WARN"
    assert "22/36" in r.panel_md
    assert "Live-Data Policy" in r.panel_md
    assert not r.blocking_sources                       # advisory, never BLOCK

    r_ok = police.police_data_freshness(snap(36, 35))   # 97% ≥ 90% floor
    assert not any(s.get("source") == "quote_coverage" for s in r_ok.stale_sources)

    # Fail-open: older snapshots without the counts → no coverage line.
    legacy = snap(36, 22)
    legacy["data_provenance"]["quotes"] = {
        "source": "yfinance", "fetched_at": now, "fresh": True}
    r_legacy = police.police_data_freshness(legacy)
    assert not any(s.get("source") == "quote_coverage" for s in r_legacy.stale_sources)
