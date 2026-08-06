"""FV wrong-ticker attachment regression tests (task #39, bug 2).

User symptom (2026-07-30 briefing, MSFT LONG DATED CSP card): "💵 FV: DCF $257
(+973% vs spot) · analyst PT $25 (+5%, n=9)" — those are AT&T's (T) values,
not MSFT's (real: DCF $290 / PT $538, n=19). Root cause: ticker `T` matched
the capital T of "Triggers"/"Time" because `first_known_ticker`'s boundary
lookarounds were uppercase-only, AND the "Triggers:"/"Rationale:" continuation
lines were treated as annotatable rec headers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import intrinsic_value as iv  # noqa: E402
from analysis import rsi_discipline  # noqa: E402


ETFS = iv.default_etf_set()

FV = {
    "MSFT": {"dcf": 289.6, "analyst_target": 537.7, "num_analysts": 19},
    "CIFR": {"dcf": 257.0, "analyst_target": 25.2, "num_analysts": 9},
    "T": {"dcf": 257.0, "analyst_target": 25.2, "num_analysts": 9},
}
SPOT = {"MSFT": 450.21, "CIFR": 24.0, "T": 24.0}
KNOWN = ["MSFT", "CIFR", "T", "NFLX"]

MSFT_CARD = [
    "### 💎 8. LONG DATED CSP · `MSFT`",
    "",
    "**Trade:** SELL 1× MSFT $410P exp Fri Oct 16 '26 (78 DTE)",
    "",
    "- **Triggers:** ⏸ Deferred (capacity gated); ✅ RSI favourable · RSI 50; "
    "⚠ LT verdict `downtrend` (-9.8% vs 200-SMA); third-party BUY",
    "- **Rationale:** Patient capital trade: elevated IV 63. Time decay is slow.",
    "- **Yield/Cost:** premium $930 (mid $9.30) · _Source: Live E*TRADE chain_",
    "- **Source:** recommendation-list-fetcher + yfinance IV",
]


def _annotate(lines):
    out, stats = iv.annotate_intrinsic(
        lines, fv_by_ticker=FV, spot_by_ticker=SPOT,
        known_tickers=KNOWN, etf_set=ETFS,
    )
    return out, stats


def test_fv_never_attaches_wrong_ticker():
    out, _ = _annotate(MSFT_CARD)
    joined = "\n".join(out)
    # The wrong-ticker values (CIFR / T: DCF $257 / PT $25) must be impossible
    # on an MSFT card.
    assert "DCF $257" not in joined
    assert "PT $25 " not in joined and "PT $25(" not in joined
    # MSFT's own values attach to the trade-ticket line.
    trade = [l for l in out if l.startswith("**Trade:**")][0]
    assert "DCF $290" in trade
    assert "PT $538" in trade and "n=19" in trade


def test_fv_continuation_lines_inherit_parent_ticker_or_nothing():
    out, _ = _annotate(MSFT_CARD)
    # Continuation/sub-lines get NO annotation of their own, period.
    for label in ("- **Triggers:**", "- **Rationale:**", "- **Yield/Cost:**",
                  "- **Source:**"):
        line = [l for l in out if l.startswith(label)][0]
        assert "FV:" not in line, f"FV leaked onto {label} line: {line}"
    # A Trade line with no inline ticker inherits the parent card's ticker.
    card = [
        "### 💎 9. LONG DATED CSP · `MSFT`",
        "",
        "**Trade:** SELL 1× $410P exp Fri Oct 16 '26 (78 DTE)",
    ]
    out2, _ = _annotate(card)
    trade = [l for l in out2 if l.startswith("**Trade:**")][0]
    assert "DCF $290" in trade  # MSFT's value, inherited from the ### header


def test_capital_t_words_do_not_match_ticker_t():
    """`first_known_ticker` boundary is case-insensitive: 'Triggers'/'Time'
    must not resolve single-letter ticker T (the 2026-07-30 root cause)."""
    tickers = sorted(KNOWN, key=len, reverse=True)
    assert rsi_discipline.first_known_ticker("- **Triggers:** RSI 50", tickers) is None
    assert rsi_discipline.first_known_ticker("Time decay is slow on LEAPs", tickers) is None
    # A genuine standalone T mention still resolves.
    assert rsi_discipline.first_known_ticker("SELL 1× T $24P", tickers) == "T"
    # Option-contract tokens still resolve the underlying.
    assert rsi_discipline.first_known_ticker("CLOSE MSFT_PUT_410_20261016", tickers) == "MSFT"
