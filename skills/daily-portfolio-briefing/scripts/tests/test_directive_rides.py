"""Directive rides (2026-08-26 MELI split brain).

Observed on the real 2026-08-26 briefing: George's MELI override (filed
2026-08-25 — momentum ride, exits GTC 75%/85%, red-day or RSI-70 stall,
over-cap accepted) was honored by Fable ("Per your directive, I'm tracking
the exits, not re-litigating the close") while the ACTION LIST still
rendered:

    2. **CLOSE** MELI_PUT_1460_20270617 — +34% ($+3,435); buy-to-close
    limit $68.93 · RSI 60 🟡 extended ⏳ IGNORED 3 DAYS

and the Money Plan still banked it:

    - **Bank today:** 1 close(s) → $+3,435 realized (MELI $1460P)

Root cause: the override was written only to state/fable_advisor_memory.md
— the pipeline's canonical directive loader (steps/load_directives.py ←
state/directives/index.yaml) never received it.

These tests pin the whole contract: (a) the canonical directives file
carries the MELI directive in machine-readable form and load_directives
parses the format; (b) the review_options directive short-circuit
suppresses the recommendation-class close AND carries the position's real
economics + the directive payload; (c) the action list renders the
directive as '🏇 RIDING (directive) — exits armed: …' with MEASURED
values, never the CLOSE; (d) a red-day -2.3% fixture renders STALL
TRIGGER FIRED as the action item; (e) the ride is never banked in the
Money Plan; (f) the exit-condition parsing is generic (any directive with
rsi_stall / red_day_pct / gtc_capture gets tracked); (g) unmeasured
conditions never fire and never fabricate (rule #19).
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import directive_rides as dr  # noqa: E402
from analysis.net_option_cash import (  # noqa: E402
    compute_net_option_cash,
    demoted_close_idents,
)
from render.panels import render_action_list  # noqa: E402
from steps.load_directives import load_directives  # noqa: E402
from steps.review_options import review_options  # noqa: E402

TODAY = "2026-08-26"
_IDENT = "MELI_PUT_1460_20270617"
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "state" / "directives" / "index.yaml")


def _meli_directive():
    return {
        "id": "meli_put_1460_momentum_ride",
        "status": "ACTIVE",
        "type": "OVERRIDE",
        "target": {"identifier": _IDENT},
        "filed": "2026-08-25",
        "reason": ("George 2026-08-25 — momentum ride on the MELI $1460P; "
                   "exits armed."),
        "exit_conditions": {
            "gtc_capture": 0.75, "gtc_limit_price": 24.60,
            "gtc_stretch_capture": 0.85, "red_day_pct": -2.0,
            "rsi_stall": 70, "max_capture": 0.85,
            "earnings_min_capture": 0.75,
        },
    }


def _meli_review(directive=None):
    """The enriched review the directive short-circuit emits — MELI
    economics from the real 2026-08-26 positions snapshot (entry $100.00,
    mid $65.65 → +34% captured, the observed '$+3,435')."""
    d = directive if directive is not None else _meli_directive()
    return {
        "underlying": "MELI", "contract": _IDENT, "type": "PUT",
        "qty": -1.0, "strike": 1460.0, "expiration": "2027-06-17",
        "current_mid": 65.65, "entry_price": 100.0, "days_to_expiry": 295,
        "recommendation": "OVERRIDE",
        "rationale": "User directive momentum ride",
        "matrix_cell_id": "DIRECTIVE_OVERRIDE",
        "directive": d, "_directive_ride": True,
    }


def _snap(day_move_pct=-0.9, rsi=60.0, spot_ref=2007.0):
    live = round(spot_ref * (1 + day_move_pct / 100.0), 2)
    return {
        "quotes": {"MELI": {"last": live}},
        "technicals": {"MELI": {"spot": spot_ref, "rsi_14": rsi}},
        "positions": [], "chains": {}, "iv_ranks": {},
        "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": []},
        "balance": {"accountValue": 1_087_287, "cash": 71_760},
    }


def _render(rev, snap):
    return render_action_list([], [rev], [], None, snap, date_str=TODAY)


# ── (a) canonical file + loader ────────────────────────────────────────────

def test_canonical_directives_file_carries_meli_override():
    """The MELI directive (George 2026-08-25) lives in the CANONICAL
    pipeline location — state/directives/index.yaml — not just Fable
    memory, with machine-readable exit conditions."""
    data = yaml.safe_load(_CANONICAL.read_text())
    ds = data.get("directives") or []
    meli = [d for d in ds
            if (d.get("target") or {}).get("identifier") == _IDENT]
    assert len(meli) == 1
    d = meli[0]
    assert d["status"] == "ACTIVE"
    assert d["type"] in ("OVERRIDE", "DEFER")
    ec = d["exit_conditions"]
    assert ec["gtc_capture"] == 0.75
    assert ec["gtc_limit_price"] == 24.60
    assert ec["red_day_pct"] == -2.0
    assert ec["rsi_stall"] == 70
    assert ec["max_capture"] == 0.85


def test_load_directives_parses_the_canonical_format(tmp_path, monkeypatch):
    """load_directives (Step 1.5) reads state/directives/index.yaml
    relative to the run cwd and returns ACTIVE directives with the
    exit_conditions block intact."""
    monkeypatch.chdir(tmp_path)
    ddir = tmp_path / "state" / "directives"
    ddir.mkdir(parents=True)
    (ddir / "index.yaml").write_text(
        yaml.safe_dump({"directives": [_meli_directive()]}))
    active, expired = load_directives(tmp_path / "snap")
    assert len(active) == 1 and expired == []
    assert active[0]["target"]["identifier"] == _IDENT
    assert active[0]["exit_conditions"]["red_day_pct"] == -2.0


# ── (b) review_options short-circuit ───────────────────────────────────────

def test_review_options_short_circuit_carries_economics_and_flag(tmp_path):
    """The directive short-circuit must carry the position's REAL
    economics (entry $100.00 / mid $65.65 — the observed '+34% ($+3,435)')
    plus the directive payload and the _directive_ride Money-Plan
    demotion flag — never call the advisor, never emit a CLOSE."""
    pos = {
        "symbol": _IDENT, "assetType": "OPTION", "underlying": "MELI",
        "type": "PUT", "strike": 1460.0, "expiration": "2027-06-17",
        "qty": -1.0, "premiumReceived": 100.0, "currentMid": 65.65,
    }
    snapshot = {"positions": [pos], "quotes": {}, "iv_ranks": {},
                "chains": {}, "open_orders": []}
    reviews = review_options(snapshot, {}, [_meli_directive()], tmp_path)
    assert len(reviews) == 1
    r = reviews[0]
    assert r["recommendation"] == "OVERRIDE"
    assert r["matrix_cell_id"] == "DIRECTIVE_OVERRIDE"
    assert r["entry_price"] == 100.0
    assert r["current_mid"] == 65.65
    assert r["_directive_ride"] is True
    assert r["directive"]["exit_conditions"]["rsi_stall"] == 70


# ── (c) RIDING render with measured exits ──────────────────────────────────

def test_riding_render_replaces_close_with_measured_exits():
    """Observed: '2. **CLOSE** MELI_PUT_1460_20270617 — +34% ($+3,435) …
    ⏳ IGNORED 3 DAYS' rendered while the directive stood. The action list
    must render '🏇 RIDING (directive)' tracking the directive's OWN exits
    with measured values — never the CLOSE."""
    lines = _render(_meli_review(), _snap(day_move_pct=-0.9, rsi=60.0))
    md = "\n".join(lines)
    assert "🏇 **RIDING (directive)** " + _IDENT in md
    assert "exits armed: GTC@75% $24.60" in md
    assert "stall on red day ≤-2% or RSI ≥ 70" in md
    # Measured no-trigger read (rule #19 — real values, stated).
    assert "MELI -0.9%" in md
    assert "RSI 60" in md
    assert "no exit trigger fired" in md
    assert f"**CLOSE** {_IDENT}" not in md
    assert "IGNORED" not in md


def test_riding_render_shows_directive_provenance():
    """The RIDING card carries the directive's own reason text so the
    override is auditable (rule #24)."""
    md = "\n".join(_render(_meli_review(), _snap()))
    assert "**Directive:**" in md
    assert "momentum ride" in md


# ── (d) stall fires loudly — the exit line IS the action ───────────────────

def test_red_day_stall_fires_as_the_action_item():
    """Today's -2.3% MELI day fires the red-day stall (≤ -2.0% per the
    directive) — the render must say so LOUDLY and make the exit the
    action item, with the buyback ticket at the measured mid."""
    lines = _render(_meli_review(), _snap(day_move_pct=-2.3, rsi=60.0))
    md = "\n".join(lines)
    assert "STALL TRIGGER FIRED, exit per directive" in md
    assert "MELI -2.3%" in md
    assert "🏇 **EXIT — STALL FIRED (directive)** " + _IDENT in md
    assert "BUY TO CLOSE 1× MELI $1460P" in md
    assert "mid $65.65" in md
    assert f"**CLOSE** {_IDENT}" not in md


def test_rsi_stall_fires_too():
    """The generic RSI stall: MELI RSI 72 ≥ 70 fires even on a green
    day."""
    md = "\n".join(_render(_meli_review(),
                           _snap(day_move_pct=1.0, rsi=72.0)))
    assert "STALL TRIGGER FIRED" in md
    assert "RSI 72 ≥ 70" in md


# ── (e) never banked while riding ──────────────────────────────────────────

def test_ride_is_never_banked_in_money_plan():
    """Observed: '- **Bank today:** 1 close(s) → $+3,435 realized (MELI
    $1460P)' banked a directive-ridden close. The _directive_ride flag
    demotes the contract out of every bank/buyback surface, and the
    rendered action list carries no CLOSE block to bank."""
    rev = _meli_review()
    assert _IDENT in demoted_close_idents([rev])
    lines = _render(rev, _snap(day_move_pct=-0.9, rsi=60.0))
    noc = compute_net_option_cash(lines, [rev])
    assert all(_IDENT not in (c.get("label") or "")
               for c in noc.get("components") or [])


def test_stalled_exit_is_not_a_recommendation_class_close():
    """Even when the stall fires, the exit item is the DIRECTIVE's exit
    (kind 'EXIT — STALL FIRED'), not a recommendation-class CLOSE that
    the aging ledger would tick as ignored."""
    lines = _render(_meli_review(), _snap(day_move_pct=-2.3, rsi=60.0))
    md = "\n".join(lines)
    assert f"**CLOSE** {_IDENT}" not in md


# ── (f) generic machine-readable exit-condition parsing ────────────────────

def test_exit_conditions_normalize_percent_and_fraction():
    """gtc_capture 75 and 0.75 both mean 75% — any position directive
    with a structured block gets tracked, not just MELI's."""
    a = dr.exit_conditions({"exit_conditions": {"gtc_capture": 75,
                                                "rsi_stall": 70}})
    b = dr.exit_conditions({"exit_conditions": {"gtc_capture": 0.75}})
    assert a["gtc_capture"] == 0.75 and a["rsi_stall"] == 70.0
    assert b["gtc_capture"] == 0.75
    assert dr.exit_conditions({"exit_conditions": {}}) is None
    assert dr.exit_conditions({}) is None


def test_generic_max_capture_and_earnings_conditions_fire():
    """max_capture and earnings_min_capture are tracked like the stalls."""
    d = {"exit_conditions": {"max_capture": 0.85}}
    r = dr.evaluate_ride(d, ticker="XYZ", capture_pct=86.0)
    assert r["stalled"] and "85% hard cap" in r["fired"][0]
    d2 = {"exit_conditions": {"earnings_min_capture": 0.75}}
    r2 = dr.evaluate_ride(d2, ticker="XYZ", capture_pct=80.0,
                          days_to_earnings=3)
    assert r2["stalled"] and "earnings in 3d" in r2["fired"][0]
    r3 = dr.evaluate_ride(d2, ticker="XYZ", capture_pct=50.0,
                          days_to_earnings=3)
    assert not r3["stalled"]


# ── (g) unmeasured never fires, never fabricates (rule #19) ────────────────

def test_unmeasured_conditions_never_fire_and_render_honestly():
    """A missing quote never fires (or suppresses) a stall — the line
    says 'exits unmeasured this cycle', never a fabricated day move."""
    d = _meli_directive()
    r = dr.evaluate_ride(d, ticker="MELI")
    assert not r["stalled"] and r["fired"] == []
    assert "unmeasured this cycle" in r["today_line"]
    # measured_day_move_pct with no reference close → None, not 0.
    assert dr.measured_day_move_pct("MELI", {"quotes": {
        "MELI": {"last": 2007.0}}, "technicals": {}}) is None
