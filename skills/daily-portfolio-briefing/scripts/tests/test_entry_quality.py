"""Tests for the entry-report quality fixes (12-entry-pipeline-spec §4-§7).

Covers:
  §4 — verdict-flip audit trail (flip line with derived cause, state persisted)
  §5 — RSI override labelling + missing-RSI ticket suppression (fail closed)
  §6 — DCF sanity suppression (model/consensus divergence)
  §7 — when_to_enter downgrades ENTRY NOW to match the candidates verdicts
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from analysis import verdict_state as vs  # noqa: E402
from steps import candidate_research as cr  # noqa: E402
from steps.when_to_enter import classify, render_when_to_enter_report  # noqa: E402


def _res(**kw):
    """A scout result with sane defaults; override per test."""
    base = {
        "ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
        "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
        "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
        "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0, "ask": 2.2,
                      "expiration": "2026-06-19", "dte": 29},
    }
    base.update(kw)
    return base


def _payload(results):
    return {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout",
                             "anchors": [], "etfs": []}},
        "results_by_theme": {"semis": results},
    }


def _card(md, tk):
    return [b for b in md.split("\n\n") if f"`{tk}`" in b][0]


# ─────────────────────────────────────────────────────────────────────────────
# §4 — Verdict-flip audit trail
# ─────────────────────────────────────────────────────────────────────────────

def test_first_run_has_no_flip_section_and_persists_state(tmp_path):
    state = tmp_path / "scout_verdicts.yaml"
    md = cr.render_candidate_report(_payload([_res()]), fv_by_ticker={},
                                    config={}, generated_at="T",
                                    verdict_state_path=state)
    assert "Verdict changes since last run" not in md
    stored = yaml.safe_load(state.read_text())
    assert stored["AMD"]["verdict"] == "CANDIDATE"
    assert stored["AMD"]["date"] == date.today().isoformat()
    assert stored["AMD"]["key_inputs"]["drawdown"] == 12
    assert stored["AMD"]["key_inputs"]["rsi"] == 40
    assert stored["AMD"]["key_inputs"]["has_buy_rec"] is False


def test_verdict_flip_line_renders_with_cause(tmp_path):
    """The ORCL case: AVOID at 35% drawdown → CANDIDATE at 46% + BUY rec.
    The flip line must name the trigger (spec §4)."""
    state = tmp_path / "scout_verdicts.yaml"
    run1 = _res(ticker="ORCL", spot=120.0, rsi_14=40, drawdown_pct=35,
                verdict="AVOID", rationale=["drawdown 35%"], csp_entry=None)
    cr.render_candidate_report(_payload([run1]), fv_by_ticker={}, config={},
                               generated_at="T1", verdict_state_path=state)
    run2 = dict(run1, drawdown_pct=46, third_party_rec="BUY",
                verdict="BUY (pullback)", rationale=["drawdown 46% + BUY rec"])
    md = cr.render_candidate_report(_payload([run2]), fv_by_ticker={}, config={},
                                    generated_at="T2", verdict_state_path=state)
    assert "## Verdict changes since last run" in md
    assert "ORCL: AVOID → CANDIDATE" in md
    assert "drawdown 46%" in md
    assert "third-party BUY now present" in md
    # State updated to the new verdict for the next run's diff.
    stored = yaml.safe_load(state.read_text())
    assert stored["ORCL"]["verdict"] == "CANDIDATE"


def test_verdict_flip_without_derivable_cause_says_review(tmp_path):
    """Verdict class changed but none of the key inputs moved → the audit line
    falls back to '(inputs changed — review)' rather than inventing a cause."""
    state = tmp_path / "scout_verdicts.yaml"
    run1 = _res(ticker="NOW", rsi_14=42, drawdown_pct=20, third_party_rec="BUY",
                verdict="WATCH", csp_entry=None)
    cr.render_candidate_report(_payload([run1]), fv_by_ticker={}, config={},
                               generated_at="T1", verdict_state_path=state)
    run2 = dict(run1, verdict="CSP ENTRY (fat premium)",
                csp_entry={"strike": 90, "mid": 1.5, "bid": 1.4, "ask": 1.6,
                           "expiration": "2026-06-19", "dte": 29})
    md = cr.render_candidate_report(_payload([run2]), fv_by_ticker={}, config={},
                                    generated_at="T2", verdict_state_path=state)
    assert "NOW: WATCH → CANDIDATE (inputs changed — review)" in md


def test_no_flip_section_when_verdicts_unchanged(tmp_path):
    state = tmp_path / "scout_verdicts.yaml"
    for gen in ("T1", "T2"):
        md = cr.render_candidate_report(_payload([_res()]), fv_by_ticker={},
                                        config={}, generated_at=gen,
                                        verdict_state_path=state)
    assert "Verdict changes since last run" not in md


# ─────────────────────────────────────────────────────────────────────────────
# §5 — RSI override labelling + missing-RSI fail-closed
# ─────────────────────────────────────────────────────────────────────────────

def test_override_candidate_labeled_not_favourable():
    """A CSP candidate at RSI 57 (outside the configured 35-55 entry band,
    below the 60-70 extended-wait demotion) with 74% drawdown + BUY rec
    qualifies only via the override path — the card must say OVERRIDE, never
    '✅ RSI favourable'."""
    r = _res(ticker="BIDU", rsi_14=57, drawdown_pct=74, third_party_rec="BUY")
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker={}, config={},
                                    generated_at="T")
    card = _card(md, "BIDU")
    assert "RSI 57 — OVERRIDE (drawdown 74% + BUY rec)" in card
    assert "✅ RSI favourable" not in card
    # The ticket itself carries the override tag too.
    assert "Entry (CSP):" in card


def test_override_buy_entry_line_not_presented_as_favourable():
    r = _res(ticker="INTC", rsi_14=57, drawdown_pct=60, third_party_rec="BUY",
             verdict="BUY (pullback)", csp_entry=None)
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker={}, config={},
                                    generated_at="T")
    card = _card(md, "INTC")
    assert "RSI 57 — OVERRIDE" in card
    assert "RSI favourable" not in card


def test_in_band_candidate_unchanged():
    """RSI 40 (inside the configured 35-55 band) renders the normal favourable
    card — no OVERRIDE."""
    md = cr.render_candidate_report(_payload([_res()]), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = _card(md, "AMD")
    assert "OVERRIDE" not in card
    assert "✅ RSI favourable" in card


def test_missing_rsi_candidate_renders_no_ticket():
    """A candidate with no RSI value may not render a concrete ticket —
    fail closed (spec §5)."""
    r = _res(ticker="SNOW", rsi_14=None)
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker={}, config={},
                                    generated_at="T")
    card = _card(md, "SNOW")
    assert "— no ticket: RSI unavailable (fail closed)" in card
    assert "Entry (CSP):" not in card
    assert "$135P" not in card


def test_when_to_enter_classify_in_band_rsi_needs_no_override():
    """Band unification (2026-08-07 QQQ bug): RSI 53 sits INSIDE the configured
    35-55 entry band — the read presents it as a pullback, never as an
    OVERRIDE. (The old hardcoded 35-50 band in verdict_state mislabelled
    in-band RSI 50-55 setups as overrides.)"""
    r = {"ticker": "BIDU", "spot": 50.0, "rsi_14": 53, "iv_rank": 50,
         "sma_200": 52, "drawdown_pct": 74, "fivedayret_pct": -1.0,
         "verdict": "BUY (pullback)", "rationale": [], "csp_entry": None,
         "days_to_earnings": None, "third_party_rec": "BUY"}
    status, label, read, _ = classify(r)
    assert status == "enter"
    assert "RSI 53 in pullback zone" in read
    assert "OVERRIDE" not in read


def test_when_to_enter_classify_no_rsi_fails_closed():
    r = {"ticker": "X", "spot": 100.0, "rsi_14": None, "iv_rank": 50,
         "sma_200": 100, "drawdown_pct": 0, "fivedayret_pct": 0,
         "verdict": "CSP ENTRY (fat premium)", "rationale": [],
         "csp_entry": {"strike": 90, "mid": 1.5, "bid": 1.4, "ask": 1.6,
                       "expiration": "2026-07-17", "dte": 35},
         "days_to_earnings": None}
    status, label, _, trigger = classify(r)
    assert status == "watch"
    assert "no RSI" in label
    assert "— no ticket: RSI unavailable (fail closed)" in trigger
    assert "SELL 1×" not in trigger


# ─────────────────────────────────────────────────────────────────────────────
# §6 — DCF sanity suppression
# ─────────────────────────────────────────────────────────────────────────────

def test_fv_suppressed_on_divergence_and_consensus_disagreement():
    """The PLTR case: DCF $11 under spot $138 while analysts target $170 —
    >60% divergence + opposite direction → suppress the number."""
    fv = {"PLTR": {"dcf": 11.0, "analyst_target": 170.0, "num_analysts": 20}}
    r = _res(ticker="PLTR", spot=138.0)
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker=fv, config={},
                                    generated_at="T")
    card = _card(md, "PLTR")
    assert "FV: unreliable (model/consensus divergence)" in card
    assert "DCF $11" not in card


def test_fv_kept_with_caveat_when_no_analyst_target():
    """The AMZN case without a consensus arbiter: keep the divergent DCF but
    flag it as a model estimate."""
    fv = {"AMZN": {"dcf": 82.0, "analyst_target": None, "num_analysts": None}}
    r = _res(ticker="AMZN", spot=237.0)
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker=fv, config={},
                                    generated_at="T")
    card = _card(md, "AMZN")
    assert "DCF $82" in card
    assert "(model estimate — large divergence from spot)" in card


def test_fv_kept_plain_when_divergent_but_consensus_agrees():
    """DCF 70% below spot but analysts also see it lower → no suppression."""
    fv = {"DOCU": {"dcf": 30.0, "analyst_target": 80.0, "num_analysts": 10}}
    r = _res(ticker="DOCU", spot=100.0)
    md = cr.render_candidate_report(_payload([r]), fv_by_ticker=fv, config={},
                                    generated_at="T")
    card = _card(md, "DOCU")
    assert "DCF $30" in card
    assert "unreliable" not in card
    assert "model estimate" not in card


def test_fv_untouched_within_60pct_of_spot():
    fv = {"AMD": {"dcf": 120.0, "analyst_target": 160.0, "num_analysts": 42}}
    md = cr.render_candidate_report(_payload([_res()]), fv_by_ticker=fv,
                                    config={}, generated_at="T")
    card = _card(md, "AMD")
    assert "DCF $120" in card and "analyst PT $160" in card
    assert "unreliable" not in card and "model estimate" not in card


# ─────────────────────────────────────────────────────────────────────────────
# §7 — candidates ↔ when_to_enter consistency contract
# ─────────────────────────────────────────────────────────────────────────────

def _wte_payload():
    return {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout"}},
        "results_by_theme": {"semis": [{
            "ticker": "AMD", "spot": 150.0, "rsi_14": 42, "iv_rank": 60,
            "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
            "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
            "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0, "ask": 2.2,
                          "expiration": "2026-07-17", "dte": 35},
            "days_to_earnings": None,
        }]},
        "generated_at_iso": "2026-06-11T08:00:00",
    }


def test_when_to_enter_downgrades_entry_to_match_candidates(tmp_path):
    """Candidates report (same date) classified AMD WATCH → when_to_enter may
    not show ENTRY NOW; downgrade with the alignment note, ticket suppressed."""
    state = tmp_path / "v.yaml"
    vs.save({"AMD": {"verdict": "WATCH", "date": date.today().isoformat(),
                     "key_inputs": {}}}, state)
    md = render_when_to_enter_report(_wte_payload(), generated_at="X",
                                     verdict_state_path=state)
    assert "(aligned to candidates report)" in md
    assert "🟢 0 ENTRY NOW" in md
    assert "SELL 1×" not in md          # no ticket survives the downgrade


def test_when_to_enter_downgrade_uses_held_rsi_wait(tmp_path):
    state = tmp_path / "v.yaml"
    vs.save({"AMD": {"verdict": "HELD_RSI", "date": date.today().isoformat(),
                     "key_inputs": {}}}, state)
    md = render_when_to_enter_report(_wte_payload(), generated_at="X",
                                     verdict_state_path=state)
    assert "🟡 WAIT — held by RSI (aligned to candidates report)" in md


def test_when_to_enter_unchanged_when_candidates_agree(tmp_path):
    state = tmp_path / "v.yaml"
    vs.save({"AMD": {"verdict": "CANDIDATE", "date": date.today().isoformat(),
                     "key_inputs": {}}}, state)
    md = render_when_to_enter_report(_wte_payload(), generated_at="X",
                                     verdict_state_path=state)
    assert "ENTRY NOW — CSP" in md
    assert "SELL 1×" in md
    assert "aligned to candidates report" not in md


def test_when_to_enter_ignores_stale_dated_state(tmp_path):
    """A stale (yesterday's) state file must not constrain today's report."""
    state = tmp_path / "v.yaml"
    vs.save({"AMD": {"verdict": "WATCH", "date": "2020-01-01",
                     "key_inputs": {}}}, state)
    md = render_when_to_enter_report(_wte_payload(), generated_at="X",
                                     verdict_state_path=state)
    assert "ENTRY NOW — CSP" in md
    assert "SELL 1×" in md
    assert "aligned to candidates report" not in md


def test_when_to_enter_behaves_as_before_without_state_file(tmp_path):
    md = render_when_to_enter_report(_wte_payload(), generated_at="X",
                                     verdict_state_path=tmp_path / "missing.yaml")
    assert "ENTRY NOW — CSP" in md
    assert "SELL 1×" in md


def test_pipeline_order_candidates_write_then_wte_aligns(tmp_path):
    """End-to-end §7: render the candidates report (writes state), then the
    when_to_enter report — an RSI-held name can't show ENTRY NOW."""
    state = tmp_path / "scout_verdicts.yaml"
    # NVDA RSI 78 → held_rsi in the candidates report...
    held = _res(ticker="NVDA", rsi_14=78, spot=217.0)
    cr.render_candidate_report(_payload([held]), fv_by_ticker={}, config={},
                               generated_at="T", verdict_state_path=state)
    # ...but suppose when_to_enter's own classifier would say enter (simulate a
    # divergent read with an in-band RSI in ITS copy of the data).
    wte = _wte_payload()
    wte["results_by_theme"]["semis"][0]["ticker"] = "NVDA"
    md = render_when_to_enter_report(wte, generated_at="X",
                                     verdict_state_path=state)
    assert "(aligned to candidates report)" in md
    assert "🟢 0 ENTRY NOW" in md
