"""Long-term-verdict discipline gate (CLAUDE.md hard rule #39).

Origin: analyst audit 2026-07-03 (task #14). The pipeline computes
``long_term_verdict`` for every ticker (technical_indicators.long_term_verdict,
attached at ``snapshot["technicals"][tk]["deep"]["long_term_verdict"]``) but
never consumed it when generating new-open recommendations — RSI band + a
Parkev BUY was enough to fire. That let ZS (LT `broken`, -28% below a falling
200-SMA, -56% drawdown) ship BOTH a $135P LONG_DATED_CSP and a $5K ADD, and
SOFI (LT `broken`) ship an ADD.

The one rule: block (demote, never hide) any NEW-OPEN recommendation — CSP,
ADD, BUY, sub-lot — when the name's ``long_term_verdict`` is in
{broken, downtrend, weakening} AND spot sits below the 200-SMA.

Override: a FRESH high-conviction third-party catalyst — Parkev rec with
``rating_tier >= 4`` AND ``age_days <= 14`` AND a BUY variant — overrides the
gate (the META case: LT `downtrend` but tier-5 STRONG_BUY 2d old). Overridden
recs still carry an LT-warning annotation so the trend contradiction is
visible, never silent.

Fail-open on missing tech data: a rec is never blocked because we don't have
chart data for the name (consistent with "missing data → no fabricated
conclusion"; the block requires a MEASURED broken chart).

Source of truth for wiring: long_term_opportunities.py (LT_CSP / ADD),
render/panels.py (PULLBACK CSP), strategy_upgrades.py (sub-lot completion +
the secular-uptrend covered-call wait).
"""

from __future__ import annotations


# LT verdicts that block new opens when spot is below the 200-SMA.
BLOCKED_VERDICTS = {"broken", "downtrend", "weakening"}

# Fresh strong-conviction override thresholds (rule #39).
OVERRIDE_MIN_TIER = 4
OVERRIDE_MAX_AGE_DAYS = 14
_BUY_VARIANTS = {"BUY", "STRONG_BUY", "WEAK_BUY"}


def _tech_for(ticker: str, snapshot_data: dict) -> dict:
    """Resolve the per-ticker technicals dict from a full snapshot OR a bare
    technicals map. Returns {} when nothing is found (fail-open upstream)."""
    if not isinstance(snapshot_data, dict):
        return {}
    technicals = snapshot_data.get("technicals")
    if not isinstance(technicals, dict):
        # Caller may have passed the technicals map directly.
        technicals = snapshot_data
    tk = (ticker or "").upper()
    tech = technicals.get(tk) or technicals.get(ticker) or {}
    return tech if isinstance(tech, dict) else {}


def _is_fresh_strong_buy(parkev_rec: dict | None) -> tuple[bool, str]:
    """True when the rec qualifies for the tier ≥4 + ≤14d fresh-BUY override.

    Returns (qualifies, describe) where describe is a short human phrase used
    in the reason/warning text either way.
    """
    if not isinstance(parkev_rec, dict):
        return False, "no third-party rec"
    try:
        tier = int(parkev_rec.get("rating_tier")) if parkev_rec.get("rating_tier") is not None else None
    except (TypeError, ValueError):
        tier = None
    try:
        age = int(parkev_rec.get("age_days")) if parkev_rec.get("age_days") is not None else None
    except (TypeError, ValueError):
        age = None
    rec = str(parkev_rec.get("recommendation") or "").upper()
    describe = f"tier {tier if tier is not None else '?'}, {age if age is not None else '?'}d, {rec or 'no rating'}"
    if rec not in _BUY_VARIANTS:
        return False, describe
    if tier is None or tier < OVERRIDE_MIN_TIER:
        return False, describe
    if age is None or age > OVERRIDE_MAX_AGE_DAYS:
        return False, describe
    return True, describe


def check_lt_verdict_gate(
    ticker: str,
    snapshot_data: dict,
    parkev_rec: dict | None = None,
) -> dict:
    """Gate a NEW-OPEN recommendation on the name's long-term verdict.

    Args:
        ticker: underlying symbol.
        snapshot_data: full briefing snapshot (uses
            ``technicals[tk]["deep"]["long_term_verdict"]``) — a bare
            technicals map is also accepted.
        parkev_rec: the full third-party rec dict for this ticker
            (``rating_tier`` / ``age_days`` / ``recommendation``), or None.

    Returns a dict:
        {"pass": bool,          # True → rec may proceed
         "reason": str | None,  # populated when pass is False
         "verdict": str | None, # the LT verdict consulted (None = no data)
         "override": bool,      # True when a fresh tier ≥4 BUY overrode the block
         "warning": str | None} # LT-warning annotation for overridden recs

    Fail-open: missing deep tech / verdict / 200-SMA data → pass (never block
    a rec because we don't have data).
    """
    result = {"pass": True, "reason": None, "verdict": None,
              "override": False, "warning": None}

    tech = _tech_for(ticker, snapshot_data)
    deep = tech.get("deep")
    if not isinstance(deep, dict):
        return result  # no deep read → fail-open

    verdict = deep.get("long_term_verdict")
    result["verdict"] = verdict
    if verdict not in BLOCKED_VERDICTS:
        return result

    # Spot vs 200-SMA: prefer the deep read's measured vs_sma200_pct, then
    # a direct spot/sma_200 comparison. Missing both → fail-open.
    vs_200 = deep.get("vs_sma200_pct")
    if vs_200 is None:
        spot = deep.get("spot") or tech.get("spot")
        sma_200 = deep.get("sma_200") or tech.get("sma_200")
        try:
            if spot is not None and sma_200 is not None and float(sma_200) > 0:
                vs_200 = (float(spot) - float(sma_200)) / float(sma_200) * 100.0
        except (TypeError, ValueError):
            vs_200 = None
    if vs_200 is None:
        return result  # can't measure the 200-SMA relationship → fail-open
    if float(vs_200) >= 0:
        return result  # spot reclaimed the 200-SMA → trend condition not met

    # Blocked chart. Check the fresh tier ≥4 BUY override.
    qualifies, describe = _is_fresh_strong_buy(parkev_rec)
    tk = (ticker or "").upper()
    if qualifies:
        result["override"] = True
        result["warning"] = (
            f"LT verdict `{verdict}` ({float(vs_200):+.1f}% vs 200-SMA) — "
            f"kept only via fresh high-conviction override ({describe}); "
            f"the trend contradicts the catalyst"
        )
        return result

    result["pass"] = False
    result["reason"] = (
        f"LT verdict `{verdict}` with spot {float(vs_200):+.1f}% vs a "
        f"200-SMA it sits below — no new opens on a broken/downtrending "
        f"chart (rule #39). Override requires a fresh (≤{OVERRIDE_MAX_AGE_DAYS}d) "
        f"tier ≥{OVERRIDE_MIN_TIER} BUY; {tk} has {describe}. Revisit when "
        f"spot reclaims the 200-SMA or the catalyst refreshes."
    )
    return result


def cc_secular_uptrend_wait(ticker: str, snapshot_data: dict) -> str | None:
    """Belt-and-suspenders CC check: writing a NEW covered call on a name in a
    measured LT ``secular-uptrend`` caps a compounder (audit 2026-07-03 noted
    Tier-A no-CC already covers the configured core; this catches non-Tier-A
    names whose chart says 'compounder' even without the config entry).

    Returns a one-line reason string when the name should be demoted to the
    wait list, else None. Fail-open on missing data.
    """
    tech = _tech_for(ticker, snapshot_data)
    deep = tech.get("deep")
    if not isinstance(deep, dict):
        return None
    if deep.get("long_term_verdict") != "secular-uptrend":
        return None
    tk = (ticker or "").upper()
    return (
        f"{tk} LT verdict `secular-uptrend` — capping a measured compounder "
        f"for near-dated premium fights the trend (rule #39). Write only into "
        f"genuine extension, or leave uncapped."
    )
