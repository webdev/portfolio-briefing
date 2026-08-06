"""Same-cycle vintage guard tests (task #39, bug 1).

User symptom (2026-07-30 briefing, MSFT LONG DATED CSP card): "✅ RSI
favourable · RSI 50" rendered on a name that closed $390.54 yesterday and
gapped to $450.21 today (+15.3%) — the RSI was computed pre-gap; the real
post-gap RSI (~65-70) would flip the hook from promote to caution. The same
card said "⚠ LT verdict `downtrend` (-9.8% vs 200-SMA)" while at $450 live
spot MSFT was ABOVE its $431 200-SMA.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import vintage_guard as vg  # noqa: E402


TECH = {"MSFT": {"spot": 390.54, "sma_200": 431.0, "rsi_14": 50.0}}
QUOTES = {"MSFT": {"last": 450.21}}

CARD = "\n".join([
    "### 💎 8. LONG DATED CSP · `MSFT`",
    "",
    "**Trade:** SELL 1× MSFT $410P exp Fri Oct 16 '26 (78 DTE)",
    "",
    "- **Triggers:** ⏸ Deferred (capacity gated); ✅ RSI favourable · RSI 50; "
    "⚠ LT verdict `downtrend` (-9.8% vs 200-SMA) — kept only via fresh "
    "high-conviction override (tier 4, 1d, BUY); third-party BUY",
    "- **Rationale:** Patient capital trade: elevated IV 63 + 78-DTE horizon.",
    "- **Yield/Cost:** premium $930 (mid $9.30) · _Source: Live E*TRADE chain_",
])


def test_vintage_guard_flags_stale_rsi_on_gap():
    flags = vg.compute_flags(QUOTES, TECH, None)
    assert "MSFT" in flags
    assert abs(flags["MSFT"]["move_pct"] - 15.3) < 0.1
    out, stats = vg.annotate_briefing(CARD, flags)
    # The promote badge is stripped — never "✅ RSI favourable" on a >5% mover.
    assert "✅ RSI favourable" not in out
    # The RSI value stays visible but carries the pre-gap staleness tag.
    assert "RSI 50 ⚠ pre-gap" in out
    assert "treat as stale" in out
    assert stats["rsi_tagged"] >= 1


def test_vintage_guard_recomputes_sma_distance():
    flags = vg.compute_flags(QUOTES, TECH, None)
    assert abs(flags["MSFT"]["vs_sma200_live_pct"] - 4.5) < 0.1
    out, stats = vg.annotate_briefing(CARD, flags)
    # The stale "(-9.8% vs 200-SMA)" read is recomputed at live spot and the
    # sign flip (below → ABOVE the 200-SMA) is called out explicitly.
    assert "(-9.8% vs 200-SMA)" not in out
    assert "+4.5% vs 200-SMA at live spot $450.21" in out
    assert "ABOVE the 200-SMA" in out
    assert stats["sma_recomputed"] == 1


def test_vintage_guard_quiet_day_no_tags():
    quiet_quotes = {"MSFT": {"last": 394.4}}  # ~1% move
    flags = vg.compute_flags(quiet_quotes, TECH, None)
    assert flags == {}
    out, stats = vg.annotate_briefing(CARD, flags)
    assert out == CARD
    assert stats["rsi_tagged"] == 0 and stats["sma_recomputed"] == 0


def test_vintage_guard_disabled_config():
    cfg = {"vintage_guard": {"enabled": False}}
    assert vg.compute_flags(QUOTES, TECH, cfg) == {}


def test_vintage_guard_threshold_config():
    # A 15.3% move is under a (silly) 20% threshold → no flag.
    cfg = {"vintage_guard": {"enabled": True, "max_intraday_move_pct": 0.20}}
    assert vg.compute_flags(QUOTES, TECH, cfg) == {}


def test_vintage_guard_fail_open_on_missing_data():
    assert vg.compute_flags(None, None, None) == {}
    assert vg.compute_flags({}, {"MSFT": {"rsi_14": 50}}, None) == {}  # no close
    assert vg.compute_flags({"MSFT": {}}, TECH, None) == {}  # no usable quote


def test_vintage_guard_does_not_bleed_onto_other_cards():
    md = CARD + "\n\n" + "\n".join([
        "### 💎 9. LONG DATED CSP · `NFLX`",
        "",
        "- **Triggers:** ✅ RSI favourable · RSI 44; third-party BUY",
    ])
    flags = vg.compute_flags(QUOTES, TECH, None)
    out, _ = vg.annotate_briefing(md, flags)
    # NFLX did not gap — its promote badge must be untouched.
    assert "✅ RSI favourable · RSI 44" in out
    # MSFT's badge is still stripped.
    assert "RSI 50 ⚠ pre-gap" in out


def test_vintage_guard_footer_names_the_movers():
    flags = vg.compute_flags(QUOTES, TECH, None)
    f = vg.footer(flags, None)
    assert f and "MSFT +15.3%" in f and f.startswith("_")
