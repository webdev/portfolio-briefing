"""Tests for the monthly-expiration preference (analysis/expiration_policy.py).

George (2026-08-10): "Institutions Trade Monthly Options on the 3rd Friday of
each month. We must incorporate this. This is a better time to trade options."

The incorporation is an expiration PREFERENCE — institutional OI/liquidity
concentrates on standard monthly (3rd-Friday) expirations → tighter spreads,
better fills, easier rolls — NOT any 'trade on 3rd Friday' timing logic
(rule #9). Evidence from the 2026-08-10 briefing: the PEP ticket chose
Sep 11 '26 (a 2nd-Friday weekly) with bid $0.14 / ask $0.45 (107% relative
spread) while the Sep 18 monthly sat inside the same DTE band.

Pins:
(a) third_friday / is_monthly date math (Sep 18 '26 monthly, Sep 11 weekly,
    Jun 19 '26 monthly per CLAUDE.md, Jun 20 Saturday NOT monthly);
(b) monthly preferred within the band; no-monthly falls back to legacy;
    measured-spread override keeps the tighter weekly WITH a note;
(c) the PEP case: candidates [Sep 4, Sep 11, Sep 18], target ~35-36 DTE →
    Sep 18 chosen and labeled "(monthly)";
(d) tenor cap (max_dte) never violated by the monthly preference;
(e) config off → legacy selection identical (byte-identical);
(f) monthly opex-week line renders in the final week ONLY;
(g) roll STO legs prefer the monthly and get the kind label.
"""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.expiration_policy import (  # noqa: E402
    OPEX_WEEK_NOTE,
    expiration_kind,
    is_monthly,
    is_monthly_opex_week,
    kind_suffix,
    label_exp,
    opex_week_line,
    policy_enabled,
    prefer_monthly_expiration,
    third_friday,
)
from steps.new_ideas import _next_monthly_expiration  # noqa: E402


# ---------------------------------------------------------------------------
# (a) third_friday / is_monthly math
# ---------------------------------------------------------------------------

def test_third_friday_math_across_months():
    """3rd-Friday math pins the standard monthly for known 2025/2026 dates."""
    assert third_friday(2026, 9) == date(2026, 9, 18)
    assert third_friday(2026, 6) == date(2026, 6, 19)   # CLAUDE.md rule #6 fact
    assert third_friday(2026, 1) == date(2026, 1, 16)
    assert third_friday(2026, 8) == date(2026, 8, 21)
    assert third_friday(2025, 12) == date(2025, 12, 19)
    # Every third_friday is a Friday, whatever the month layout.
    for y in (2025, 2026, 2027):
        for m in range(1, 13):
            assert third_friday(y, m).weekday() == 4


def test_is_monthly_sep18_monthly_sep11_weekly():
    """Sep 18 '26 is the standard monthly; Sep 11 '26 is a 2nd-Friday weekly."""
    assert is_monthly(date(2026, 9, 18)) is True
    assert is_monthly("2026-09-18") is True
    assert is_monthly(date(2026, 9, 11)) is False
    assert is_monthly("2026-09-04") is False
    assert expiration_kind("2026-09-18") == "monthly"
    assert expiration_kind("2026-09-11") == "weekly"


def test_is_monthly_jun19_not_jun20_saturday():
    """Jun 19 '26 (Friday) is the monthly; Jun 20 is a Saturday → never monthly
    (CLAUDE.md rule #6: 'recommending Jun 20 is a bug')."""
    assert is_monthly("2026-06-19") is True
    assert is_monthly("2026-06-20") is False


def test_is_monthly_unparseable_fails_closed():
    assert is_monthly("garbage") is False
    assert is_monthly(None) is False
    assert expiration_kind("garbage") is None
    assert kind_suffix("garbage") == ""


# ---------------------------------------------------------------------------
# (b) prefer_monthly_expiration selection + spread override
# ---------------------------------------------------------------------------

_TODAY = date(2026, 8, 6)
_PEP_CANDIDATES = [date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18)]


def test_monthly_preferred_within_band():
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY)
    assert choice is not None
    assert choice.expiration == date(2026, 9, 18)
    assert choice.kind == "monthly"
    assert choice.note is None
    assert choice.changed is True  # legacy would have picked Sep 11 (36 DTE)


def test_no_monthly_in_band_falls_back_to_nearest():
    cands = [date(2026, 9, 4), date(2026, 9, 11)]
    choice = prefer_monthly_expiration(cands, target_dte=35, band=14, today=_TODAY)
    assert choice is not None
    assert choice.expiration == date(2026, 9, 11)  # nearest to target, legacy
    assert choice.kind == "weekly"
    assert choice.changed is False


def test_no_candidate_in_band_returns_none():
    assert prefer_monthly_expiration(
        [date(2026, 12, 18)], target_dte=35, band=14, today=_TODAY) is None


def test_spread_override_keeps_tighter_weekly_with_note():
    """Tie-break (b): weekly wins ONLY when its measured relative spread is
    under half the monthly's — and the choice carries the measured reason."""
    spreads = {"2026-09-11": 0.03, "2026-09-18": 0.08}
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY,
        spreads_by_exp=spreads, spread_override_pct=0.5)
    assert choice is not None
    assert choice.expiration == date(2026, 9, 11)
    assert choice.kind == "weekly"
    assert choice.note == "weekly kept — spread 3% vs monthly 8%"


def test_spread_override_not_triggered_when_weekly_not_materially_tighter():
    spreads = {"2026-09-11": 0.05, "2026-09-18": 0.08}  # 5% is NOT < 4%
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY,
        spreads_by_exp=spreads, spread_override_pct=0.5)
    assert choice.expiration == date(2026, 9, 18)
    assert choice.kind == "monthly"
    assert choice.note is None


def test_spread_override_requires_both_measurements():
    """Missing either measured spread → monthly wins (fail closed on the
    override, rule #10 — never infer a spread advantage from missing data)."""
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY,
        spreads_by_exp={"2026-09-11": 0.03})
    assert choice.expiration == date(2026, 9, 18)
    assert choice.kind == "monthly"


# ---------------------------------------------------------------------------
# (c) The PEP case — the observed 2026-08-10 briefing defect
# ---------------------------------------------------------------------------

def test_pep_case_monthly_chosen_and_labeled():
    """Observed 2026-08-10 briefing (PEP PULLBACK CSP): the ticket rendered
    'Sep 11 '26' — a 2nd-Friday weekly quoted at 'bid $0.14 / ask $0.45'
    (~107% relative spread) — while the Sep 18 standard monthly, where
    institutional OI/liquidity concentrates, sat inside the same DTE band.
    With the policy on, Sep 18 is chosen and labeled '(monthly)'."""
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY)
    assert choice.expiration == date(2026, 9, 18)
    pretty = choice.expiration.strftime("%a %b %d '%y")
    assert label_exp(pretty, choice.expiration) == "Fri Sep 18 '26 (monthly)"
    assert kind_suffix(choice.expiration) == ", monthly"


def test_pep_case_new_ideas_selector_prefers_monthly():
    """The actual PEP surface: steps/new_ideas._next_monthly_expiration.
    Legacy picked the expiration nearest 35 DTE (Sep 11); with
    prefer_monthly=True the Sep 18 monthly in the same band wins."""
    got = _next_monthly_expiration(
        list(_PEP_CANDIDATES), prefer_monthly=True, today=_TODAY)
    assert got == date(2026, 9, 18)


def test_new_ideas_selector_falls_back_when_no_monthly_qualifies():
    got = _next_monthly_expiration(
        [date(2026, 9, 4), date(2026, 9, 11)], prefer_monthly=True, today=_TODAY)
    assert got == date(2026, 9, 11)


# ---------------------------------------------------------------------------
# (d) tenor cap — the monthly preference NEVER violates max_dte
# ---------------------------------------------------------------------------

def test_monthly_preference_never_violates_tenor_cap():
    """A monthly beyond max_dte is excluded even though it's the only monthly;
    the selection stays inside the cap."""
    today = date(2026, 8, 6)
    # Sep 18 monthly = 43 DTE; cap at 40 → monthly ineligible.
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=today, max_dte=40)
    assert choice is not None
    assert choice.expiration == date(2026, 9, 11)  # weekly inside the cap
    assert (choice.expiration - today).days <= 40
    assert choice.kind == "weekly"


def test_tenor_cap_with_monthly_inside_cap_still_prefers_monthly():
    choice = prefer_monthly_expiration(
        _PEP_CANDIDATES, target_dte=35, band=14, today=_TODAY, max_dte=45)
    assert choice.expiration == date(2026, 9, 18)  # 43 DTE ≤ 45 cap


# ---------------------------------------------------------------------------
# (e) config off → byte-identical legacy selection
# ---------------------------------------------------------------------------

def _load_fetcher():
    p = (Path(__file__).resolve().parents[3]
         / "etrade-chain-fetcher" / "scripts" / "fetch.py")
    spec = importlib.util.spec_from_file_location("etrade_chain_fetcher_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass decorator resolves the module
    spec.loader.exec_module(mod)
    return mod


def test_config_off_choose_expiration_is_legacy_identical(monkeypatch):
    """prefer_monthly=False (the default / config off) → the canonical
    chain-fetcher selection is unchanged: nearest-to-target with the Friday
    preference — the PEP-style Sep 11 weekly, exactly as before."""
    mod = _load_fetcher()
    today = date.today()
    monthly = _nearby_monthly(today)
    weekly = monthly - timedelta(days=7)  # 2nd Friday — never a monthly
    exps = [weekly - timedelta(days=7), weekly, monthly]
    target_dte = (weekly - today).days  # legacy nearest-to-target → the weekly
    monkeypatch.setattr(mod, "list_expirations", lambda *a, **k: exps)
    legacy = mod.choose_expiration(symbol="PEP", target_dte=target_dte,
                                   tolerance_days=14)
    assert legacy == weekly
    # Same call with the policy ON flips to the monthly in the same band.
    preferred = mod.choose_expiration(symbol="PEP", target_dte=target_dte,
                                      tolerance_days=14, prefer_monthly=True)
    assert preferred == monthly


def test_policy_enabled_reads_config_block():
    assert policy_enabled({"expiration_policy": {"prefer_monthly": True}}) is True
    assert policy_enabled({"expiration_policy": {"prefer_monthly": False}}) is False
    assert policy_enabled({}) is False
    assert policy_enabled(None) is False


def test_config_off_new_ideas_selector_legacy_identical():
    got = _next_monthly_expiration(list(_PEP_CANDIDATES), today=_TODAY)
    assert got == date(2026, 9, 11)  # nearest 35 DTE — legacy behavior


def test_labels_suppressed_when_policy_disabled():
    """Disabled → NO kind labels anywhere (byte-identical legacy rendering)."""
    assert kind_suffix("2026-09-18", enabled=False) == ""
    assert label_exp("Fri Sep 18 '26", "2026-09-18", enabled=False) == "Fri Sep 18 '26"


# ---------------------------------------------------------------------------
# (f) monthly opex-week line — final week only, informational only
# ---------------------------------------------------------------------------

def test_opex_week_line_renders_in_final_week_only():
    exp = date(2026, 9, 18)  # monthly
    # 8 days out — not yet the final week.
    assert opex_week_line(exp, today=date(2026, 9, 10)) is None
    # Final week (7 days out through expiration day) — line renders.
    assert opex_week_line(exp, today=date(2026, 9, 11)) == OPEX_WEEK_NOTE
    assert opex_week_line(exp, today=date(2026, 9, 16)) == OPEX_WEEK_NOTE
    assert opex_week_line(exp, today=date(2026, 9, 18)) == OPEX_WEEK_NOTE
    # After expiration — never.
    assert opex_week_line(exp, today=date(2026, 9, 19)) is None


def test_opex_week_line_never_fires_for_weeklies():
    assert is_monthly_opex_week(date(2026, 9, 11), today=date(2026, 9, 8)) is False
    assert opex_week_line("2026-09-11", today=date(2026, 9, 8)) is None


def test_opex_note_is_non_directional():
    """Rule #9: mechanics language only — no prediction verbs."""
    lowered = OPEX_WEEK_NOTE.lower()
    for banned in ("will ", "predict", "expect", "drop", "rally", "target"):
        assert banned not in lowered
    assert "pin/assignment mechanics" in OPEX_WEEK_NOTE


def test_watch_panel_gets_opex_line_only_in_final_week(monkeypatch):
    """Watch panel (render_watch_with_commentary): a monthly-expiring short
    put in its final week carries the ⏰ line; the same position weeks out
    does not; policy off → never; weeklies → never."""
    from analysis import expiration_policy as ep
    from steps import per_option_commentary as poc

    class _FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 14)  # Monday of Sep '26 monthly opex week

    monkeypatch.setattr(ep, "date", _FakeDate)

    def _render(exp_iso: str, enabled: bool = True):
        review = {
            "contract": "PEP_PUT", "recommendation": "HOLD", "type": "PUT",
            "strike": 140.0, "expiration": exp_iso,
            "days_to_expiry": 4, "current_mid": 0.2, "entry_price": 1.0,
            "qty": -1, "underlying": "PEP", "matrix_cell_id": "DEFAULT_HOLD",
        }
        snap = {"_config": {"expiration_policy": {"prefer_monthly": enabled}},
                "technicals": {}, "quotes": {}}
        return "\n".join(poc.render_watch_with_commentary([], [review], snap))

    # Sep 18 '26 monthly, 4 days out → final week → line renders.
    assert OPEX_WEEK_NOTE in _render("2026-09-18")
    # Policy off → never (byte-identical legacy Watch panel).
    assert OPEX_WEEK_NOTE not in _render("2026-09-18", enabled=False)
    # Oct 16 '26 monthly, a month out → not the final week.
    assert OPEX_WEEK_NOTE not in _render("2026-10-16")
    # A weekly in its final week never carries the monthly-opex line.
    assert OPEX_WEEK_NOTE not in _render("2026-09-16")


# ---------------------------------------------------------------------------
# (g) roll STO legs — monthly preferred within tenor cap + labeled
# ---------------------------------------------------------------------------

def _wheel_roll_target():
    p = (Path(__file__).resolve().parents[3]
         / "wheel-roll-advisor" / "scripts" / "roll_target.py")
    spec = importlib.util.spec_from_file_location("wheel_roll_target_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nearby_monthly(today: date) -> date:
    """First 3rd-Friday monthly whose DTE lands in [15, 50] — one always
    exists (successive monthlies are 28-35 days apart, the window is wider)."""
    probe = today
    for _ in range(4):
        tf = third_friday(probe.year, probe.month)
        if 15 <= (tf - today).days <= 50:
            return tf
        probe = (probe.replace(day=1) + timedelta(days=32)).replace(day=1)
    raise AssertionError("no nearby monthly found — fixture bug")


def _roll_chain(today: date):
    """Chain fixture: current exp near-dated, a weekly 'next' one week before
    the nearby monthly, the monthly itself, and later expirations — each with
    a priced short-call row at one strike."""
    monthly = _nearby_monthly(today)
    weekly_next = monthly - timedelta(days=7)  # 2nd Friday — never a monthly
    assert not is_monthly(weekly_next)
    exps = sorted({
        today + timedelta(days=3),   # current (near-dated weekly)
        weekly_next,                 # legacy next (weekly)
        monthly,                     # nearby standard monthly
        monthly + timedelta(days=28),
        monthly + timedelta(days=56),
    })
    cands = []
    for e in exps:
        cands.append({
            "expirationDate": e.isoformat(),
            "strikePrice": 100.0,
            "bid": 1.0, "ask": 1.2,
            "daysToExpiry": (e - today).days,
            "delta": 0.25,
        })
    return exps, {"candidates": cands}


def test_roll_sto_leg_prefers_monthly_within_cap():
    """enumerate_roll_candidates with expiration_policy on snaps the OUT-leg
    target to the nearby standard monthly among the chain's REAL expirations."""
    today = date.today()
    exps, chain = _roll_chain(today)
    mod = _wheel_roll_target()
    position = {
        "daysToExpiry": 3, "strikePrice": 100.0, "entryPrice": 2.0,
        "currentMid": 1.5, "ivRank": 50, "underlyingPrice": 110.0,
        "quantity": 1, "optionType": "CALL",
        "expirationDate": exps[0].isoformat(),
    }
    legacy = mod.enumerate_roll_candidates(position, chain, {})
    legacy_next = [c for c in legacy if c.instruction]
    assert legacy_next, "fixture must produce at least one priced candidate"
    # Legacy same_next picks the immediate next listing (a weekly).
    assert legacy_next[0].instruction["sell_expiration"] == exps[1].isoformat()

    # Skip the CURRENT expiration (exps[0]) when picking the expected snap
    # target: when today+3 itself lands on a 3rd Friday (observed
    # 2026-08-18 → current exp Aug 21 IS a monthly), the first monthly in
    # the chain is the leg being closed, not the OUT-leg target — production
    # correctly snapped to the NEXT monthly and the old picker flagged it.
    monthly_iso = next(e for e in exps[1:]
                       if mod._is_monthly_expiration(e.isoformat()))
    preferred = mod.enumerate_roll_candidates(
        position, chain,
        {"expiration_policy": {"prefer_monthly": True},
         "monthly_snap_max_tenor_days": 120})
    pref_next = [c for c in preferred if c.instruction]
    assert pref_next[0].instruction["sell_expiration"] == monthly_iso.isoformat()


def test_roll_sto_monthly_snap_respects_tenor_cap():
    """A tenor cap tighter than the monthly's extension vetoes the snap —
    the monthly preference NEVER violates max_action_tenor_days."""
    today = date.today()
    exps, chain = _roll_chain(today)
    mod = _wheel_roll_target()
    # Same current-exp skip as above — on a date where exps[0] is itself a
    # 3rd Friday the old picker made monthly_ext 0 and the cap negative.
    monthly_iso = next(e for e in exps[1:]
                       if mod._is_monthly_expiration(e.isoformat()))
    monthly_ext = (monthly_iso - today).days - 3  # extension over current DTE 3
    position = {
        "daysToExpiry": 3, "strikePrice": 100.0, "entryPrice": 2.0,
        "currentMid": 1.5, "ivRank": 50, "underlyingPrice": 110.0,
        "quantity": 1, "optionType": "CALL",
        "expirationDate": exps[0].isoformat(),
    }
    cap = monthly_ext - 5  # tighter than the monthly, looser than the weekly
    capped = mod.enumerate_roll_candidates(
        position, chain,
        {"expiration_policy": {"prefer_monthly": True},
         "monthly_snap_max_tenor_days": cap})
    capped_next = [c for c in capped if c.instruction]
    # The snap is vetoed — the same_next candidate keeps the in-cap weekly.
    assert capped_next[0].instruction["sell_expiration"] == exps[1].isoformat()


def test_pullback_csp_selector_prefers_monthly(monkeypatch):
    """PULLBACK CSP surface (adapters.etrade_market.find_put_strike_near):
    with the policy on, a standard monthly inside the 25-40 DTE band beats
    the DTE-nearest weekly; off → legacy selection and NO exp_kind."""
    from types import SimpleNamespace

    from adapters import etrade_market as em

    today = date.today()
    monthly = _nearby_monthly(today)
    weekly = monthly - timedelta(days=7)
    m_dte = (monthly - today).days
    band_min, band_max = m_dte - 10, m_dte + 2  # mid = m-4 → weekly is nearer

    monkeypatch.setattr(em, "get_option_expirations",
                        lambda *a, **k: [weekly, monthly])
    row = SimpleNamespace(strike=90.0, bid=1.0, ask=1.1, delta=-0.2)
    monkeypatch.setattr(em, "get_option_chain",
                        lambda *a, **k: {"put": [row]})

    legacy = em.find_put_strike_near(
        "PEP", target_otm_pct=10.0, target_dte_min=band_min,
        target_dte_max=band_max, spot=100.0)
    assert legacy["expiration"] == weekly.isoformat()
    assert "exp_kind" not in legacy

    preferred = em.find_put_strike_near(
        "PEP", target_otm_pct=10.0, target_dte_min=band_min,
        target_dte_max=band_max, spot=100.0, prefer_monthly=True)
    assert preferred["expiration"] == monthly.isoformat()
    assert preferred["exp_kind"] == "monthly"


def test_roll_sto_leg_label_helper():
    """(g) roll STO legs labeled: the block #3/#4 renderers pass the selected
    expiration through label_exp — monthly gets '(monthly)', weekly '(weekly)',
    and a disabled policy leaves the string untouched."""
    assert label_exp("Fri Sep 18 '26", "2026-09-18") == "Fri Sep 18 '26 (monthly)"
    assert label_exp("Fri Sep 11 '26", "2026-09-11") == "Fri Sep 11 '26 (weekly)"
    assert label_exp("Fri Sep 11 '26", "2026-09-11", enabled=False) == "Fri Sep 11 '26"
