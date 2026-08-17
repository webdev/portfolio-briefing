"""Expiration policy — prefer standard monthly (3rd-Friday) expirations.

George (2026-08-10): "Institutions Trade Monthly Options on the 3rd Friday of
each month. We must incorporate this. This is a better time to trade options."

The incorporation is an expiration PREFERENCE, not opex-day timing folklore
(rule #9 — no directional claims): institutional open interest and liquidity
concentrate on the standard monthly (3rd-Friday) expirations, which means
tighter spreads, better fills, and easier rolls. Evidence from the real
2026-08-10 briefing: the PEP ticket chose Sep 11 '26 (a 2nd-Friday weekly)
with bid $0.14 / ask $0.45 — a 107% relative spread — while the Sep 18
monthly sat inside the same DTE band.

Single source of truth for:
- ``third_friday(year, month)`` — the standard monthly expiration date
- ``is_monthly(exp)`` — is this date an equity/ETF standard monthly?
  (Some products use different monthly conventions — VIX is Wednesdays,
  some futures options differ. This pipeline trades equity/ETF options
  only, where the standard monthly is the 3rd Friday.)
- ``prefer_monthly_expiration(...)`` — pick the monthly among the chain's
  REAL listed expirations within a DTE band (rule #6: a preference among
  chain-listed dates, never a synthesized date)
- kind labels ("monthly" / "weekly") for rendered tickets (rule #19:
  labels only from computed dates, never assumed)
- monthly-opex-week awareness for the Watch panel (informational,
  non-directional — mechanics note, no prediction)

Config (briefing.yaml)::

    expiration_policy:
      prefer_monthly: true
      spread_override_pct: 0.5

Disabled (``prefer_monthly: false`` or block absent) → every wired selector
falls back to byte-identical legacy selection and NO kind labels render.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# A weekly only overrides the monthly preference when its measured relative
# spread is materially tighter: weekly_spread < override_pct * monthly_spread.
DEFAULT_SPREAD_OVERRIDE_PCT = 0.5

# Informational, non-directional (rule #9): mechanics note, no prediction.
OPEX_WEEK_NOTE = (
    "⏰ monthly opex week — elevated pin/assignment mechanics near the "
    "strike; plan exits early in the week."
)


# ---------------------------------------------------------------------------
# Date math
# ---------------------------------------------------------------------------

def third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of (year, month) — the standard monthly expiration."""
    first = date(year, month, 1)
    days_to_first_friday = (4 - first.weekday()) % 7  # Friday = weekday 4
    return first + timedelta(days=days_to_first_friday + 14)


def _to_date(exp) -> date | None:
    """Parse a date / datetime / ISO string into a date. None on failure."""
    if isinstance(exp, datetime):
        return exp.date()
    if isinstance(exp, date):
        return exp
    try:
        return date.fromisoformat(str(exp)[:10])
    except (ValueError, TypeError):
        return None


def _good_friday(year: int) -> date:
    """Good Friday (Easter − 2 days), Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1) - timedelta(days=2)


def _is_market_holiday_friday(d: date) -> bool:
    """True when Friday ``d`` is a US market closure.

    The ONLY market holidays that can land on a 3rd Friday (day 15-21):
      - Good Friday (varies; e.g. Fri Apr 19 '30);
      - Juneteenth — Jun 19 itself when it falls on the Friday, or the
        observed Fri Jun 18 when Jun 19 is a Saturday (e.g. Fri Jun 18
        '27, which shifts the June 2027 monthly to Thu Jun 17 '27).
    July 4/Christmas/New Year never fall in the 15-21 window; the Monday
    and Thursday holidays never fall on a Friday."""
    if d == _good_friday(d.year):
        return True
    if d.month == 6 and (
            d.day == 19
            or (d.day == 18 and date(d.year, 6, 19).weekday() == 5)):
        return True
    return False


def is_holiday_shifted_monthly(exp) -> bool:
    """True iff ``exp`` is the Thursday immediately before a 3rd Friday
    that is a US market holiday — the holiday-shifted standard monthly.

    When the 3rd Friday is a market closure (Juneteenth-observed or Good
    Friday — see :func:`_is_market_holiday_friday`), the exchanges list
    that month's monthly on the preceding Thursday. Observed bug
    (2026-08-17 briefing): "Sell-to-Open 1× $180P Thu Jun 17 '27 (weekly)"
    — Fri Jun 18 '27 is the Juneteenth closure, so Thu Jun 17 '27 IS the
    June 2027 monthly, never a weekly. A Thursday before a NON-holiday
    3rd Friday stays a weekly (no such listing exists on real chains, but
    synthetic dates must not be misclassified)."""
    d = _to_date(exp)
    if d is None or d.weekday() != 3:  # Thursday = weekday 3
        return False
    friday = d + timedelta(days=1)
    return (friday == third_friday(d.year, d.month)
            and _is_market_holiday_friday(friday))


def is_monthly(exp) -> bool:
    """True iff ``exp`` is a standard equity/ETF monthly (3rd-Friday)
    expiration — including the holiday-shifted Thursday variant (see
    :func:`is_holiday_shifted_monthly`).

    Note: only equity/ETF standard monthlies are classified — products with
    different monthly conventions (VIX Wednesdays etc.) are out of scope;
    this pipeline trades equity/ETF options only.
    """
    d = _to_date(exp)
    if d is None:
        return False
    return d == third_friday(d.year, d.month) or is_holiday_shifted_monthly(d)


def expiration_kind(exp) -> str | None:
    """"monthly" / "monthly, holiday-shifted" / "weekly" for a parseable
    expiration, None otherwise. A holiday-shifted monthly must NEVER label
    "weekly" (rule #19 — the label is computed from the real date)."""
    d = _to_date(exp)
    if d is None:
        return None
    if is_holiday_shifted_monthly(d):
        return "monthly, holiday-shifted"
    return "monthly" if is_monthly(d) else "weekly"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def policy_config(params: dict | None) -> dict:
    """Normalize the ``expiration_policy`` block from briefing.yaml params."""
    cfg = ((params or {}).get("expiration_policy") or {})
    return {
        "prefer_monthly": bool(cfg.get("prefer_monthly", False)),
        "spread_override_pct": float(
            cfg.get("spread_override_pct", DEFAULT_SPREAD_OVERRIDE_PCT)
        ),
    }


def policy_enabled(params: dict | None) -> bool:
    """True when the monthly-preference policy is switched on in config."""
    return policy_config(params)["prefer_monthly"]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

@dataclass
class ExpirationChoice:
    """Result of a monthly-preference selection among REAL chain expirations."""
    expiration: date
    kind: str                 # "monthly" | "weekly"
    note: str | None = None   # spread-override note, or None
    changed: bool = False     # True when the preference changed the legacy pick


def _spread_for(spreads_by_exp: dict | None, d: date) -> float | None:
    """Look up a measured relative spread by date or ISO-string key."""
    if not spreads_by_exp:
        return None
    for key in (d, d.isoformat()):
        if key in spreads_by_exp:
            try:
                return float(spreads_by_exp[key])
            except (TypeError, ValueError):
                return None
    return None


def prefer_monthly_expiration(
    candidates,
    target_dte: int,
    band: int = 14,
    spreads_by_exp: dict | None = None,
    spread_override_pct: float = DEFAULT_SPREAD_OVERRIDE_PCT,
    today: date | None = None,
    max_dte: int | None = None,
) -> ExpirationChoice | None:
    """Pick an expiration from the chain's REAL listed dates within a DTE band.

    Tie-break rules:
      (a) a monthly (3rd-Friday) within the band wins by default;
      (b) if measured relative spreads are provided for BOTH the monthly and
          the legacy (nearest-to-target) weekly, and the weekly's spread is
          materially tighter (< ``spread_override_pct`` × the monthly's),
          the weekly wins WITH a note;
      (c) no monthly in band → nearest-to-target (legacy behavior), labeled
          by its actual kind.

    ``max_dte`` is a hard tenor cap (e.g. ``roll.max_action_tenor_days``) —
    the monthly preference NEVER selects past it. Candidates are always the
    chain's real expirations (rule #6) — nothing is synthesized here.

    Returns None when no candidate falls inside the band (caller keeps its
    legacy no-expiration handling).
    """
    today = today or date.today()
    parsed: list[date] = []
    for c in candidates or []:
        d = _to_date(c)
        if d is None or d < today:
            continue
        dte = (d - today).days
        if max_dte is not None and dte > max_dte:
            continue
        if abs(dte - target_dte) > band:
            continue
        if d not in parsed:
            parsed.append(d)
    if not parsed:
        return None

    target = today + timedelta(days=target_dte)

    def _distance(d: date) -> tuple:
        # Nearest to target; tie → Friday preferred, then earlier date
        # (mirrors the legacy chain-fetcher scoring).
        return (abs((d - target).days), 0 if d.weekday() == 4 else 1, d)

    legacy = min(parsed, key=_distance)
    monthlies = [d for d in parsed if is_monthly(d)]
    if not monthlies:
        return ExpirationChoice(
            expiration=legacy, kind=expiration_kind(legacy) or "weekly",
            note=None, changed=False,
        )

    monthly_pick = min(monthlies, key=_distance)
    if monthly_pick == legacy:
        return ExpirationChoice(
            expiration=monthly_pick,
            kind=expiration_kind(monthly_pick) or "monthly")

    # (b) measured-spread override: keep the weekly only when BOTH spreads
    # are measured and the weekly is materially tighter.
    m_spread = _spread_for(spreads_by_exp, monthly_pick)
    w_spread = _spread_for(spreads_by_exp, legacy)
    if (
        m_spread is not None and w_spread is not None
        and m_spread > 0
        and w_spread < spread_override_pct * m_spread
        and not is_monthly(legacy)
    ):
        return ExpirationChoice(
            expiration=legacy,
            kind=expiration_kind(legacy) or "weekly",
            note=(
                f"weekly kept — spread {w_spread * 100:.0f}% "
                f"vs monthly {m_spread * 100:.0f}%"
            ),
            changed=False,
        )

    return ExpirationChoice(
        expiration=monthly_pick,
        kind=expiration_kind(monthly_pick) or "monthly", changed=True)


# ---------------------------------------------------------------------------
# Labels (rule #19 — computed from the real selected date, never assumed)
# ---------------------------------------------------------------------------

def kind_suffix(exp, enabled: bool = True) -> str:
    """Return ", monthly" / ", weekly" for insertion inside a "(N DTE)" paren.

    Empty string when the policy is disabled or the date is unparseable —
    disabled config renders byte-identical legacy output.
    """
    if not enabled:
        return ""
    kind = expiration_kind(exp)
    return f", {kind}" if kind else ""


def label_exp(exp_pretty: str, exp, enabled: bool = True) -> str:
    """Append " (monthly)" / " (weekly)" to an already-formatted expiration.

    e.g. "Fri Sep 18 '26" → "Fri Sep 18 '26 (monthly)". No-op when disabled
    or unparseable (never a guessed label).
    """
    if not enabled:
        return exp_pretty
    kind = expiration_kind(exp)
    return f"{exp_pretty} ({kind})" if kind else exp_pretty


# ---------------------------------------------------------------------------
# Monthly opex-week awareness (informational, rule #9-safe)
# ---------------------------------------------------------------------------

def is_monthly_opex_week(exp, today: date | None = None) -> bool:
    """True when ``exp`` is a monthly AND today falls in its final week.

    Final week = the 7 calendar days up to and including expiration.
    """
    d = _to_date(exp)
    if d is None or not is_monthly(d):
        return False
    today = today or date.today()
    return 0 <= (d - today).days <= 7


def opex_week_line(exp, today: date | None = None) -> str | None:
    """The Watch-panel note for a position in its monthly opex week, or None."""
    if is_monthly_opex_week(exp, today=today):
        return OPEX_WEEK_NOTE
    return None
