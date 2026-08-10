"""2026-08-10 briefing fixes — five defects observed in the real briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).

Observed (briefing_2026-08-10.md digest + briefing_full_2026-08-10.md):
  1a. MELI $1460P Jun '27 opened Friday ($146,000 obligation, 13.3% of NLV)
      — 'Since Yesterday' said nothing (closes + rolls only).
  1b. No concentration flag fired — the equity-MV-only check saw 0% MELI.
  2.  '### 🔄 Detected User-Executed Rolls (1)' rendered with ZERO bullets
      in the digest (lines 111-112).
  3.  'PEP … SELL 1× PEP $125P … mid $0.29' ($29 / $12,500 = 2.6% ann) and
      'MCD … $245P … mid $0.35' (~1.6% ann) rendered as top candidates
      with conviction badges, far below the 12% / 0.5% floors.
  4.  Action #1 was 'CSP — PAID-TO-WAIT VRT … ⏸ Deferred (capacity gated)'
      — a planning card ranked ABOVE the executable CLOSE RDDT, and
      'Action Items: 2' counted it as executable.
  5.  '_Strategy: sell a put below spot — … The name does not claim the
      stock is currently pulling back; today's state: RSI 46 (pullback)._'
      — meta-commentary about the recommendation's NAME.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.briefing_diff import (  # noqa: E402
    detect_executed_opens,
    detect_executed_rolls,
    render_diff_panel,
    render_executed_open_lines,
    render_executed_roll_directives,
)
from render.digest import build_digest  # noqa: E402
from render.panels import (  # noqa: E402
    sort_deferred_actions,
    sync_action_item_count,
)
from steps import candidate_research as cr  # noqa: E402
from steps.red_flags import compute_red_flags  # noqa: E402

TODAY = "2026-08-10"


def _pos(sym, und, typ, strike, exp, qty=-1, premium=None):
    p = {"symbol": sym, "assetType": "OPTION", "underlying": und,
         "type": typ, "strike": strike, "expiration": exp, "qty": qty}
    if premium is not None:
        p["premiumReceived"] = premium
    return p


def _eq(sym, qty, price, mv=None):
    return {"symbol": sym, "assetType": "EQUITY", "qty": qty, "price": price,
            "marketValue": mv if mv is not None else qty * price}


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1a — user-executed OPENS were invisible in 'Since Yesterday'
# ─────────────────────────────────────────────────────────────────────────────

_OTHER_PUT = _pos("XYZ_PUT_2532_20261218", "XYZ", "PUT", 2532.0, "2026-12-18")
_MELI_PUT = _pos("MELI_PUT_1460_20270617", "MELI", "PUT", 1460.0, "2027-06-17")


def test_meli_open_detected_with_measured_obligation_and_coverage():
    """Observed: MELI $1460P Jun '27 appeared in 2026-08-10 positions.json
    (absent 2026-08-07) — a $146,000 obligation, 13.3% of NLV ($1,097,890)
    — with NO 'Since Yesterday' mention and coverage's 0.22×→0.17× drop
    unexplained. The open must be detected with MEASURED obligation, % of
    NLV, and the same-day coverage counterfactual (rule #19)."""
    prev = [_OTHER_PUT]
    today = [_OTHER_PUT, _MELI_PUT]
    opens = detect_executed_opens(prev, today, nlv=1_097_721.08,
                                  cash=69_441.95)
    assert len(opens) == 1
    o = opens[0]
    assert o["symbol"] == "MELI_PUT_1460_20270617"
    assert o["obligation"] == 146_000.0
    assert abs(o["pct_of_nlv"] - 13.3) < 0.05
    # cash / total obligation with vs without the open — both measured
    assert abs(o["coverage_with"] - 69_441.95 / 399_200.0) < 1e-9
    assert abs(o["coverage_without"] - 69_441.95 / 253_200.0) < 1e-9

    lines = "\n".join(render_executed_open_lines(opens))
    assert "**MELI_PUT_1460_20270617**" in lines
    assert "$146,000" in lines
    assert "13.3% of NLV" in lines
    assert "0.27× → 0.17×" in lines            # measured counterfactual
    assert "$399,200" in lines                 # today's total put obligation


def test_open_section_renders_in_since_yesterday_panel():
    opens = detect_executed_opens([_OTHER_PUT], [_OTHER_PUT, _MELI_PUT],
                                  nlv=1_097_721.08, cash=69_441.95)
    yesterday_md = "## Today's Action List\n1. **CLOSE** XYZ_PUT_2532_20261218 — x\n"
    today_md = "## Today's Action List\n1. **CLOSE** XYZ_PUT_2532_20261218 — x\n"
    panel = "\n".join(render_diff_panel(today_md, yesterday_md,
                                        executed_opens=opens,
                                        today_iso=TODAY))
    assert "### 🆕 Detected User-Executed Opens (1)" in panel
    assert "$146,000" in panel and "13.3% of NLV" in panel


def test_roll_sto_leg_is_not_a_fresh_open():
    """The QCOM $185P Dec → $180P Feb roll was already detected as a roll —
    its STO leg must NOT double-report as a fresh open."""
    prev = [_pos("QCOM_PUT_185_20261218", "QCOM", "PUT", 185.0, "2026-12-18")]
    today = [_pos("QCOM_PUT_180_20270219", "QCOM", "PUT", 180.0, "2027-02-19"),
             _MELI_PUT]
    rolls = detect_executed_rolls(prev, today)
    assert len(rolls) == 1 and rolls[0]["underlying"] == "QCOM"
    opens = detect_executed_opens(prev, today, nlv=1_000_000, cash=50_000)
    assert [o["underlying"] for o in opens] == ["MELI"]


def test_detect_executed_opens_fails_open_on_missing_snapshots():
    assert detect_executed_opens(None, [_MELI_PUT]) == []
    assert detect_executed_opens([], []) == []
    assert detect_executed_opens([_OTHER_PUT], None) == []


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1b — obligation-inclusive per-name concentration flag
# ─────────────────────────────────────────────────────────────────────────────

def _snap(positions, nlv=1_097_721.08, cash=69_441.95, config=None):
    return {"balance": {"accountValue": nlv, "cash": cash},
            "positions": positions,
            "quotes": {},
            "_config": config or {}}


def _flags(snapshot_data):
    return compute_red_flags(snapshot_data=snapshot_data, analytics=None,
                             options_reviews=[], equity_reviews=[],
                             capital_plan=None, recommendations_list=[])


def test_meli_13_pct_obligation_fires_concentration_flag():
    """Observed: the MELI $1460P is 13.3% of NLV on a single name — over the
    10% Tier C cap — yet no concentration flag fired (the equity-MV check
    saw 0% because there are no MELI shares). The obligation-inclusive
    check must fire MEDIUM/HIGH with the measured numbers."""
    flags = _flags(_snap([_MELI_PUT]))
    hits = [f for f in flags
            if "Obligation-inclusive concentration: MELI" in f.headline]
    assert len(hits) == 1
    f = hits[0]
    assert f.severity in ("MEDIUM", "HIGH")
    assert "13.3% of NLV" in f.headline
    assert "$146,000" in f.headline
    # unconfigured tier caps fall back to Tier C 8% (production
    # briefing.yaml sets tier_c_max_pct: 8.0 too) — 13.3% ≥ 1.5×8 → HIGH
    assert "8% Tier C cap" in f.headline
    assert f.severity == "HIGH"


def test_under_cap_obligation_stays_silent():
    """A short-put obligation UNDER the tier cap must not flag (no noise)."""
    small = _pos("ABC_PUT_500_20261218", "ABC", "PUT", 500.0, "2026-12-18")
    flags = _flags(_snap([small]))     # $50,000 = 4.6% of NLV < 8% cap
    assert not any("Obligation-inclusive" in f.headline for f in flags)


def test_tier_a_22_pct_cap_respected():
    """A Tier A name (cap 22%) with equity 15.3% + $67,000 of puts (21.4%
    total) stays silent; pushing the obligation to 24.4% total fires with
    the Tier A cap named."""
    cfg = {"position_tiers": {"tier_a_core": ["GOOG"]}}
    goog_eq = _eq("GOOG", 476, 353.05, mv=168_048.0)
    under = [goog_eq,
             _pos("GOOG_PUT_335_20261218", "GOOG", "PUT", 335.0,
                  "2026-12-18", qty=-2)]           # $67,000 → 21.4% total
    flags = _flags(_snap(under, config=cfg))
    assert not any("Obligation-inclusive" in f.headline for f in flags)

    over = [goog_eq,
            _pos("GOOG_PUT_500_20261218", "GOOG", "PUT", 500.0,
                 "2026-12-18", qty=-2)]            # $100,000 → 24.4% total
    flags = _flags(_snap(over, config=cfg))
    hits = [f for f in flags if "Obligation-inclusive" in f.headline]
    assert len(hits) == 1
    assert "22% Tier A cap" in hits[0].headline


def test_equity_already_over_cap_does_not_double_flag():
    """When the equity alone is already over the cap, flag #4 / drift
    alerts own it — the obligation-inclusive flag must not duplicate."""
    over_eq = _eq("XXX", 100, 1_300.0, mv=130_000.0)   # 11.8% equity alone
    puts = _pos("XXX_PUT_200_20261218", "XXX", "PUT", 200.0, "2026-12-18")
    flags = _flags(_snap([over_eq, puts]))
    assert not any("Obligation-inclusive" in f.headline for f in flags)


def test_medium_severity_between_cap_and_1_5x_cap():
    med = _pos("MED_PUT_1000_20270617", "MED", "PUT", 1000.0, "2027-06-17")
    flags = _flags(_snap([med], nlv=1_000_000))   # 10%: over 8%, under 12%
    hits = [f for f in flags if "Obligation-inclusive" in f.headline]
    assert hits and hits[0].severity == "MEDIUM"


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — rolls header with zero bullets in the digest
# ─────────────────────────────────────────────────────────────────────────────

def test_roll_directive_lines_are_bullets_that_survive_the_digest():
    """Observed digest (lines 111-112): '### 🔄 Detected User-Executed Rolls
    (1)' followed by NOTHING — the italic '_Detected roll: QCOM $185P
    Dec→Feb …_' line was stripped as a transparency footer. Roll items now
    render as bullets so the digest keeps them."""
    prev = [_pos("QCOM_PUT_185_20261218", "QCOM", "PUT", 185.0, "2026-12-18")]
    today = [_pos("QCOM_PUT_180_20270219", "QCOM", "PUT", 180.0, "2027-02-19",
                  premium=34.0)]
    rolls = detect_executed_rolls(prev, today)
    lines = render_executed_roll_directives(rolls, TODAY)
    assert len(lines) == 1
    assert lines[0].startswith("- 🔄 ")
    assert "Detected roll: QCOM $185P Dec→Feb ($185→$180)" in lines[0]

    full = "\n".join([
        "# Daily Briefing — Test",
        "",
        "**Action Items:** 1",
        "",
        "## Today's Action List",
        "",
        "1. **CLOSE** QCOM_PUT_180_20270219 — x",
        "",
        "## Since Yesterday's Briefing",
        "",
        "### 🔄 Detected User-Executed Rolls (1)",
        lines[0],
        "",
        "### ✅ Resolved or Executed (1)",
        "- CLOSE FOO — done",
        "",
    ])
    digest = build_digest(full)
    assert "Detected User-Executed Rolls (1)" in digest
    assert "Detected roll: QCOM $185P" in digest    # the item is IN the digest


def test_digest_drops_header_left_childless_by_italic_strip():
    """Defensive: a legacy '### … (N)' section whose only content is a
    standalone italic line must lose the HEADER too — never a count-1
    header with zero items (the observed shape)."""
    full = "\n".join([
        "# Daily Briefing — Test",
        "",
        "## Today's Action List",
        "",
        "1. **CLOSE** QCOM_PUT_180_20270219 — x",
        "",
        "## Since Yesterday's Briefing",
        "",
        "### 🔄 Detected User-Executed Rolls (1)",
        "_Detected roll: QCOM $185P Dec→Feb ($185→$180). If deliberate…_",
        "",
        "### ✅ Resolved or Executed (1)",
        "- CLOSE FOO — done",
        "",
    ])
    digest = build_digest(full)
    assert "Detected User-Executed Rolls" not in digest   # header dropped
    assert "Resolved or Executed (1)" in digest           # siblings kept


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — delivered-yield floor on the candidate surface (rule #44)
# ─────────────────────────────────────────────────────────────────────────────

def _thin_payload():
    return {
        "themes": {"staples": {"name": "Consumer Staples & Value",
                               "anchors": ["PEP", "MCD"], "etfs": []},
                   "apps": {"name": "Applications",
                            "anchors": ["APP"], "etfs": []}},
        "results_by_theme": {
            "staples": [
                # Observed: PEP $125P mid $0.29 / 32 DTE — $29 on $12,500
                # collateral ≈ 2.6% ann, WITH a 🔥 high-conviction badge.
                {"ticker": "PEP", "spot": 137.99, "rsi_14": 44, "iv_rank": 55,
                 "sma_200": 150, "drawdown_pct": 15, "fivedayret_pct": -1.0,
                 "verdict": "CSP ENTRY (pullback)", "rationale": ["pullback"],
                 "rating_tier": 3, "conviction": "High",
                 "csp_entry": {"strike": 125, "mid": 0.29, "bid": 0.14,
                               "ask": 0.45, "expiration": "2026-09-11",
                               "dte": 32}},
                # Observed: MCD $245P mid $0.35 / 32 DTE ≈ 1.6% ann.
                {"ticker": "MCD", "spot": 272.10, "rsi_14": 45, "iv_rank": 50,
                 "sma_200": 280, "drawdown_pct": 8, "fivedayret_pct": -0.5,
                 "verdict": "CSP ENTRY (INDEPENDENT SETUP)",
                 "rationale": ["setup"],
                 "csp_entry": {"strike": 245, "mid": 0.35, "bid": 0.27,
                               "ask": 0.44, "expiration": "2026-09-11",
                               "dte": 32}},
            ],
            "apps": [
                # Above-floor control: $8.75 on $30,500 / 32 DTE ≈ 33% ann.
                {"ticker": "APP", "spot": 340.65, "rsi_14": 45, "iv_rank": 60,
                 "sma_200": 350, "drawdown_pct": 20, "fivedayret_pct": -2.0,
                 "verdict": "CSP ENTRY (pullback)", "rationale": ["pullback"],
                 "csp_entry": {"strike": 305, "mid": 8.75, "bid": 8.00,
                               "ask": 9.50, "expiration": "2026-09-11",
                               "dte": 32}},
            ],
        },
    }


def test_below_floor_ticket_demoted_with_measured_numbers():
    """Observed: '**🎯 CANDIDATE · `PEP` · $137.99** ✅ RSI favourable 🔥 high
    conviction … SELL 1× PEP $125P … mid $0.29' — $29 premium on $12,500
    collateral (2.6% ann) rendered as a top candidate. It must demote to a
    visible 'premium too thin' row with the measured numbers, forfeit the
    conviction badge, and never render a 🎯 CANDIDATE card."""
    md = cr.render_candidate_briefing(_thin_payload(), fv_by_ticker={},
                                      config={}, generated_at=TODAY)
    assert "🎯 CANDIDATE · `PEP`" not in md
    assert "🎯 CANDIDATE · `MCD`" not in md
    assert "⏸ Premium too thin — below the delivered-yield floor (2)" in md
    assert "⏸ premium too thin — $29 on $12,500 (2.6% ann < 12% floor)" in md
    assert "⏸ premium too thin — $35 on $24,500 (1.6% ann < 12% floor)" in md
    # conviction badge forfeited — 🔥 appears nowhere on the PEP row
    pep_row = [ln for ln in md.splitlines() if "`PEP`" in ln][0]
    assert "🔥" not in pep_row and "high conviction" not in pep_row
    # thin rows are NOT candidate-card headers, so the digest's Top-N
    # parser (which keys on '**🎯 CANDIDATE') can never pick them.
    assert not pep_row.startswith("**🎯 CANDIDATE")


def test_above_floor_ticket_untouched():
    """The APP $305P at mid $8.75 (~33% ann) stays a normal candidate card
    with its entry ticket — floors only demote genuinely thin premium."""
    md = cr.render_candidate_briefing(_thin_payload(), fv_by_ticker={},
                                      config={}, generated_at=TODAY)
    assert "🎯 CANDIDATE · `APP`" in md
    assert "SELL 1× APP $305P" in md
    app_lines = [ln for ln in md.splitlines() if "`APP`" in ln]
    assert not any("premium too thin" in ln for ln in app_lines)


def test_yield_floor_helper_fails_open_on_missing_data():
    assert cr._yield_floor_failure(None, {}) is None
    assert cr._yield_floor_failure({}, {}) is None
    assert cr._yield_floor_failure({"strike": 125, "mid": 0, "dte": 32},
                                   {}) is None
    assert cr._yield_floor_failure({"strike": 125, "mid": 0.29, "dte": 0},
                                   {}) is None


def test_sector_bonus_cannot_resurrect_below_floor_ticket():
    """The sector-diversification +1 promoted PEP/MCD into the Top-5 digest
    slots. The floor check runs BEFORE card rendering, so no sector note /
    bonus can resurrect a below-floor ticket."""
    md = cr.render_candidate_briefing(
        _thin_payload(), fv_by_ticker={}, config={}, generated_at=TODAY,
        snapshot_data={"positions": [], "quotes": {},
                       "balance": {"accountValue": 1_000_000}})
    assert "🎯 CANDIDATE · `PEP`" not in md
    assert "premium too thin" in md


# ─────────────────────────────────────────────────────────────────────────────
# Bug 4 — deferred planning cards sort below executable; split count
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_MD = "\n".join([
    "# Daily Briefing — Monday, August 10, 2026",
    "",
    "**Portfolio NLV:** $1,097,890 | **Cash:** $69,442 (6.3%)",
    "**Action Items:** 2",
    "",
    "## Today's Action List — Monday, August 10, 2026",
    "",
    "1. **CSP — PAID-TO-WAIT** VRT — sell $240P exp Fri Sep 11 for $6.33 premium",
    "   - **⏸ Deferred (capacity gated) — stress coverage 0.17× < 0.50× floor;"
    " shown for planning, not a green light (rule #41)**",
    "2. **CLOSE** RDDT_PUT_140_20260911 — +31% ($+151); buy-to-close limit $3.57",
    "   - **Why:** 31% of max profit captured.",
    "",
    "### 📋 Total Impact (if all actions executed)",
    "",
    "- **Total actions:** 2",
    "",
    "## 🚦 Red Flags & Priorities",
    "",
    "_none_",
])


def test_deferred_card_sorts_below_executable_and_count_splits():
    """Observed: action #1 was 'CSP — PAID-TO-WAIT VRT … ⏸ Deferred
    (capacity gated)' ranked ABOVE the executable 'CLOSE RDDT', and
    'Action Items: 2' counted the planning card. After the fix the CLOSE is
    #1, the deferred card is #2 (still fully visible — rule #24/#41), and
    the header reads 'Action Items: 1 (+1 deferred)'."""
    out = sync_action_item_count(sort_deferred_actions(_ACTION_MD))
    assert "**Action Items:** 1 (+1 deferred)" in out
    close_at = out.index("**CLOSE** RDDT_PUT_140_20260911")
    csp_at = out.index("**CSP — PAID-TO-WAIT** VRT")
    assert close_at < csp_at                       # executable first
    assert "1. **CLOSE** RDDT_PUT_140_20260911" in out
    assert "2. **CSP — PAID-TO-WAIT** VRT" in out
    assert "⏸ Deferred (capacity gated)" in out    # never hidden
    # Total Impact tail stays after the items
    assert out.index("### 📋 Total Impact") > csp_at


def test_deferred_sort_is_idempotent():
    once = sync_action_item_count(sort_deferred_actions(_ACTION_MD))
    twice = sync_action_item_count(sort_deferred_actions(once))
    assert once == twice


def test_no_deferred_items_keeps_plain_count():
    md = _ACTION_MD.replace(
        "   - **⏸ Deferred (capacity gated) — stress coverage 0.17× < 0.50×"
        " floor; shown for planning, not a green light (rule #41)**",
        "   - **Source:** Live E*TRADE chain")
    out = sync_action_item_count(sort_deferred_actions(md))
    assert "**Action Items:** 2" in out
    assert "deferred" not in out.split("## Today's Action List")[0]
    # order untouched
    assert out.index("**CSP — PAID-TO-WAIT** VRT") \
        < out.index("**CLOSE** RDDT_PUT_140_20260911")


# ─────────────────────────────────────────────────────────────────────────────
# Bug 5 — paid-to-wait explainer template (no meta-commentary about the name)
# ─────────────────────────────────────────────────────────────────────────────

def test_paid_to_wait_explainer_states_strategy_and_todays_state(monkeypatch):
    """Observed: '_Strategy: sell a put below spot — keep the premium if no
    dip comes, or re-acquire at -12% if it does. The name does not claim
    the stock is currently pulling back; today's state: RSI 46
    (pullback)._' — the second sentence is meta-commentary about the
    recommendation's NAME. The template now states only the strategy +
    today's measured state."""
    from render import panels

    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"VRT": {"last": 273.0}},
        "technicals": {"VRT": {"spot": 272.5, "rsi_14": 46.0}},
        "positions": [{"symbol": "VRT", "assetType": "EQUITY",
                       "qty": 100, "price": 273.0}],
        "earnings_calendar": {},
        "recommendations_list": [],
        "_config": {"core_positions": ["VRT"], "accounts": []},
    }
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 240.0, "mid": 6.33,
                                          "expiration": "2026-09-11"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "VRT", "price": 273.0, "weight": 0.02,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    md = "\n".join(panels.render_action_list(
        equity_reviews, [], [], analytics={},
        snapshot_data=snap, date_str=TODAY))
    assert "**CSP — PAID-TO-WAIT** VRT — sell $240P" in md
    assert "Paid-to-wait: keep the premium if no dip comes" in md
    assert "re-acquire at -12% below spot if one does" in md
    assert "Today: RSI 46 (pullback)" in md
    # the awkward boilerplate is gone
    assert "does not claim the stock is currently pulling back" not in md
    assert "Strategy: sell a put below spot" not in md
