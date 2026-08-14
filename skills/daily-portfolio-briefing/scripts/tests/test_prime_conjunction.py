"""💎 Prime conjunction — George's card-strict entry tier (2026-08-14).

George (2026-08-14): "strengthen our algorithm by essentially validating
that, for CSP, our [RSI] between 35 and 45, [IV] rank is greater than 60,
support under strike — basically all the tight algorithm... should
absolutely get incorporated into the daily briefing, and we should
clearly see all of the good entries based on this algorithm."

The weighted Setup Grade permits tradeoffs (a B/A- can pass with IVr 45
when the other legs are perfect; SNDK graded B at RSI 48 late-band). The
prime conjunction is a strict AND — a TIER ON TOP of the grade, never a
re-weighting. These tests pin:

  (a) conjunction TRUE only when EVERY component is in its prime band —
      each single-miss case is FALSE with the measured missing entry
      (RSI 48; IVr 45 with everything else perfect; 1-touch support;
      RVr-proxy-only vol read);
  (b) the Best Setups panel renders the '💎 Prime entries' subsection
      FIRST — passers listed, or the closest-miss line from the top-scored
      candidate's measured missing list (never padded, rule #19);
  (c) the digest keeps the Prime subsection within its line budget;
  (d) EntryDecision.prime is annotation ONLY — it never flips a verdict
      (a non-prime B still ENTERs when everything else passes);
  (e) the 💎 PRIME badge renders next to the grade.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_algorithm as ea  # noqa: E402
from analysis import setup_grade as sg  # noqa: E402


CFG_ON = {"setup_grade": {"enabled": True}}
AS_OF = date(2026, 8, 14)

SR_2TOUCH_SUPPORT = {"supports": [
    {"price": 145.0, "touches": 3, "strength": 5.0}], "resistances": []}
SR_1TOUCH_SUPPORT = {"supports": [
    {"price": 145.0, "touches": 1, "strength": 5.0}], "resistances": []}
SR_2TOUCH_RESISTANCE = {"supports": [], "resistances": [
    {"price": 108.0, "touches": 2, "strength": 5.0}]}


def _csp_components(**kw):
    """Every component in its prime band: RSI 40 (35-45), TRUE chain IVr
    75 (≥ 60), 3-touch support UNDER the $150 strike, uptrend above the
    200-SMA, red day, earnings 30d clear."""
    base = {
        "rsi": 40.0, "iv_rank": 75.0, "iv_rank_source": "chain",
        "support_resistance": SR_2TOUCH_SUPPORT, "strike": 150.0,
        "spot": 160.0, "sma_200": 150.0, "lt_verdict": "uptrend",
        "day_change_pct": -0.01, "days_to_earnings": 30,
    }
    base.update(kw)
    return base


def _cc_components(**kw):
    """CC prime: RSI 72 (≥ 70), TRUE chain IVr 45 (≥ 40), tested 2-touch
    resistance at the $108 strike, uptrend, green day, earnings clear."""
    base = {
        "rsi": 72.0, "iv_rank": 45.0, "iv_rank_source": "chain",
        "support_resistance": SR_2TOUCH_RESISTANCE, "strike": 108.0,
        "spot": 100.0, "sma_200": 90.0, "lt_verdict": "uptrend",
        "day_change_pct": 0.01, "days_to_earnings": 30,
    }
    base.update(kw)
    return base


# ── (a) The conjunction — TRUE only when ALL prime ───────────────────────


def test_csp_all_prime_passes():
    """"for CSP, our [RSI] between 35 and 45, [IV] rank is greater than
    60, support under strike — basically all the tight algorithm"."""
    prime, missing = sg.prime_conjunction(_csp_components(), "csp", CFG_ON)
    assert prime is True
    assert missing == []


def test_cc_all_prime_passes():
    prime, missing = sg.prime_conjunction(_cc_components(), "cc", CFG_ON)
    assert prime is True
    assert missing == []


def test_rsi_48_single_miss_fails_with_measured_entry():
    """The SNDK-class case: RSI 48 late-band earns a weighted B, but the
    card says 35-45 — the conjunction fails with the measured value."""
    prime, missing = sg.prime_conjunction(
        _csp_components(rsi=48.0), "csp", CFG_ON)
    assert prime is False
    assert missing == ["RSI 48 (prime 35-45)"]


def test_ivr_45_with_others_perfect_fails():
    """A B/A- can pass the weighted grade with IVr 45 when other legs are
    perfect — the conjunction cannot ("[IV] rank is greater than 60")."""
    prime, missing = sg.prime_conjunction(
        _csp_components(iv_rank=45.0), "csp", CFG_ON)
    assert prime is False
    assert missing == ["IVr 45 < 60"]


def test_one_touch_support_fails():
    """"support under strike" means a TESTED (≥2-touch) cluster — a
    single touch is noise, not support."""
    prime, missing = sg.prime_conjunction(
        _csp_components(support_resistance=SR_1TOUCH_SUPPORT),
        "csp", CFG_ON)
    assert prime is False
    assert missing == ["no ≥2-touch support under the strike"]


def test_rvr_proxy_only_vol_cannot_satisfy_prime():
    """A realized-vol proxy — even at rank 80 — is not a TRUE chain IVr;
    prime requires the real implied read and the miss says why."""
    prime, missing = sg.prime_conjunction(
        _csp_components(iv_rank=80.0, iv_rank_source="rv"), "csp", CFG_ON)
    assert prime is False
    assert len(missing) == 1
    assert "realized-vol proxy" in missing[0]
    assert "chain IVr ≥ 60" in missing[0]


def test_support_above_strike_fails():
    """"support under strike" — a cluster sitting ABOVE the strike is the
    wrong side, even inside the grade's ±3% scoring band."""
    sr = {"supports": [{"price": 152.0, "touches": 3, "strength": 5.0}],
          "resistances": []}
    prime, missing = sg.prime_conjunction(
        _csp_components(support_resistance=sr), "csp", CFG_ON)
    assert prime is False
    assert any("above the $150 strike" in m for m in missing)


def test_green_day_and_near_earnings_each_fail_csp():
    prime, missing = sg.prime_conjunction(
        _csp_components(day_change_pct=0.02), "csp", CFG_ON)
    assert prime is False
    assert missing == ["green day (prime wants a red day)"]
    prime, missing = sg.prime_conjunction(
        _csp_components(days_to_earnings=5), "csp", CFG_ON)
    assert prime is False
    assert missing == ["earnings 5d away (< 14d clear)"]


def test_broken_trend_fails():
    prime, missing = sg.prime_conjunction(
        _csp_components(lt_verdict="weakening"), "csp", CFG_ON)
    assert prime is False
    assert missing == ["LT trend weakening (prime needs intact)"]


def test_unmeasured_component_is_never_prime():
    """A conjunction can't be verified on missing data (rule #19) — an
    unmeasured RSI is non-prime, listed as such, never guessed."""
    prime, missing = sg.prime_conjunction(
        _csp_components(rsi=None), "csp", CFG_ON)
    assert prime is False
    assert missing == ["RSI n/a (prime 35-45)"]


def test_multi_miss_lists_every_component_measured():
    prime, missing = sg.prime_conjunction(
        _csp_components(rsi=48.0, iv_rank=45.0, day_change_pct=0.02),
        "csp", CFG_ON)
    assert prime is False
    assert missing == ["RSI 48 (prime 35-45)", "IVr 45 < 60",
                       "green day (prime wants a red day)"]


def test_cc_prime_thresholds_rsi_70_ivr_40():
    """CC card: RSI ≥ 70 (prime strength) AND IVr ≥ 40."""
    prime, missing = sg.prime_conjunction(
        _cc_components(rsi=65.0), "cc", CFG_ON)
    assert prime is False
    assert missing == ["RSI 65 < 70"]
    prime, missing = sg.prime_conjunction(
        _cc_components(iv_rank=35.0), "cc", CFG_ON)
    assert prime is False
    assert missing == ["IVr 35 < 40"]


def test_earnings_exempt_basket_skips_earnings_check():
    """An ETF/basket has no print — earnings can't hold prime back."""
    prime, missing = sg.prime_conjunction(
        _csp_components(days_to_earnings=None, earnings_exempt=True),
        "csp", CFG_ON)
    assert prime is True
    assert missing == []


# ── The graders attach prime — tier ON TOP, grade math unchanged ─────────


def test_csp_setup_attaches_prime_and_grade_unchanged():
    kw = dict(_csp_components())
    kw.pop("days_to_earnings")
    g = sg.csp_setup(days_to_earnings=30, drawdown_pct=10.0,
                     config=CFG_ON, **kw)
    assert g["prime"] is True
    assert g["prime_missing"] == []
    assert g["letter"] in ("A", "A-", "B")      # floors untouched


def test_non_prime_grade_keeps_its_letter():
    """A tier on top, not a re-weighting: losing prime never moves the
    weighted letter/score."""
    kw = dict(_csp_components(iv_rank=45.0))
    g = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON, **kw)
    assert g["prime"] is False
    assert g["prime_missing"] == ["IVr 45 < 60"]
    kw2 = dict(_csp_components(iv_rank=45.0, iv_rank_source="rv"))
    g2 = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON, **kw2)
    assert g["score"] == g2["score"]            # source changes prime only


def test_hard_blocked_grade_is_never_prime():
    g = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON,
                     **_csp_components(rsi=75.0))
    assert g["letter"] == "—"
    assert g["prime"] is False


def test_unverified_vintage_cap_revokes_prime():
    """Rule #46: an unverified RSI can never claim every check is in its
    prime band."""
    g = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON, **_csp_components())
    assert g["prime"] is True
    capped = sg._cap_unverified_grade(g, CFG_ON)
    assert capped["prime"] is False
    assert capped["prime_missing"] == [sg.UNVERIFIED_RSI_NOTE]


# ── (e) Badge — 💎 PRIME next to the grade ────────────────────────────────


def test_format_grade_note_carries_prime_badge():
    """"we should clearly see all of the good entries" — the badge rides
    next to the grade on every surface printing the note line."""
    g = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON, **_csp_components())
    note = sg.format_grade_note(g)
    assert "💎 PRIME**" in note
    assert note.startswith("**Setup Grade: ")


def test_format_grade_note_no_badge_when_not_prime():
    g = sg.csp_setup(drawdown_pct=10.0, config=CFG_ON,
                     **_csp_components(iv_rank=45.0))
    assert "💎" not in sg.format_grade_note(g)


# ── (b) Best Setups panel — Prime subsection first ───────────────────────


def _graded_idea(ticker, letter, score, ann, prime=False, prime_missing=None,
                 **kw):
    idea = {
        "ticker": ticker, "source": "recommendation_list_csp",
        "instruction": f"SELL 1x {ticker} $95P", "type": "CSP",
        "strike": 95.0, "expiration": "2026-09-18",
        "expiration_pretty": "Fri Sep 18 '26", "dte": 35, "mid": 1.9,
        "annualized_pct": ann, "rsi_14": 41.0,
        "setup_grade": letter, "setup_grade_score": score,
        "setup_grade_message": f"🏁 Entry: {letter} — test",
        "setup_grade_drivers": ["RSI 41 prime"],
        "setup_grade_prime": prime,
        "setup_grade_prime_missing": list(prime_missing or []),
    }
    idea.update(kw)
    return idea


def test_panel_renders_prime_section_first_with_passer():
    ideas = [
        _graded_idea("PRM", "A", 90.0, 20.0, prime=True),
        _graded_idea("WGT", "B", 70.0, 18.0, prime=False,
                     prime_missing=["IVr 45 < 60"]),
    ]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert [e["ticker"] for e in best["prime"]] == ["PRM"]
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    p = md.index("💎 Prime entries — card-strict")
    c = md.index("Sell puts into weakness (CSP):")
    assert p < c                                  # FIRST subsection
    assert "- 💎 **A** (90) `PRM`" in md
    # The weighted list still shows the passer (tier on top, not a move).
    assert "**A** (90) 💎 `PRM`" in md


def test_panel_prime_passer_keeps_deferred_tag():
    tag = ("⏸ Deferred (capacity gated) — stress coverage 0.22× < 0.50× "
           "floor; shown for planning, not a green light (rule #41)")
    ideas = [_graded_idea("GTD", "A", 88.0, 18.0, prime=True,
                          capacity_blocked=True)]
    best = sg.collect_best_setups(new_ideas=ideas, capacity_tag=tag,
                                  config=CFG_ON)
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    prime_at = md.index("💎 Prime entries")
    assert tag in md[prime_at:md.index("Sell puts into weakness")]


def test_panel_no_passers_shows_closest_miss_measured():
    """'_none today — closest miss: CGNX (IVr 52 < 60)_' — from the
    top-scored candidate's measured missing list, never padded."""
    ideas = [
        _graded_idea("CGNX", "B", 74.0, 18.0,
                     prime_missing=["IVr 52 < 60"]),
        _graded_idea("LOW", "B", 66.0, 16.0,
                     prime_missing=["RSI 58 (prime 35-45)"]),
    ]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert best["prime"] == []
    assert best["prime_closest_miss"]["ticker"] == "CGNX"
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    assert "_none today — closest miss: CGNX (IVr 52 < 60)_" in md


def test_panel_no_prime_info_renders_plain_none_never_fabricated():
    """Legacy entries without a measured missing list get '_none today_'
    — a fabricated closest-miss would violate rule #19."""
    ideas = [{"ticker": "OLD", "setup_grade": "B",
              "setup_grade_score": 70.0, "setup_grade_message": "🏁 B",
              "setup_grade_drivers": [], "strike": 95.0,
              "expiration_pretty": "Fri Sep 18 '26",
              "annualized_pct": 18.0, "rsi_14": 41.0}]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    assert "- _none today_" in md
    assert "closest miss" not in md


def test_prime_list_not_capped_by_top_n():
    """Every conjunction-passer is unmistakable — the prime list is never
    cut at the weighted top-N."""
    ideas = [_graded_idea(f"T{i}", "B", 66.0 + i, 18.0, prime=True)
             for i in range(5)]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert len(best["prime"]) == 5
    assert len(best["csp"]) == 3                  # weighted list still top-3


# ── (c) Digest keeps the Prime subsection within budget ──────────────────


def test_digest_keeps_prime_subsection_within_budget():
    from render.digest import build_digest
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea("PRM", "A", 90.0, 20.0, prime=True)],
        config=CFG_ON)
    bs_lines = sg.render_best_setups(best, config=CFG_ON)
    full = "\n".join([
        "# Daily Briefing — 2026-08-14", "",
        "## Today's Action List", "", "1. **CLOSE** MU $95P — take profit",
        "",
        *bs_lines,
        "## Watch", "", "- stuff", "",
        "## Red Flags & Priorities", "", "- none", "",
    ])
    digest = build_digest(full, config={"render": {"digest": True}},
                          extras={"date": "2026-08-14"})
    assert "💎 Prime entries — card-strict" in digest
    assert "- 💎 **A** (90) `PRM`" in digest
    assert len(digest.splitlines()) <= 250        # digest line budget


def test_digest_keeps_closest_miss_line():
    from render.digest import build_digest
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea("CGNX", "B", 74.0, 18.0,
                                prime_missing=["IVr 52 < 60"])],
        config=CFG_ON)
    bs_lines = sg.render_best_setups(best, config=CFG_ON)
    full = "\n".join([
        "# Daily Briefing — 2026-08-14", "",
        "## Today's Action List", "", "1. item", "",
        *bs_lines,
        "## Red Flags & Priorities", "", "- none", "",
    ])
    digest = build_digest(full, config={"render": {"digest": True}},
                          extras={"date": "2026-08-14"})
    assert "closest miss: CGNX (IVr 52 < 60)" in digest


# ── (d) EntryDecision.prime — annotation only, never flips a verdict ─────


def _prime_snapshot():
    """APP with a TRUE chain IVr, red day, 3-touch support under the
    strike — the full card."""
    return {
        "technicals": {"APP": {
            "spot": 300.0, "rsi_14": 42.0, "sma_200": 250.0,
            "support_resistance": {
                "supports": [{"price": 278.0, "touches": 3,
                              "strength": 5.0}],
                "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        }},
        "iv_ranks": {"APP": 70.0},
        "chain_iv": {"APP": {"iv_rank": 75.0}},
        "quotes": {"APP": {"last": 301.0, "dayChangePct": -0.012}},
        "positions": [],
        "balance": {"accountValue": 1_000_000},
        "earnings_calendar": {"APP": "2026-12-01"},
    }


def test_entry_decision_prime_true_on_full_card():
    dec = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                            _prime_snapshot(), None, CFG_ON, as_of=AS_OF)
    assert dec.verdict == "ENTER"
    assert dec.prime is True
    f = [x for x in dec.ordered_reasons
         if x["check"] == "prime_conjunction"]
    assert len(f) == 1 and "💎 PRIME" in f[0]["detail"]
    assert "💎 PRIME" in dec.one_line


def test_non_prime_still_enters_annotation_never_flips_verdict():
    """"a non-prime B is still ENTER when everything else passes" — the
    missing list rides in ordered_reasons, the verdict is untouched."""
    snap = _prime_snapshot()
    del snap["chain_iv"]                         # vol becomes RVr proxy
    dec = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                            snap, None, CFG_ON, as_of=AS_OF)
    assert dec.verdict == "ENTER"                # verdict unchanged
    assert dec.prime is False
    f = [x for x in dec.ordered_reasons
         if x["check"] == "prime_conjunction"][0]
    assert f["status"] == "pass"                 # annotation, not a gate
    assert "not prime" in f["detail"]
    assert "realized-vol proxy" in f["detail"]
    assert "💎" not in dec.one_line


def test_prime_annotation_on_wait_and_blocked_paths():
    """The annotation coexists with WAIT/BLOCKED verdicts — it neither
    rescues nor worsens them."""
    snap = _prime_snapshot()
    snap["technicals"]["APP"]["rsi_14"] = 64.0   # extended band → WAIT
    snap["quotes"]["APP"]["last"] = 300.5
    dec = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                            snap, None, CFG_ON, as_of=AS_OF)
    assert dec.verdict == "WAIT"
    assert dec.prime is False
    snap2 = _prime_snapshot()
    snap2["technicals"]["APP"]["rsi_14"] = 78.0  # hard block
    snap2["quotes"]["APP"]["last"] = 300.5
    dec2 = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                             snap2, None, CFG_ON, as_of=AS_OF)
    assert dec2.verdict == "BLOCKED"
    assert dec2.prime is False
    f = [x for x in dec2.ordered_reasons
         if x["check"] == "prime_conjunction"][0]
    assert f["status"] == "n/a"                  # blocked → not applicable


def test_evaluate_entry_deterministic_with_prime():
    a = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                          _prime_snapshot(), None, CFG_ON, as_of=AS_OF)
    b = ea.evaluate_entry("csp", "APP", 280.0, "2026-09-18", 6.5,
                          _prime_snapshot(), None, CFG_ON, as_of=AS_OF)
    assert a.prime == b.prime
    assert a.ordered_reasons == b.ordered_reasons
