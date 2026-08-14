"""Rule #50 — Tier A covered calls are ALGORITHM-GATED, not banned.

George (2026-08-14): "I want to relax our covered calls strategy... we have
long-term stocks notion... we should allow writing covered calls for ALL of
them and incorporate that in the code, in the algorithm. So when there is a
good [wheel] formula for selling a covered call based on RSI, IV rank,
resistance, and strike, and everything included in the recommendation
obviously, based on my holdings."

Pins the contract:
  (a) `algorithm_gated.enabled` makes EVERY Tier A ticker CC-eligible
      (whitelist obsolete when on, honored when off — backward compatible).
  (b) An ENTER-verdict Tier A holding renders an actionable ticket with the
      algorithm-gated envelope (min OTM, delta cap, ≤50% coverage, monthly
      DTE window, real chain quote).
  (c) WAIT/BLOCKED verdicts render a visible reason, never an actionable
      ticket; missing live chain fails CLOSED.
  (d) The tax-aware LTCG block caps/suppresses coverage when lots sit in
      the 60-day pre-LTCG window.
  (e) Legacy config (no algorithm_gated block) keeps the old behavior
      byte-identical, incl. tier_a_no_cc transparency records.
  (f) Coverage-cap math: 476 GOOG shares → 4 round lots → max 2 written.
  (g) Day-color / RSI hard block / B floor are INHERITED from the canonical
      entry algorithm (rule #48) — no parallel logic.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis import position_tiers
from steps.strategy_upgrades import compute_strategy_upgrades
import steps.strategy_upgrades as _su
from render.strategy_upgrades_panel import render_strategy_upgrades


# ─── Fixtures ──────────────────────────────────────────────────────────────

_TIER_B_C = {
    "tier_b": {"enabled": True, "rsi_floor": 70, "min_otm_pct": 10.0,
               "max_delta": 0.15, "coverage_cap_pct": 50, "max_dte": 30,
               "roll_up_trigger": 0.93, "tax_aware_assignment_block": True},
    "tier_c": {"enabled": True, "rsi_floor": 60, "min_otm_pct": 4.0,
               "max_delta": 0.30, "coverage_cap_pct": 100, "max_dte": 45,
               "roll_up_trigger": 0.97, "tax_aware_assignment_block": False},
}


def _ag_config(*, algorithm_gated=True, extra=None):
    """Config with the strict Tier A envelope + NVDA whitelist (rule #34)
    and, by default, the rule-#50 algorithm_gated block enabled."""
    tier_a = {
        "enabled": True, "rsi_floor": 75, "min_otm_pct": 20.0,
        "max_delta": 0.10, "coverage_cap_pct": 20, "max_dte": 30,
        "roll_up_trigger": 0.92, "tax_aware_assignment_block": True,
        "willing_to_write_cc_on": ["NVDA"],
    }
    if algorithm_gated is not None:
        tier_a["algorithm_gated"] = {"enabled": bool(algorithm_gated)}
    cfg = {
        "max_position_pct": 0.10,
        "position_tiers": {
            "tier_a_core": ["GOOG", "MSFT", "NVDA", "AMZN", "META"],
            "tier_b_income": ["MU"],
        },
        "covered_call_tiers": {"tier_a": tier_a, **_TIER_B_C},
    }
    if extra:
        cfg.update(extra)
    return cfg


def _goog_position(qty=476, price=340.0, lots=None):
    pos = {
        "symbol": "GOOG", "assetType": "EQUITY", "qty": qty, "price": price,
        "costBasis": 200.0,
        "accountsBreakdown": [f"INDIVIDUAL: {int(qty)} sh"],
    }
    if lots is not None:
        pos["lots"] = lots
    return pos


def _ag_snapshot(*, rsi=65.0, spot=340.0, quote_last=None, position=None):
    return {
        "positions": [position or _goog_position()],
        "balance": {"accountValue": 2_000_000, "cash": 100_000},
        "chains": {},
        "earnings_calendar": {},
        "quotes": {"GOOG": {"last": spot if quote_last is None else quote_last}},
        "technicals": {"GOOG": {"rsi_14": rsi, "spot": spot}},
    }


class _AgFakeFetcher:
    """Chain-fetcher stub returning a fixed envelope-compliant quote:
    $380C on a $340 spot (11.8% OTM), δ0.12, mid $6.00, ~35 DTE monthly."""

    def __init__(self, quote=None):
        self._exp = date.today() + timedelta(days=35)
        self._q = quote or {
            "strike": 380.0, "bid": 5.90, "mid": 6.00, "ask": 6.10,
            "delta": 0.12, "iv": 0.32, "open_interest": 500,
            "expiration": self._exp.isoformat(), "source": "etrade_live",
        }

    def choose_expiration(self, **kw):
        return self._exp

    def find_strike_near_delta(self, **kw):
        return dict(self._q)

    def find_strike_at_otm_pct(self, **kw):
        return None


def _goog_write(upgrades):
    return [u for u in upgrades
            if u.get("type") == "write_covered_call"
            and u.get("underlying") == "GOOG"]


def _run(snapshot, config, monkeypatch, fetcher=None):
    if fetcher is not None:
        monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fetcher, None))
    return compute_strategy_upgrades(
        snapshot, equity_reviews=[], options_reviews=[], params=config)


# ─── (a) Eligibility — whitelist obsolete when on, honored when off ────────


def test_algorithm_gated_enables_all_tier_a_tickers():
    """George: "we should allow writing covered calls for ALL of them" —
    with algorithm_gated on, every Tier A ticker is CC-eligible, whether or
    not it sits on the rule-#34 whitelist."""
    cfg = _ag_config()
    for tk in ("GOOG", "MSFT", "AMZN", "META", "NVDA"):
        assert position_tiers.is_cc_enabled_for_tier("A", cfg, ticker=tk), tk
    # Even without ticker context — the algorithm is the gate, not a name.
    assert position_tiers.is_cc_enabled_for_tier("A", cfg)


def test_whitelist_still_honored_when_algorithm_gated_absent_or_disabled():
    """Backward compatible: absent/disabled algorithm_gated block → the
    rule-#34 per-name whitelist is the ONLY way in (NVDA yes, GOOG no)."""
    for cfg in (_ag_config(algorithm_gated=None),
                _ag_config(algorithm_gated=False)):
        assert position_tiers.is_cc_enabled_for_tier("A", cfg, ticker="NVDA")
        assert not position_tiers.is_cc_enabled_for_tier("A", cfg, ticker="GOOG")
        assert not position_tiers.is_cc_enabled_for_tier("A", cfg)


def test_algorithm_gated_envelope_overlays_the_strict_tier_a_settings():
    """The Tier A envelope under algorithm_gated: min_otm 10 / max_delta
    0.15 / coverage ≤50% / DTE 21-45 / tax-aware block — not the punitive
    RSI-75 strict envelope; the canonical entry algorithm owns the RSI gate
    (rsi_floor collapses to 0, never a parallel floor)."""
    s = position_tiers.cc_settings_for_tier("A", _ag_config())
    assert s["algorithm_gated"] is True
    assert s["enabled"] is True
    assert s["rsi_floor"] == 0
    assert s["min_otm_pct"] == 10.0
    assert s["max_delta"] == 0.15
    assert s["coverage_cap_pct"] == 50
    assert s["min_dte"] == 21
    assert s["max_dte"] == 45
    assert s["tax_aware_assignment_block"] is True
    # Legacy config keeps the strict envelope byte-identical.
    legacy = position_tiers.cc_settings_for_tier(
        "A", _ag_config(algorithm_gated=None))
    assert "algorithm_gated" not in legacy
    assert legacy["rsi_floor"] == 75
    assert legacy["min_otm_pct"] == 20.0
    assert legacy["coverage_cap_pct"] == 20


def test_tier_b_and_c_envelopes_unchanged_by_algorithm_gated():
    cfg = _ag_config()
    b = position_tiers.cc_settings_for_tier("B", cfg)
    c = position_tiers.cc_settings_for_tier("C", cfg)
    assert b["rsi_floor"] == 70 and b["coverage_cap_pct"] == 50
    assert c["rsi_floor"] == 60 and c["coverage_cap_pct"] == 100
    assert "algorithm_gated" not in b and "algorithm_gated" not in c


# ─── (b) + (f) ENTER → actionable ticket with the envelope ─────────────────


def test_enter_verdict_renders_actionable_ticket_with_envelope(monkeypatch):
    """"So when there is a good [wheel] formula for selling a covered call
    based on RSI, IV rank, resistance, and strike" — RSI 65 (strength),
    fresh vintage, envelope-compliant $380C (11.8% OTM, δ0.12, 35 DTE, 16%+
    ann) → the canonical evaluator says ENTER and the ticket is actionable:
    476 GOOG shares → 4 round lots → coverage cap 50% → SELL 2×."""
    snap = _ag_snapshot(rsi=65.0)
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    writes = _goog_write(ups)
    assert len(writes) == 1
    w = writes[0]
    assert w["algorithm_gated"] is True
    assert w["entry_verdict"] == "ENTER"
    assert not w["algo_wait"]
    assert w["tier"] == "A"
    assert w["tier_violations"] == []
    assert w["contracts_writable"] == 2          # (f) 4 lots × 50% = 2
    assert w["contracts_in_account"] == 4
    assert w["target_strike"] == 380.0
    assert w["target_delta"] == 0.12
    assert w["chain_source"] == "etrade_live"
    assert 21 <= w["target_dte"] <= 45           # monthly window
    # No tier_a_no_cc transparency record — the holding IS eligible now.
    assert not [u for u in ups if u.get("type") == "tier_a_no_cc"
                and u.get("underlying") == "GOOG"]

    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" in md
    assert "SELL 2× GOOG $380C" in md
    assert "🧮" in md                             # the evaluator's one-liner


def test_coverage_cap_never_exceeds_half_the_lots(monkeypatch):
    """Protection #1: ≤50% coverage — 700 shares → 7 lots → max 3 CCs."""
    snap = _ag_snapshot(position=_goog_position(qty=700))
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["contracts_writable"] == 3


# ─── (c) WAIT / BLOCKED → visible reason, never actionable ─────────────────


def test_wait_verdict_renders_reason_never_actionable(monkeypatch):
    """'⏸ GOOG — CC waits' style: RSI 50 is mid-range (wait for strength)
    — the evaluator returns WAIT and the write renders in the wait section
    with the measured reason, never as ✅ READY TO WRITE."""
    snap = _ag_snapshot(rsi=50.0)
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["entry_verdict"] == "WAIT"
    assert w["algo_wait"] is True
    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" not in md
    assert "wait for strength" in md.lower()
    assert "**🧮 Entry algorithm:**" in md


def test_blocked_rsi_inherited_from_the_canonical_evaluator(monkeypatch):
    """(g spot check) RSI 30 is the call-side oversold HARD BLOCK — the
    verdict comes from the SAME rsi_discipline hook the entry algorithm
    runs (rule #48 — no parallel logic), and the write is never
    actionable (held back visibly)."""
    snap = _ag_snapshot(rsi=30.0)
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["entry_verdict"] == "BLOCKED"
    assert w["rsi_blocked"] is True
    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" not in md
    assert "Held back by RSI" in md


def test_missing_live_chain_fails_closed(monkeypatch):
    """A rule-of-thumb estimate must never cap a Tier A compounder: chain
    fetcher unavailable → algo_wait with the fail-closed reason, no
    actionable ticket (rule #10)."""
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (None, None))
    snap = _ag_snapshot(rsi=65.0)
    ups = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=_ag_config())
    w = _goog_write(ups)[0]
    assert w["algo_wait"] is True
    assert "chain unavailable" in (w["entry_reason"] or "")
    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" not in md
    assert "chain unavailable" in md


# ─── (d) Tax-aware LTCG lot block ──────────────────────────────────────────


def test_tax_aware_block_caps_coverage_to_ltcg_safe_lots(monkeypatch):
    """Protection #2: 300 shares acquired 330d ago sit inside the 60-day
    pre-LTCG window — only the 176 LTCG-safe shares (1 round lot) may back
    the write; contracts capped 2 → 1 with the measured note."""
    today = date.today()
    lots = [
        {"acquiredDate": (today - timedelta(days=330)).isoformat(), "qty": 300},
        {"acquiredDate": (today - timedelta(days=800)).isoformat(), "qty": 176},
    ]
    snap = _ag_snapshot(position=_goog_position(qty=476, lots=lots))
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["contracts_writable"] == 1
    assert w["tax_aware_note"] and "pre-LTCG" in w["tax_aware_note"]
    md = "\n".join(render_strategy_upgrades(ups))
    assert "🧾 tax-aware block" in md


def test_tax_aware_block_suppresses_when_no_safe_lot(monkeypatch):
    """All lots inside the window → NO LTCG-safe round lot → the write
    demotes to the wait list with the measured tax note (never hidden)."""
    today = date.today()
    lots = [{"acquiredDate": (today - timedelta(days=320)).isoformat(),
             "qty": 476}]
    snap = _ag_snapshot(position=_goog_position(qty=476, lots=lots))
    ups = _run(snap, _ag_config(), monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["tier_envelope_wait"] is True
    assert any("pre-LTCG" in v for v in w["tier_violations"])
    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" not in md
    assert "no LTCG-safe round lot" in md


def test_tax_aware_fails_open_without_lot_data():
    """No lot data → no adjustment fabricated (rule #19)."""
    settings = {"tax_aware_assignment_block": True}
    assert position_tiers.tax_aware_cc_lot_adjustment(
        {"symbol": "GOOG", "qty": 476}, settings) is None
    assert position_tiers.tax_aware_cc_lot_adjustment(
        {"symbol": "GOOG", "lots": []}, settings) is None
    # Flag off → None even with at-risk lots.
    lots = [{"acquiredDate": (date.today() - timedelta(days=330)).isoformat(),
             "qty": 300}]
    assert position_tiers.tax_aware_cc_lot_adjustment(
        {"lots": lots}, {"tax_aware_assignment_block": False}) is None


# ─── (e) Legacy config = byte-identical old behavior ───────────────────────


def test_legacy_config_keeps_tier_a_no_cc_records(monkeypatch):
    """Without the algorithm_gated block, a non-whitelisted Tier A holding
    still yields the tier_a_no_cc transparency record and NO write — the
    pre-rule-#50 behavior, byte-identical."""
    snap = _ag_snapshot(rsi=65.0)
    ups = _run(snap, _ag_config(algorithm_gated=None), monkeypatch,
               _AgFakeFetcher())
    assert _goog_write(ups) == []
    recs = [u for u in ups if u.get("type") == "tier_a_no_cc"
            and u.get("underlying") == "GOOG"]
    assert len(recs) == 1
    assert "Tier A" in recs[0]["rationale"]
    md = "\n".join(render_strategy_upgrades(ups))
    assert "no CC, long-term core" in md


# ─── (g) Evaluator gates inherited — day color ─────────────────────────────


def test_day_color_red_day_wait_inherited_from_evaluator(monkeypatch):
    """Rule #49 inherited: with entry_algorithm.day_color enabled and GOOG
    -2.9% on the day, a new CC (sold into STRENGTH) gets the evaluator's
    '🟩 wait for a green day' — WAIT, never ENTER, no parallel logic."""
    cfg = _ag_config(extra={"entry_algorithm": {"day_color": {
        "enabled": True, "green_min_pct": 0.3, "red_min_pct": -0.3}}})
    snap = _ag_snapshot(rsi=65.0, spot=340.0, quote_last=330.0)  # -2.9%
    ups = _run(snap, cfg, monkeypatch, _AgFakeFetcher())
    w = _goog_write(ups)[0]
    assert w["entry_verdict"] == "WAIT"
    assert w["algo_wait"] is True
    assert "green day" in (w["entry_reason"] or "")
    md = "\n".join(render_strategy_upgrades(ups))
    assert "✅ READY TO WRITE" not in md
    assert "ENTRY ALGORITHM — WAIT" in md
    assert "wait for a green day" in md
