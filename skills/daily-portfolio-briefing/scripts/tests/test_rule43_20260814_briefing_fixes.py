"""Rule #43 regression tests — five defects observed on the REAL 2026-08-14
briefing (briefing_2026-08-14.md + briefing_full_2026-08-14.md). Each test
docstring quotes the observed output verbatim.

BUG A — fictional coverage-after: Money Plan rendered
    "**Net option cash today (mid-fills):** −$7,100 buybacks = −$7,100 ·
     **Coverage after:** 0.11× → ~0.38×"
by treating freed put collateral as NEW CASH. The book is margin-secured —
cash did NOT drop $123K when SNDK $1230P opened, so it cannot rise $123K
when it closes. Honest post-close coverage ≈ 0.13×.

BUG B — Money Plan vs Total Impact disagreed AGAIN:
    "**Bank today:** 4 close(s) → $+3,693 realized (SNDK $1230P, SMH $710C,
     NOK $11P, IREN $47P)" / "−$7,100 buybacks"        (💰 Money Plan)
    "**Net cash today (mid-fills):** −$5,320 buybacks = −$5,320"  (📋 Total
     Impact)
Difference = NOK's $1,780 BTC — but NOK's action today was
    "4. **HOLD — GTC AT 50%** NOK_PUT_11_20261218 — +30% captured"
A GTC is a resting order, not a fill: NOT banked, NOT a buyback.

BUG C — Best Setups / redeploy-path not position/concentration-aware:
    "- **B** (66) `SNDK` — SELL 1× $1230P exp 2026-09-18 (36 DTE)"
while (i) action #1 was "**CLOSE** SNDK_PUT_1230_20260918", (ii) Red Flag #8
had "SNDK at 11.0% of NLV … over the 8% Tier C cap", and (iii) the SNDK
close card said "**Redeploy path:** 3 A/B setups waiting (WDC B, CGNX B,
SNDK B)" — SNDK offered as the redeploy target for closing SNDK.

BUG D — S/R data artifact: the SNDK action card rendered
    "↳ S: $903 (200-SMA, fib, 1 touch) · $44.4 (52w low, 1 touch) · …"
a $44.4 "support" on a $1,625 stock — a post-spinoff/short-history OHLC
artifact surfaced as a level.

BUG E — attribution verification: "Interest / dividends (residual cash):
-$38,603" (was -$20,328 the prior day) and "Since last snapshot (2026-08-13
→ 2026-08-14): … premium +$4,053". VERDICT (traced on the real snapshots):
the +$4,053 is CORRECT — SNDK $1230P (premiumReceived $43.50 → +$4,350)
first appears in the 2026-08-14 snapshot, minus the RDDT $140P buyback
inferred at its prior mark (−$297); the Since-Yesterday panel itself shows
"🆕 SNDK_PUT_1230_20260918 — new short PUT opened at the broker". The
residual jump is the one-time re-basing from the endpoint-based attribution
(old code, rendered 2026-08-13) to the chain-summed rebuild (shipped
2026-08-13, first rendered 2026-08-14): recomputing as-of 2026-08-13 with
the chained method already gives −$38,366, and −38,603 − (−38,366) = −236 =
today's daily link — the chain is self-consistent. Fix: a derivation note
renders under a large residual so the number is self-explaining.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.net_option_cash import (  # noqa: E402
    compute_net_option_cash,
    gtc_hold_idents,
)
from analysis.redeploy_path import (  # noqa: E402
    ab_setups,
    coverage_after_components,
    redeployment_path,
)
from analysis.setup_grade import (  # noqa: E402
    closing_today_from_action_lines,
    collect_best_setups,
    render_best_setups,
)
from render.money_plan import build_money_plan  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# Real-briefing fixtures (numbers from the 2026-08-14 snapshot/reviews)
# ─────────────────────────────────────────────────────────────────────────

def _action_lines_20260814():
    """The four composed action blocks from the real 2026-08-14 briefing."""
    return [
        "## Today's Action List — Friday, August 14, 2026",
        "",
        "1. **CLOSE** SNDK_PUT_1230_20260918 — +33% ($+1,415); "
        "buy-to-close limit $30.82",
        "   - **Why:** 33% of max profit already captured",
        "2. **CLOSE** SMH_CALL_710_20261120 — +39% ($+790); "
        "buy-to-close limit $12.97",
        "   - **Why:** 39% of max profit already captured",
        "3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +38% captured "
        "($+714); buy-to-close at mid $11.50 before IREN prints in 13d",
        "   - **Why:** the GTC-at-50% squeeze requires earnings > 30d away",
        "4. **HOLD — GTC AT 50%** NOK_PUT_11_20261218 — +30% captured; "
        "place a GTC buy-to-close at the 50%-capture price $1.28",
        "   - **Why:** the high-extrinsic exit is expensive today",
        "",
    ]


def _reviews_20260814():
    return [
        {"contract": "SNDK_PUT_1230_20260918", "underlying": "SNDK",
         "type": "PUT", "strike": 1230.0, "qty": -1,
         "entry_price": 43.50, "current_mid": 29.35},
        {"contract": "SMH_CALL_710_20261120", "underlying": "SMH",
         "type": "CALL", "strike": 710.0, "qty": -1,
         "entry_price": 20.25, "current_mid": 12.35},
        {"contract": "IREN_PUT_47_20261218", "underlying": "IREN",
         "type": "PUT", "strike": 47.0, "qty": -1,
         "entry_price": 18.64, "current_mid": 11.50},
        {"contract": "NOK_PUT_11_20261218", "underlying": "NOK",
         "type": "PUT", "strike": 11.0, "qty": -10,
         "entry_price": 2.56, "current_mid": 1.78},
    ]


def _playbook_20260814():
    """Playbook sweep including NOK — which the action list resolved to
    HOLD — GTC AT 50% at render time (the sweep can't know that)."""
    return {
        "closes": [
            {"contract": "NOK_PUT_11_20261218", "ticker": "NOK",
             "strike": 11.0, "qty": -10, "buy_to_close_mid": 1.78,
             "realized_profit": 780.0},
            {"contract": "IREN_PUT_47_20261218", "ticker": "IREN",
             "strike": 47.0, "qty": -1, "buy_to_close_mid": 11.50,
             "realized_profit": 714.0},
        ],
        "opens": [],
        "coverage_before": 0.11,
        "coverage_after": 0.13,
    }


# ─────────────────────────────────────────────────────────────────────────
# BUG A — honest coverage-after
# ─────────────────────────────────────────────────────────────────────────

def test_bug_a_honest_coverage_after_on_the_real_sndk_numbers():
    """Observed: "**Coverage after:** 0.11× → ~0.38×". Real numbers: cash
    $77,348, obligation $705,700, playbook closes freeing ~$138,700 with
    ~$5,865 of buybacks. The fictional (cash + freed)/(obl − freed) gives
    ~0.38×; the honest (cash − buybacks)/(obl − freed) gives ~0.13× —
    obligation shrinks; cash does not rise."""
    cash, obl = 77_348.0, 705_700.0
    freed, btc = 138_700.0, 5_865.0
    honest = coverage_after_components(cash, obl, freed=freed, btc_cost=btc)
    assert honest == pytest.approx(0.126, abs=0.005)
    assert honest < 0.20
    # The fictional formula the briefing rendered — must NOT be what the
    # single source of truth computes.
    fiction = (cash + freed) / (obl - freed)
    assert fiction == pytest.approx(0.38, abs=0.01)
    assert honest != pytest.approx(fiction, abs=0.05)


def test_bug_a_sndk_single_close_honest_projection():
    """The SNDK-only close (freed $123,000, BTC $2,935): honest post-close
    coverage = (77,348 − 2,935)/(705,700 − 123,000) ≈ 0.13× — never the
    Fable-quoted "pushes coverage from 0.11× toward 0.38×"."""
    honest = coverage_after_components(
        77_348.0, 705_700.0, freed=123_000.0, btc_cost=2_935.0)
    assert honest == pytest.approx(0.1277, abs=0.002)


def test_bug_a_coverage_after_components_edge_cases():
    """inf when the close retires the whole obligation; None on garbage —
    fail-open, never fabricated (rule #19)."""
    import math
    assert math.isinf(coverage_after_components(
        50_000.0, 100_000.0, freed=100_000.0, btc_cost=1_000.0))
    assert coverage_after_components(None, 100_000.0) is None
    assert coverage_after_components(50_000.0, float("nan")) is None
    # New-open premium credits cash; deployed obligation adds to the book.
    v = coverage_after_components(
        100_000.0, 1_000_000.0, freed=300_000.0, btc_cost=1_000.0,
        new_obligation=15_000.0, new_premium=240.0)
    assert v == pytest.approx(99_240 / 715_000, abs=1e-4)


def test_bug_a_money_plan_small_gain_note_obligation_shrinks():
    """When the honest improvement is small, the Money Plan line says WHY:
    "coverage moves 0.11× → 0.13× — obligation shrinks; cash does not"."""
    lines, plan = build_money_plan(
        date_str="2026-08-14",
        action_list_lines=_action_lines_20260814(),
        options_reviews=_reviews_20260814(),
        new_ideas=[],
        playbook=_playbook_20260814(),
        analytics={"stress_coverage": {"coverage_ratio": 0.11}},
        snapshot_data={"positions": []},
        config={},
    )
    text = "\n".join(lines)
    assert "**Coverage after:** 0.11× → ~0.13×" in text
    assert "obligation shrinks; cash does not" in text


def test_bug_a_money_plan_large_gain_no_note():
    """A material coverage gain (≥ 0.10×) renders without the small-gain
    physics note — the note is for the 0.11×→0.13× class of move."""
    pb = _playbook_20260814()
    pb["coverage_after"] = 0.45
    lines, _ = build_money_plan(
        date_str="2026-08-14",
        action_list_lines=_action_lines_20260814(),
        options_reviews=_reviews_20260814(),
        new_ideas=[],
        playbook=pb,
        analytics={"stress_coverage": {"coverage_ratio": 0.11}},
        snapshot_data={"positions": []},
        config={},
    )
    text = "\n".join(lines)
    assert "**Coverage after:** 0.11× → ~0.45×" in text
    assert "obligation shrinks; cash does not" not in text


# ─────────────────────────────────────────────────────────────────────────
# BUG B — GTC-hold items are neither banked nor buybacks; one voice
# ─────────────────────────────────────────────────────────────────────────

def test_bug_b_gtc_hold_ident_classification():
    """"4. **HOLD — GTC AT 50%** NOK_PUT_11_20261218" parses as a hold —
    never a close."""
    holds = gtc_hold_idents(_action_lines_20260814())
    assert holds == {"NOK_PUT_11_20261218"}


def test_bug_b_total_impact_and_money_plan_byte_agree():
    """Observed: Money Plan "−$7,100 buybacks = −$7,100" vs Total Impact
    "−$5,320 buybacks = −$5,320" — difference exactly NOK's $1,780 BTC
    (mid $1.78 × 100 × 10). With the GTC-hold excluded from the playbook
    fold-in, both surfaces compute −$5,320 and the composition strings are
    byte-identical."""
    noc_no_pb = compute_net_option_cash(
        _action_lines_20260814(), options_reviews=_reviews_20260814())
    noc_with_pb = compute_net_option_cash(
        _action_lines_20260814(), options_reviews=_reviews_20260814(),
        playbook=_playbook_20260814())
    # SNDK 2,935 + SMH 1,235 + IREN 1,150 = 5,320 — NOK's 1,780 excluded.
    assert noc_no_pb["net_cash"] == pytest.approx(-5320.0)
    assert noc_with_pb["net_cash"] == pytest.approx(-5320.0)
    assert noc_with_pb["composition"] == noc_no_pb["composition"]
    assert "−$5,320" in noc_with_pb["composition"]
    labels = [c["label"] for c in noc_with_pb["components"]]
    assert not any("NOK" in lb for lb in labels)


def test_bug_b_money_plan_bank_today_excludes_gtc_hold():
    """Observed: "**Bank today:** 4 close(s) → $+3,693 realized (SNDK
    $1230P, SMH $710C, NOK $11P, IREN $47P)". NOK's action today is HOLD —
    GTC (a resting order, not a fill): the bank line must count 3 closes
    (SNDK +1,415, SMH +790, IREN +714 = $+2,919) and never list NOK."""
    lines, plan = build_money_plan(
        date_str="2026-08-14",
        action_list_lines=_action_lines_20260814(),
        options_reviews=_reviews_20260814(),
        new_ideas=[],
        playbook=_playbook_20260814(),
        analytics={"stress_coverage": {"coverage_ratio": 0.11}},
        snapshot_data={"positions": []},
        config={},
    )
    text = "\n".join(lines)
    assert "Bank today:** 3 close(s) → $+2,919 realized" in text
    assert "NOK" not in text
    assert len(plan["banks"]) == 3
    # And the Money Plan's net-cash matches Total Impact's — one voice.
    assert plan["net_cash"] == pytest.approx(-5320.0)


# ─────────────────────────────────────────────────────────────────────────
# BUG C — Best Setups / redeploy pool position + concentration awareness
# ─────────────────────────────────────────────────────────────────────────

def _snapshot_20260814():
    """Snapshot with the held SNDK $1230P (the $123,000 obligation from
    Red Flag #8) and the real NLV."""
    return {
        "positions": [
            {"symbol": "SNDK_PUT_1230_20260918", "assetType": "OPTION",
             "type": "PUT", "underlying": "SNDK", "strike": 1230.0,
             "qty": -1, "currentMid": 29.35},
        ],
        "balance": {"accountValue": 1_114_954.0, "cash": 77_348.0},
    }


def _spotlight_ideas():
    """Graded income-opportunity candidates: SNDK duplicates the HELD put;
    WDC is a clean name."""
    return [
        {"ticker": "SNDK", "setup_grade": "B", "setup_grade_score": 66,
         "setup_grade_message": "B — good setup", "strike": 1230.0,
         "expiration_pretty": "2026-09-18", "annualized_pct": 35.0,
         "rsi_14": 48.0},
        {"ticker": "WDC", "setup_grade": "B", "setup_grade_score": 73,
         "setup_grade_message": "B — good setup", "strike": 440.0,
         "expiration_pretty": "Fri Oct 16 '26", "annualized_pct": 43.0,
         "rsi_14": 48.0},
    ]


def test_bug_c_spotlight_excludes_held_put_with_visible_reason():
    """Observed: "- **B** (66) `SNDK` — SELL 1× $1230P exp 2026-09-18 (36
    DTE)" spotlit while action #1 was "**CLOSE** SNDK_PUT_1230_20260918"
    and the user HELD that exact put. The spotlight must exclude SNDK from
    the CSP pool and render one visible ⏸ line with the reasons
    (rule #24)."""
    best = collect_best_setups(
        new_ideas=_spotlight_ideas(),
        snapshot_data=_snapshot_20260814(),
        closing_today={"SNDK_PUT_1230_20260918"},
        config={})
    tickers = [e["ticker"] for e in best["csp"]]
    assert "SNDK" not in tickers
    assert "WDC" in tickers
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex
    assert "you hold this put ($1230P held)" in ex["SNDK"]["reason"]
    assert "the action list closes this contract today" in ex["SNDK"]["reason"]
    md = "\n".join(render_best_setups(best))
    assert "⏸ SNDK B (66) — excluded:" in md
    assert "you hold this put" in md


def test_bug_c_spotlight_excludes_over_cap_name():
    """Red Flag #8 (observed): "SNDK at 11.0% of NLV (equity 0.0% +
    $123,000 short-put obligation) — over the 8% Tier C cap". A candidate
    at a NON-overlapping strike on the same over-cap name is still
    excluded, with the measured cap reason."""
    ideas = [{"ticker": "SNDK", "setup_grade": "B", "setup_grade_score": 66,
              "setup_grade_message": "B — good setup", "strike": 1000.0,
              "expiration_pretty": "2026-09-18", "annualized_pct": 35.0,
              "rsi_14": 48.0}]
    best = collect_best_setups(
        new_ideas=ideas, snapshot_data=_snapshot_20260814(), config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = best["excluded_csp"]
    assert len(ex) == 1 and ex[0]["ticker"] == "SNDK"
    assert "over the 8% cap" in ex[0]["reason"]
    # Not a strike overlap ($1000 vs held $1230 is 18.7% apart).
    assert "you hold this put" not in ex[0]["reason"]


def test_bug_c_clean_names_unaffected():
    """A name with no holdings, under its cap, not closing today, keeps its
    spotlight slot — the exclusions never over-fire."""
    ideas = [{"ticker": "WDC", "setup_grade": "B", "setup_grade_score": 73,
              "setup_grade_message": "B — good setup", "strike": 440.0,
              "expiration_pretty": "Fri Oct 16 '26", "annualized_pct": 43.0,
              "rsi_14": 48.0}]
    best = collect_best_setups(
        new_ideas=ideas, snapshot_data=_snapshot_20260814(), config={})
    assert [e["ticker"] for e in best["csp"]] == ["WDC"]
    assert best["excluded_csp"] == []


def test_bug_c_redeploy_path_never_offers_the_closing_contract():
    """Observed on the SNDK close card: "**Redeploy path:** 3 A/B setups
    waiting (WDC B, CGNX B, SNDK B)" — SNDK offered as the redeploy target
    for closing SNDK. With the position-aware exclusions in the shared
    collect_best_setups pool, the A/B pool (and the rendered reason) can
    never contain the held/over-cap name."""
    best = collect_best_setups(
        new_ideas=_spotlight_ideas(),
        snapshot_data=_snapshot_20260814(),
        closing_today={"SNDK_PUT_1230_20260918"},
        config={})
    pool = ab_setups(best)
    assert all(e["ticker"] != "SNDK" for e in pool)
    analytics = {"stress_coverage": {
        "coverage_ratio": 0.11, "cash": 77_348.0,
        "total_put_obligations": 705_700.0}}
    close_impact = {"side": "put", "freed_collateral": 123_000.0,
                    "btc_cost": 2_935.0}
    ok, reason = redeployment_path(
        analytics, best, close_impact,
        {"redeploy_aware_tp": {"enabled": True}})
    assert ok
    assert "SNDK" not in reason
    assert "WDC" in reason


def test_bug_c_closing_today_parser():
    """Contract idents come from the composed action list; HOLD — GTC items
    are NOT closes."""
    idents = closing_today_from_action_lines(_action_lines_20260814())
    assert "SNDK_PUT_1230_20260918" in idents
    assert "IREN_PUT_47_20261218" in idents
    assert "NOK_PUT_11_20261218" not in idents


# ─────────────────────────────────────────────────────────────────────────
# BUG D — S/R far-extreme artifact filter
# ─────────────────────────────────────────────────────────────────────────

def _lvl(price, side, source, touches=1, confluence=None, strength=2.0):
    from analysis.support_resistance import Level
    return Level(price=price, side=side, source=source, touches=touches,
                 strength=strength, confluence=list(confluence or []))


def test_bug_d_sndk_44_dollar_52w_low_is_filtered():
    """Observed: "S: … $44.4 (52w low, 1 touch)" on a $1,625 stock — a
    post-spinoff/short-history OHLC artifact. A single-touch 52w-extreme-
    only support below 50% of spot (with only self-confluence) is
    dropped."""
    from analysis.support_resistance import (
        DEFAULT_CONFIG,
        SRC_LOW_52W,
        SUPPORT,
        _is_far_extreme_artifact,
    )
    lv = _lvl(44.4, SUPPORT, SRC_LOW_52W, touches=1,
              confluence=[SRC_LOW_52W])
    assert _is_far_extreme_artifact(lv, 1625.50, DEFAULT_CONFIG) is True


def test_bug_d_genuine_levels_never_dropped():
    """Fail-open: multi-touch levels, confluent levels, near-spot 52w
    extremes, and non-extreme sources always survive — including the SNDK
    card's real levels ($903 fib/200-SMA support, $2,335 52w high at 1.44×
    spot)."""
    from analysis.support_resistance import (
        DEFAULT_CONFIG,
        RESISTANCE,
        SRC_HIGH_52W,
        SRC_LOW_52W,
        SRC_SMA200,
        SRC_SWING,
        SUPPORT,
        _is_far_extreme_artifact,
    )
    spot = 1625.50
    cfg = DEFAULT_CONFIG
    # Multi-touch 52w low far below spot → kept (never drop multi-touch).
    assert not _is_far_extreme_artifact(
        _lvl(44.4, SUPPORT, SRC_LOW_52W, touches=3), spot, cfg)
    # Genuine confluence (200-SMA) → kept even at 1 touch.
    assert not _is_far_extreme_artifact(
        _lvl(44.4, SUPPORT, SRC_LOW_52W, touches=1,
             confluence=[SRC_LOW_52W, SRC_SMA200]), spot, cfg)
    # 52w low at 60% of spot → inside the sanity band, kept.
    assert not _is_far_extreme_artifact(
        _lvl(975.0, SUPPORT, SRC_LOW_52W, touches=1), spot, cfg)
    # The observed $2,335 52w-high resistance (1.44× spot < 2×) → kept.
    assert not _is_far_extreme_artifact(
        _lvl(2335.0, RESISTANCE, SRC_HIGH_52W, touches=1), spot, cfg)
    # A far single-touch SWING support is not a 52w-extreme → kept.
    assert not _is_far_extreme_artifact(
        _lvl(44.4, SUPPORT, SRC_SWING, touches=1), spot, cfg)
    # A 52w-high resistance beyond 2× spot IS an artifact.
    assert _is_far_extreme_artifact(
        _lvl(3500.0, RESISTANCE, SRC_HIGH_52W, touches=1), spot, cfg)


def test_bug_d_compute_sr_end_to_end_filters_the_artifact():
    """End-to-end: a short post-spinoff history with one absurd early bar
    ($44 print) on a ~$1,600 stock never yields a sub-$800 support; normal
    swing/SMA levels still render."""
    import numpy as np
    import pandas as pd

    from analysis.support_resistance import compute_sr

    n = 220
    idx = pd.bdate_range(end="2026-08-14", periods=n)
    rng = np.random.default_rng(7)
    closes = np.linspace(1400.0, 1625.0, n) + rng.normal(0, 8, n)
    closes[0] = 44.4          # the artifact print
    highs = closes + 12
    lows = closes - 12
    lows[0] = 44.0
    hist = pd.DataFrame({"High": highs, "Low": lows, "Close": closes},
                        index=idx)
    sr = compute_sr(hist, spot=1625.50, sma_200=1500.0)
    for lv in sr.supports:
        assert lv.price > 800.0, f"artifact support survived: {lv.label()}"


# ─────────────────────────────────────────────────────────────────────────
# BUG E — attribution verified correct; derivation note renders
# ─────────────────────────────────────────────────────────────────────────

def test_bug_e_premium_4053_is_the_sndk_open_minus_rddt_buyback():
    """Observed: "Since last snapshot (2026-08-13 → 2026-08-14): … premium
    +$4,053" on a window the bug report claimed had "NO new opens". Traced
    on the real snapshots: SNDK_PUT_1230_20260918 (premiumReceived $43.50)
    first appears in the 2026-08-14 positions (+$4,350) and
    RDDT_PUT_140_20260911 disappears (buyback inferred at its prior mark
    $2.97 → −$297): 4,350 − 297 = +$4,053. The attribution is CORRECT —
    this fixture reproduces the real window shape and pins the math."""
    from analysis.pnl_attribution import compute_attribution

    prior = {
        "date": "2026-08-13",
        "balance": {"accountValue": 1_115_764.46, "cash": 76_103.84},
        "positions": [
            {"symbol": "RDDT_PUT_140_20260911", "assetType": "OPTION",
             "type": "PUT", "underlying": "RDDT", "strike": 140.0,
             "qty": -1, "premiumReceived": 4.91, "currentMid": 2.97,
             "marketValue": -297.0, "expiration": "2026-09-11"},
        ],
    }
    cur = {
        "date": "2026-08-14",
        "balance": {"accountValue": 1_114_953.78, "cash": 77_348.33},
        "positions": [
            {"symbol": "SNDK_PUT_1230_20260918", "assetType": "OPTION",
             "type": "PUT", "underlying": "SNDK", "strike": 1230.0,
             "qty": -1, "premiumReceived": 43.50, "currentMid": 29.35,
             "marketValue": -2935.0, "expiration": "2026-09-18"},
        ],
    }
    pa = compute_attribution(cur, prior, name="daily")
    assert pa.buckets["option_premium_net"] == pytest.approx(4053.0)


def test_bug_e_chained_attribution_carries_n_windows():
    """compute_chained_attribution records how many snapshot-to-snapshot
    windows were summed, so the renderer can derive the residual
    honestly."""
    from analysis.pnl_attribution import compute_chained_attribution

    snaps = [
        {"date": f"2026-08-{d:02d}",
         "balance": {"accountValue": 1_000_000.0 + d, "cash": 50_000.0},
         "positions": []}
        for d in (11, 12, 13, 14)
    ]
    pa = compute_chained_attribution(snaps, name="since_inception")
    assert pa.n_windows == 3
    assert pa.to_dict()["n_windows"] == 3


def test_bug_e_large_residual_renders_derivation_note():
    """Observed: "Interest / dividends (residual cash): -$38,603" jumped
    ~$18K in one day with no explanation. A residual this large must be
    self-explaining: the renderer appends a derivation note (per-window
    cash residual, chain-summed across N windows, includes estimate
    error)."""
    from render.benchmark_panel import _render_attribution

    attribution = {
        "status": "ok",
        "periods": [{
            "name": "since_inception",
            "start_date": "2026-05-09", "end_date": "2026-08-14",
            "nlv_start": 1_000_000.0, "nlv_end": 1_114_953.78,
            "nlv_change": 114_953.78,
            "buckets": {"interest_dividends": -38_603.0,
                        "option_premium_net": 41_261.0},
            "unattributed": -66.0, "cash_drag": {},
            "n_windows": 66, "notes": [], "prior_window": {},
        }],
    }
    lines: list = []
    _render_attribution(lines, attribution)
    text = "\n".join(lines)
    assert "Interest / dividends (residual cash): -$38,603" in text
    assert "derivation: per-window cash residual" in text
    assert "chain-summed across 66 snapshot windows" in text
    assert "not a broker statement line" in text


def test_bug_e_small_residual_no_note():
    """A modest residual (under max(0.5% NLV, $5K)) renders clean — the
    derivation note never becomes boilerplate."""
    from render.benchmark_panel import _render_attribution

    attribution = {
        "status": "ok",
        "periods": [{
            "name": "since_inception",
            "start_date": "2026-05-09", "end_date": "2026-08-14",
            "nlv_start": 1_000_000.0, "nlv_end": 1_010_000.0,
            "nlv_change": 10_000.0,
            "buckets": {"interest_dividends": -1_200.0},
            "unattributed": -10.0, "cash_drag": {},
            "n_windows": 66, "notes": [], "prior_window": {},
        }],
    }
    lines: list = []
    _render_attribution(lines, attribution)
    assert "derivation: per-window cash residual" not in "\n".join(lines)
