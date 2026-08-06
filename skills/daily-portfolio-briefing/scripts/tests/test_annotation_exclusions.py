"""Junk-line chip leak tests + shared exclusion helper (task #39, bug 3).

User symptom (2026-07-30 briefing, MSFT LONG DATED CSP card): the
"- **Triggers:**" and "- **Yield/Cost:**" sub-lines carried "🅿️ no rec ·
🔵 Tier C" chips contradicting the card header's "🅿️ TOP 12 · 🔥 High · 1d ·
🟢 Tier A". Root cause: the bare-token fallback extracted "LT" (from "LT
verdict"), "TRADE" (from "E*TRADE"), and "LEAP" from prose on labelled
sub-lines that rule #27 excludes from annotation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import line_exclusions  # noqa: E402
from analysis import parkev_chip  # noqa: E402
from analysis import position_tiers  # noqa: E402
from analysis import intrinsic_value  # noqa: E402
from analysis import vintage_guard  # noqa: E402


RECS = {
    "MSFT": {"ticker": "MSFT", "rating_tier": 4,
             "raw_recommendation": "Top 12 Stock", "recommendation": "BUY",
             "conviction": "High", "age_days": 1, "aging": False},
}
TIER_CFG = {"position_tiers": {"tier_a_core": ["MSFT"]}}

CARD = [
    "### 💎 8. LONG DATED CSP · `MSFT`",
    "",
    "**Trade:** SELL 1× MSFT $410P exp Fri Oct 16 '26 (78 DTE)",
    "",
    "- **Triggers:** ⏸ Deferred (capacity gated); ✅ RSI favourable · RSI 50; "
    "⚠ LT verdict `downtrend` (-9.8% vs 200-SMA); third-party BUY",
    "- **Rationale:** Stock-replacement LEAP: deep-ITM call captures upside.",
    "- **Yield/Cost:** premium $930 (mid $9.30) · _Source: Live E*TRADE chain_",
    "- **Source:** recommendation-list-fetcher + yfinance IV",
]


def test_no_chips_on_rationale_trigger_yield_source_lines():
    """Sweep BOTH annotators over the card: labelled sub-lines get no 🅿️ chip
    and no tier badge."""
    md = "\n".join(CARD)
    md = parkev_chip.annotate_parkev_chips(md, RECS)
    md = position_tiers.annotate_tier_badges(md, TIER_CFG)
    lines = md.splitlines()
    header = lines[0]
    assert "🅿️ TOP 12" in header and "🟢 Tier A" in header
    for label in ("- **Triggers:**", "- **Rationale:**", "- **Yield/Cost:**",
                  "- **Source:**"):
        line = [l for l in lines if l.startswith(label)][0]
        assert "🅿️" not in line, f"Parkev chip leaked onto {label}: {line}"
        assert "Tier" not in line, f"Tier badge leaked onto {label}: {line}"


def test_chip_annotators_share_exclusion_helper():
    """Structural: every annotator imports the SAME exclusion helper module —
    the rule-#27 exclusion list lives in exactly one place."""
    assert parkev_chip.line_exclusions is line_exclusions
    assert position_tiers.line_exclusions is line_exclusions
    assert intrinsic_value.line_exclusions is line_exclusions
    assert vintage_guard.line_exclusions is line_exclusions


def test_exclusion_helper_labels_and_continuations():
    assert line_exclusions.is_excluded_line("- **Triggers:** RSI 50")
    assert line_exclusions.is_excluded_line("- **Rationale:** because")
    assert line_exclusions.is_excluded_line("- **Yield/Cost:** $930")
    assert line_exclusions.is_excluded_line("- **Source:** yfinance")
    assert line_exclusions.is_excluded_line("- **Why:** strongest signal")
    assert line_exclusions.is_excluded_line("  ↳ S: $388 · R: $431")
    assert line_exclusions.is_excluded_line("_italic transparency footer_")
    # Card headers / trade tickets / watch rows are NOT excluded.
    assert not line_exclusions.is_excluded_line("### 💎 8. LONG DATED CSP · `MSFT`")
    assert not line_exclusions.is_excluded_line("**Trade:** SELL 1× MSFT $410P")
    assert not line_exclusions.is_excluded_line("- **AMZN** @ $227.72 — HOLD")


def test_bare_token_stoplist_covers_briefing_vocabulary():
    """Belt-and-suspenders: LT / TRADE / LEAP / STOCK never extract as tickers."""
    for text in ("⚠ LT verdict downtrend", "via E*TRADE chain",
                 "Stock-replacement LEAP strategy"):
        assert parkev_chip._extract_ticker(text) is None, text
        assert position_tiers._extract_ticker(text) is None, text


def test_regression_msft_card_2026_07_30():
    """End-to-end over the real card shape: run the aggregate post-pass chain
    (intrinsic → parkev → tier → vintage) and assert the three 2026-07-30
    symptoms are gone: DCF $257 (AT&T's value), Tier C chips on sub-lines,
    un-tagged pre-gap RSI."""
    etfs = intrinsic_value.default_etf_set()
    fv = {
        "MSFT": {"dcf": 289.6, "analyst_target": 537.7, "num_analysts": 19},
        "T": {"dcf": 257.0, "analyst_target": 25.2, "num_analysts": 9},
    }
    spot = {"MSFT": 450.21, "T": 24.0}
    lines, _ = intrinsic_value.annotate_intrinsic(
        list(CARD), fv_by_ticker=fv, spot_by_ticker=spot,
        known_tickers=["MSFT", "T"], etf_set=etfs,
    )
    md = "\n".join(lines)
    md = parkev_chip.annotate_parkev_chips(md, RECS)
    md = position_tiers.annotate_tier_badges(md, TIER_CFG)
    flags = vintage_guard.compute_flags(
        {"MSFT": {"last": 450.21}},
        {"MSFT": {"spot": 390.54, "sma_200": 431.0, "rsi_14": 50.0}},
        None,
    )
    md, _ = vintage_guard.annotate_briefing(md, flags)

    # Bug 2: AT&T's fair value can no longer attach anywhere on the card.
    assert "DCF $257" not in md
    assert "DCF $290" in md  # MSFT's real value is present (on the Trade line)
    # Bug 3: no Tier C / no-rec chips on sub-lines; header keeps Tier A.
    assert "Tier C" not in md
    assert "🅿️ no rec" not in md
    assert "🟢 Tier A" in md
    # Bug 1: the stale promote badge is stripped and RSI carries the pre-gap
    # tag; the 200-SMA distance is recomputed at live spot.
    assert "✅ RSI favourable" not in md
    assert "RSI 50 ⚠ pre-gap" in md
    assert "ABOVE the 200-SMA" in md
