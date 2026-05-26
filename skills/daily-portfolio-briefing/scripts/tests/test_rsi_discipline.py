"""Tests for the RSI discipline module (standard wheel bands + hard gate)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import rsi_discipline as rd  # noqa: E402


# --- PUT side (selling cash-secured puts) ----------------------------------

def test_put_overbought_blocks():
    a = rd.assess(72, "put")
    assert a.action == "block"
    assert a.blocked is True
    assert a.zone == "blocked"
    assert "overbought" in a.label

def test_put_block_boundary_at_70():
    # block_above is inclusive at the threshold
    assert rd.assess(70, "put").action == "block"
    assert rd.assess(69.9, "put").action == "warn"  # extended caution band

def test_put_extended_warns():
    a = rd.assess(63, "put")
    assert a.action == "warn"
    assert a.zone == "caution"
    assert a.label == "extended"

def test_put_pullback_favored():
    a = rd.assess(38, "put")
    assert a.action == "allow"
    assert a.zone == "favored"
    assert a.label == "pullback"

def test_put_favored_boundaries():
    assert rd.assess(30, "put").zone == "favored"
    assert rd.assess(50, "put").zone == "favored"
    assert rd.assess(55, "put").zone == "neutral"  # between favored and caution

def test_put_falling_knife_warns_not_blocks():
    a = rd.assess(20, "put")
    assert a.action == "warn"        # caution, never a hard block on the low side
    assert a.label == "deeply oversold"


# --- CALL side (selling covered calls) -------------------------------------

def test_call_oversold_blocks():
    a = rd.assess(30, "call")
    assert a.action == "block"
    assert a.blocked is True
    assert a.label == "oversold"

def test_call_block_boundary_at_35():
    assert rd.assess(34.9, "call").action == "block"
    assert rd.assess(35, "call").action == "warn"   # >= block_below is not blocked

def test_call_midrange_warns():
    a = rd.assess(50, "call")
    assert a.action == "warn"
    assert a.label == "mid-range"

def test_call_extended_favored():
    a = rd.assess(68, "call")
    assert a.action == "allow"
    assert a.zone == "favored"
    assert a.label == "extended"


# --- Missing data ----------------------------------------------------------

def test_none_rsi_allows_with_unknown():
    a = rd.assess(None, "put")
    assert a.action == "allow"      # fail-open on the *gate* but flag it
    assert a.zone == "unknown"
    assert a.tag == "RSI n/a"

def test_unknown_side_defaults_to_put():
    a = rd.assess(72, "spread")
    assert a.side == "put"
    assert a.action == "block"


# --- tag() display ---------------------------------------------------------

def test_tag_put_overbought():
    assert rd.tag(72, "put") == "RSI 72 🔴 overbought"

def test_tag_put_pullback():
    assert rd.tag(38, "put") == "RSI 38 🟢 pullback"

def test_tag_neutral_has_no_word():
    # mid-50s on the put side is neutral → terse, value only
    assert rd.tag(54, "put") == "RSI 54"

def test_tag_none():
    assert rd.tag(None) == "RSI n/a"

def test_tag_side_agnostic():
    assert rd.tag(75) == "RSI 75 🔴 overbought"
    assert rd.tag(54) == "RSI 54"


# --- config override -------------------------------------------------------

def test_load_thresholds_defaults():
    th = rd.load_thresholds(None)
    assert th["put"]["block_above"] == 70.0
    assert th["call"]["block_below"] == 35.0
    assert th["enabled"] is True

def test_load_thresholds_override():
    th = rd.load_thresholds({"rsi_discipline": {"put": {"block_above": 65}, "enabled": False}})
    assert th["put"]["block_above"] == 65.0
    assert th["enabled"] is False
    # untouched keys keep defaults
    assert th["call"]["block_below"] == 35.0

def test_conservative_band_changes_gate():
    th = rd.load_thresholds({"rsi_discipline": {"put": {"block_above": 65}}})
    assert rd.assess(66, "put", th).action == "block"
    assert rd.assess(66, "put").action == "warn"   # default 70 → only warn


# --- helpers ---------------------------------------------------------------

def test_rsi_for():
    tech = {"NVDA": {"rsi_14": 41.2}, "MU": {"rsi_14": None}}
    assert rd.rsi_for("NVDA", tech) == 41.2
    assert rd.rsi_for("nvda", tech) == 41.2
    assert rd.rsi_for("MU", tech) is None
    assert rd.rsi_for("AAPL", tech) is None

def test_first_known_ticker_longest_first():
    tickers = sorted(["GOOG", "GOOGL", "NVDA"], key=len, reverse=True)
    assert rd.first_known_ticker("3. **TRIM** GOOGL (core) — ...", tickers) == "GOOGL"
    assert rd.first_known_ticker("1. **CLOSE** NVDA_PUT_195_20260620", tickers) == "NVDA"

def test_infer_side():
    assert rd.infer_side("1. **PULLBACK CSP** NVDA — sell $170P") == "put"
    assert rd.infer_side("2. **CLOSE** META_CALL_670_20260620 — +32%") == "call"
    assert rd.infer_side("3. **TRIM** GOOGL (core) — 12% NLV") is None

def test_annotate_action_lines():
    tech = {"NVDA": {"rsi_14": 72.0}, "MU": {"rsi_14": 41.0}}
    items = [
        "1. **PULLBACK CSP** MU — sell $115P exp Fri Jun 19",
        "   - **Why:** core position",
        "2. **CLOSE** NVDA_PUT_195_20260620 — +43%",
    ]
    out = rd.annotate_action_lines(items, tech)
    assert out[0].endswith("RSI 41 🟢 pullback")   # MU put open, favored
    assert out[1] == items[1]                       # sub-bullet untouched
    assert "RSI 72" in out[2]                        # NVDA close annotated
    # original list not mutated
    assert "RSI" not in items[0]

def test_annotate_skips_lines_already_having_rsi():
    tech = {"NVDA": {"rsi_14": 72.0}}
    items = ["1. **NEW CSP** NVDA — RSI 72 already here"]
    out = rd.annotate_action_lines(items, tech)
    assert out[0] == items[0]


# --- BUY side (equity adds / sub-lot completions) --------------------------

def test_buy_overbought_blocks():
    a = rd.assess(72, "buy")
    assert a.action == "block"
    assert a.label == "overbought"

def test_buy_block_boundary_at_70():
    assert rd.assess(70, "buy").action == "block"
    assert rd.assess(69, "buy").action == "warn"   # extended caution

def test_buy_pullback_favored():
    a = rd.assess(45, "buy")
    assert a.action == "allow"
    assert a.zone == "favored"

def test_buy_deep_oversold_still_favored():
    # buying a dip is fine; only overbought blocks
    assert rd.assess(20, "buy").action == "allow"

def test_tag_buy():
    assert rd.tag(72, "buy") == "RSI 72 🔴 overbought"
    assert rd.tag(45, "buy") == "RSI 45 🟢 pullback"


# --- central hook ----------------------------------------------------------

def test_hook_removes_overbought_put():
    v = rd.hook("put", 73)
    assert v.removed and v.decision == "remove"
    assert "overbought" in v.reason

def test_hook_removes_oversold_call():
    v = rd.hook("call", 30)
    assert v.removed
    assert "oversold" in v.reason

def test_hook_removes_overbought_buy():
    v = rd.hook("buy", 75)
    assert v.removed

def test_hook_promotes_favored():
    v = rd.hook("put", 38)
    assert v.promoted and v.badge == "✅ RSI favourable"
    v2 = rd.hook("buy", 45)
    assert v2.promoted

def test_hook_keeps_caution_with_badge():
    v = rd.hook("put", 63)  # extended → caution
    assert v.decision == "keep"
    assert v.badge == "⚠ RSI caution"

def test_hook_management_never_removed():
    for sd in (None, "manage", "close", "roll", "trim", "exit", "hedge", "collar"):
        v = rd.hook(sd, 75)
        assert v.decision == "keep"
        assert v.side == "manage"
        assert v.badge == ""   # management is annotated, not promoted/blocked

def test_hook_missing_rsi_keeps():
    v = rd.hook("put", None)
    assert v.decision != "remove"   # fail-open on the gate, but flagged
    assert v.tag == "RSI n/a"


# --- audit (RSI-on-every-recommendation verifier) --------------------------

def test_audit_flags_rec_line_missing_rsi():
    md = "\n".join([
        "1. **CLOSE** NVDA_PUT_195_20260717 — +40%",  # no RSI nearby
        "   - **Why:** captured profit",
    ])
    offenders = rd.audit_missing_rsi(md)
    assert any("CLOSE" in o for o in offenders)

def test_audit_passes_when_rsi_present():
    md = "\n".join([
        "1. **CLOSE** NVDA_PUT_195_20260717 — +40%  · RSI 60",
        "   - **Why:** captured profit",
    ])
    assert rd.audit_missing_rsi(md) == []

def test_audit_accepts_rsi_in_following_lines():
    md = "\n".join([
        "**SOFI — 700 shares (no CC yet)** ✅ READY TO WRITE",
        "  - SELL 7× SOFI $16.5C exp Fri Jun 26",
        "  - **RSI:** RSI 42 🟡 mid-range",
    ])
    # READY TO WRITE line has RSI within 2 lines → not flagged
    assert rd.audit_missing_rsi(md) == []
