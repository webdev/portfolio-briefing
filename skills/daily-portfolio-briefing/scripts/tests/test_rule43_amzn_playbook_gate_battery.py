"""Rule #43 regression tests — 2026-08-03 Rotation Playbook Phase 2 row:

    AMZN $240P Sep 04 '26 | +$77 | $24,000 | 4% | ⭐⭐⭐ · Parkev BUY High ·
    3d · IV rank 100 · 🔓 unlocks after Phase 1

selected while AMZN's LIVE RSI ≈ 80 (the snapshot RSI 66 was pre-gap — the
same briefing's vintage-guard footer listed "AMZN +5.5%"), the yield was 4%
annualized (below risk-free), and IV rank 100 was the post-gap realized-vol
artifact. The fix is a final `_phase2_gate_battery` applied to every
candidate before selection: (1) live-RSI enforcement (vintage check +
standard hook, fail-safe exclude on unverifiable up-moves), (2) a yield
floor (`playbook_min_annualized_yield` 12% / min premium 0.5% of
collateral), (3) IV-rank honesty (gap-inflated ranks lose the "IV rank N"
token and the +1 conviction bonus). All exclusions render in the playbook's
warnings footer with reasons (rule #24).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.rotation_playbook import compute_playbook  # noqa: E402
from render.rotation_playbook_panel import render_rotation_playbook  # noqa: E402

TODAY = date(2026, 8, 3)


# ── Fixture builders ──────────────────────────────────────────────────────


def _held(ticker="GOOG", strike=325.0, exp="2026-08-21", qty=-1,
          entry=7.9447, mid=4.45, dte=18):
    return {"underlying": ticker, "type": "PUT", "strike": strike,
            "expiration": exp, "qty": qty, "entry_price": entry,
            "current_mid": mid, "days_to_expiry": dte}


def _sweeper():
    """Frees $67.5K — enough to fund every test candidate."""
    return [
        _held(),
        _held(ticker="MSFT", strike=350, exp="2026-09-18",
              entry=23.4244, mid=9.525, dte=46),
    ]


def _cand(ticker="AMZN", strike=240.0, exp="2026-09-04", dte=32,
          premium=0.77, **kw):
    row = {"kind": "SCOUT_CSP", "ticker": ticker, "strike": strike,
           "expiration": exp, "dte": dte, "premium": premium}
    row.update(kw)
    return row


def _rec(tier=3, conviction="High", age=3):
    return {"rating_tier": tier, "conviction": conviction,
            "age_days": age, "recommendation": "BUY"}


def _an(cands, quotes=None, technicals=None):
    """Analytics with a clean (post-expiry) earnings entry per candidate so
    the RDDT unknown-earnings penalty stays out of these scores."""
    sd = {"balance": {"accountValue": 1_000_000.0, "cash": 300_000.0}}
    if quotes:
        sd["quotes"] = quotes
    if technicals:
        sd["technicals"] = technicals
    cal = {c["ticker"]: "2027-06-30" for c in cands}
    return {"nlv": 1_000_000.0, "snapshot_data": sd,
            "technicals": technicals or {}, "earnings_calendar": cal}


def _amzn_row():
    """The observed AMZN row: $240P Sep 04 '26, $77 premium on $24,000
    collateral (4% ann), snapshot RSI 66, IV rank 100, spot +5.5% since the
    technicals close (6-entry close series — live RSI not computable)."""
    cands = [_cand(rsi_14=66.0, iv_rank=100.0)]
    an = _an(cands,
             quotes={"AMZN": {"lastTrade": 239.00}},
             technicals={"AMZN": {"spot": 226.50, "recent_closes":
                                  [232.11, 231.39, 230.86, 226.65,
                                   235.50, 226.50]}})
    return cands, an


def _pb(cands, an, recs=None, config=None):
    return compute_playbook(_sweeper(), cands,
                            recs or {c["ticker"]: _rec() for c in cands},
                            set(), an, config or {}, today=TODAY)


# ── Fix 1 — live-RSI enforcement ──────────────────────────────────────────


def test_amzn_shape_excluded_stale_upmove_rsi():
    """Observed: 'AMZN $240P Sep 04 '26 | +$77 | $24,000 | 4% | ⭐⭐⭐ ·
    Parkev BUY High · 3d · IV rank 100 · 🔓 unlocks after Phase 1' selected
    while the vintage-guard footer listed 'AMZN +5.5%'. A >5% UP-move on a
    pre-gap snapshot RSI (66, ≤70) with no computable live RSI must exclude
    the candidate from Phase 2 selection (fail-safe: live RSI plausibly
    >70), keeping the reason visible in the footer (rule #24)."""
    cands, an = _amzn_row()
    pb = _pb(cands, an)
    assert pb is not None
    assert "AMZN" not in [o.ticker for o in pb.opens]
    line = next(w for w in pb.warnings if w.startswith("⛔ AMZN $240P"))
    assert "stale RSI" in line
    assert "+5.5%" in line
    assert "pre-gap" in line
    assert "plausibly >70" in line


def test_live_rsi_computed_and_hard_blocks_over_70():
    """When the close series allows, the live RSI IS computed (Wilder's on
    the daily series + today's live price as the current bar) and the
    standard hook applies: live RSI 100 (all-gains series, spot +5.8%)
    hard-blocks the new put-sale even though the snapshot RSI (48) looked
    favourable — the AMZN failure mode with enough data to prove it."""
    closes = [212.0 + i for i in range(15)]      # 15 rising closes → 226
    cands = [_cand(premium=4.00, rsi_14=48.0)]   # 19% ann — clears the floor
    an = _an(cands,
             quotes={"AMZN": {"lastTrade": 239.00}},
             technicals={"AMZN": {"spot": 226.0, "recent_closes": closes}})
    pb = _pb(cands, an)
    assert "AMZN" not in [o.ticker for o in pb.opens]
    line = next(w for w in pb.warnings if w.startswith("⛔ AMZN $240P"))
    assert "RSI 100 overbought" in line
    assert "hard block" in line


def test_extended_band_60_70_excluded_from_playbook():
    """The playbook opens only from the ≤60 bands: a candidate at RSI 66
    (the AMZN snapshot value, trusted — no intraday move) is excluded as
    extended-wait; RSI 55 on the identical shape stays selectable."""
    cands = [_cand(premium=4.00, rsi_14=66.0)]   # yield clears the floor
    pb = _pb(cands, _an(cands))
    assert "AMZN" not in [o.ticker for o in pb.opens]
    line = next(w for w in pb.warnings if w.startswith("⛔ AMZN $240P"))
    assert "RSI 66 extended (60-70)" in line
    assert "≤60 bands" in line
    # Same shape at RSI 55 → selected (band is [60, 70)).
    ok = [_cand(premium=4.00, rsi_14=55.0)]
    pb2 = _pb(ok, _an(ok))
    assert [o.ticker for o in pb2.opens] == ["AMZN"]


# ── Fix 2 — yield floor ───────────────────────────────────────────────────


def test_yield_floor_excludes_4pct():
    """'+$77 | $24,000 | 4%' — 4% annualized is below risk-free; the floor
    (playbook_min_annualized_yield 0.12, min premium 0.5% of collateral)
    excludes it with 'yield floor: 4% ann < 12% — premium doesn't pay for
    the risk' in the footer. Config lowering BOTH floors re-admits it."""
    cands = [_cand()]                             # $77 / $24,000 / 32d ≈ 4%
    pb = _pb(cands, _an(cands))
    assert "AMZN" not in [o.ticker for o in pb.opens]
    line = next(w for w in pb.warnings if w.startswith("⛔ AMZN $240P"))
    assert "yield floor: 4% ann < 12% — premium doesn't pay for the risk" \
        in line
    # 0.77/240 = 0.32% of collateral — the companion floor fires too.
    assert "0.32% of collateral" in line
    # Config override (both floors) re-admits the same shape.
    pb2 = _pb(cands, _an(cands),
              config={"rotation_playbook": {
                  "playbook_min_annualized_yield": 0.02,
                  "playbook_min_premium_pct_of_collateral": 0.001}})
    assert [o.ticker for o in pb2.opens] == ["AMZN"]


def test_no_stars_below_yield_floor():
    """'⭐⭐⭐ · Parkev BUY High · 3d · IV rank 100' rendered on a 4%-ann row —
    ⭐ conviction may never render on a candidate that fails the floor. The
    excluded AMZN appears ONLY in the warnings footer, with no stars and no
    Sell-to-Open row."""
    cands, an = _amzn_row()
    pb = _pb(cands, an)
    md = "\n".join(render_rotation_playbook(pb))
    amzn_lines = [ln for ln in md.splitlines() if "AMZN" in ln]
    assert amzn_lines, "exclusion must stay visible (rule #24)"
    assert all("⭐" not in ln for ln in amzn_lines)
    assert not any("Sell-to-Open" in ln for ln in amzn_lines)


# ── Fix 3 — IV-rank honesty in the conviction column ──────────────────────


def test_iv_rank_bonus_revoked_when_gap_inflated():
    """'IV rank 100' in the conviction column on a 4%-ann candidate is the
    post-gap realized-vol artifact. Claimed rank ≥60 + delivered yield <20%
    ann drops the 'IV rank N' token, appends '⚠ IV rank gap-inflated', and
    revokes the +1 IV-rank conviction bonus (re-score). A genuinely fat
    delivery (≥20% ann) keeps token and bonus."""
    thin = [_cand(ticker="CRM", strike=100, exp="2026-09-02", dte=30,
                  premium=1.20, iv_rank=100.0, rsi_14=45.0)]   # 14.6% ann
    pb = _pb(thin, _an(thin))
    (o,) = pb.opens
    # 3 × High(3) × fresh(2.5) = 22.5, +2 pullback, +1 IV granted then
    # REVOKED = 24.5 (not 25.5).
    assert o.conviction_score == pytest.approx(24.5)
    assert "⚠ IV rank gap-inflated" in o.setup_flags
    assert "IV rank 100" not in o.setup_flags
    row = next(ln for ln in "\n".join(render_rotation_playbook(pb))
               .splitlines() if "CRM $100P" in ln and "Sell-to-Open" in ln)
    assert "⚠ IV rank gap-inflated" in row
    assert "IV rank 100" not in row
    # Control: 24.3% ann delivery is genuinely fat — token + bonus kept.
    fat = [_cand(ticker="CRM", strike=100, exp="2026-09-02", dte=30,
                 premium=2.00, iv_rank=100.0, rsi_14=45.0)]
    (o2,) = _pb(fat, _an(fat)).opens
    assert o2.conviction_score == pytest.approx(25.5)
    # One-voice vol (George 2026-08-12): the rich-vol flag now carries the
    # honest proxy label instead of the bare legacy token.
    assert "RVr 100 (realized-vol proxy)" in o2.setup_flags
    assert "⚠ IV rank gap-inflated" not in o2.setup_flags


# ── Regression guards ─────────────────────────────────────────────────────


def test_clean_candidate_unaffected():
    """A clean candidate (RSI 45, ~25% ann, live quote within the vintage
    threshold) passes the battery untouched — selected as before, score
    unchanged, no stale-RSI annotation, no footer exclusion."""
    cands = [_cand(ticker="CRM", strike=150, exp="2026-09-10", dte=38,
                   premium=3.90, rsi_14=45.0)]    # ≈25% ann
    an = _an(cands,
             quotes={"CRM": {"lastTrade": 151.0}},          # +0.7% — fresh
             technicals={"CRM": {"spot": 150.0, "recent_closes":
                                 [148.0, 149.2, 150.5, 149.8, 150.3,
                                  150.0]}})
    pb = _pb(cands, an)
    assert [o.ticker for o in pb.opens] == ["CRM"]
    (o,) = pb.opens
    assert o.conviction_score == pytest.approx(24.5)        # 22.5 + 2 pullback
    assert not any("stale RSI" in w for w in o.warnings)
    assert not any(w.startswith("⛔ CRM") for w in pb.warnings)


def test_exclusions_render_in_footer():
    """Rule #24 — the excluded AMZN row renders in the playbook's Warnings
    section with its reasons (yield floor + stale RSI), never silently
    dropped."""
    cands, an = _amzn_row()
    md = "\n".join(render_rotation_playbook(_pb(cands, an)))
    assert "**Warnings:**" in md
    assert "⛔ AMZN $240P excluded" in md
    assert "yield floor" in md
    assert "stale RSI" in md
