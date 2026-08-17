"""Expiration ladder analysis — detect concentration of expirations on single dates.

Two distinct risks live here:

  1. **Cash-secured put obligation on a single Friday.** A 20% market drop in the
     week-of leaves all puts in that bucket assigned simultaneously. Even if the
     book is well-laddered overall, a single fat expiration date is a real
     liquidity event when the drop happens to land there. ``analyze_put_buckets``
     surfaces these with tiered severity (critical / warning / info) so the Red
     Flags panel can highlight them separately from the static stress-coverage
     ratio (which assumes ALL expirations assign at once — a worst-case fantasy
     that overstates real risk on a well-distributed book).

  2. **Generic expiration concentration** (puts + calls + long options) — the
     pre-existing ``analyze_expiration_ladder`` continues to surface this as an
     informational ladder panel.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ExpirationCluster:
    """A date with significant expiration concentration."""
    expiration: date
    contract_count: int
    total_notional: float
    pct_of_nlv: float
    contracts: list[dict] = field(default_factory=list)


@dataclass
class PutBucketCluster:
    """A single expiration date with cash-secured short-put concentration.

    Used by the Red Flags panel to surface dates where simultaneous assignment
    would land a meaningful slice of NLV at once. Severity:

      - ``critical`` (default ≥30% NLV): explicit red flag, surfaced in the
        Risk Alerts section AND the Red Flags & Priorities panel.
      - ``warning`` (default 20-30% NLV): warning in Red Flags, not in Risk Alerts.
      - ``info`` (default 10-20% NLV): informational in the ladder panel, no
        priority flag.
    """
    expiration: date
    days_to_expiry: int | None
    contract_count: int
    total_obligation: float        # cash-secured: strike × qty × 100, summed
    pct_of_nlv: float
    severity: str                  # critical | warning | info
    names: dict = field(default_factory=dict)  # {ticker: obligation}
    contracts: list[dict] = field(default_factory=list)


def analyze_put_buckets(
    positions: list[dict],
    nlv: float,
    *,
    today: date | None = None,
    critical_pct: float = 0.30,
    warning_pct: float = 0.20,
    info_pct: float = 0.10,
) -> list[PutBucketCluster]:
    """Bucket cash-secured short puts by expiration date; tag each with severity.

    A well-laddered book can still hide a single fat Friday. This pass surfaces
    those dates so the Red Flags panel can warn separately from the static
    stress-coverage ratio.

    Returns a list sorted by expiration date. Empty list when no buckets pass
    the ``info_pct`` floor (no concentration worth surfacing).
    """
    if nlv <= 0:
        return []

    def _to_date(v):
        """Coerce expiration to a date object — accepts date or ISO ``YYYY-MM-DD``."""
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                y, m, d = v.split("-")
                return date(int(y), int(m), int(d))
            except (ValueError, IndexError):
                return None
        return None

    buckets: dict[date, dict] = {}
    for p in positions:
        if not p.get("expiration"):
            continue
        # Cash-secured = SHORT PUT only. Short calls cap upside but don't lock cash;
        # long options are not assignment risk.
        if p.get("position_type") != "short_put":
            continue

        exp = _to_date(p.get("expiration"))
        if exp is None:
            continue
        if exp not in buckets:
            buckets[exp] = {"contracts": [], "obligation": 0.0, "names": {}}

        # The briefing's enriched positions use ``qty`` (E*TRADE field); the
        # legacy ``quantity`` is also accepted for back-compat with fixtures /
        # tests that build positions by hand.
        qty_raw = p.get("qty", p.get("quantity", 0)) or 0
        qty = abs(int(qty_raw))
        strike = float(p.get("strike", 0.0) or 0.0)
        obligation = strike * qty * 100.0
        ticker = (p.get("underlying") or p.get("symbol", "").split("_")[0]).upper()

        buckets[exp]["contracts"].append(p)
        buckets[exp]["obligation"] += obligation
        buckets[exp]["names"][ticker] = buckets[exp]["names"].get(ticker, 0.0) + obligation

    out: list[PutBucketCluster] = []
    for exp, data in buckets.items():
        pct = data["obligation"] / nlv
        if pct < info_pct:
            continue
        if pct >= critical_pct:
            severity = "critical"
        elif pct >= warning_pct:
            severity = "warning"
        else:
            severity = "info"
        days = None
        if today is not None and isinstance(exp, date):
            days = (exp - today).days
        out.append(PutBucketCluster(
            expiration=exp,
            days_to_expiry=days,
            contract_count=len(data["contracts"]),
            total_obligation=data["obligation"],
            pct_of_nlv=pct,
            severity=severity,
            names=data["names"],
            contracts=data["contracts"],
        ))
    out.sort(key=lambda b: b.expiration)
    return out


def _bucket_exp_iso(b) -> str | None:
    """ISO date string for a bucket's expiration (PutBucketCluster or dict)."""
    exp = getattr(b, "expiration", None)
    if exp is None and isinstance(b, dict):
        exp = b.get("expiration")
    if isinstance(exp, date):
        return exp.isoformat()
    if isinstance(exp, str) and exp:
        return exp[:10]
    return None


def bucket_pct_by_exp(put_buckets) -> dict[str, float]:
    """{ISO expiration: pct_of_nlv (0-1)} from :func:`analyze_put_buckets`
    output. Used by roll candidate selection to deprioritize a roll's STO
    leg landing on an already-concentrated single Friday (the 2026-08-17
    QCOM bug: the action list rolled INTO the exact Jun 17 '27 date red
    flag #5 warned about). Accepts dataclass or dict shapes; unparseable
    entries are skipped (fail-open, rule #19)."""
    out: dict[str, float] = {}
    for b in put_buckets or []:
        iso = _bucket_exp_iso(b)
        if iso is None:
            continue
        pct = getattr(b, "pct_of_nlv", None)
        if pct is None and isinstance(b, dict):
            pct = b.get("pct_of_nlv")
        try:
            out[iso] = float(pct)
        except (TypeError, ValueError):
            continue
    return out


def roll_into_cluster_warning(
    new_expiration,
    new_strike: float,
    qty: int,
    put_buckets,
    nlv: float,
    *,
    warning_pct: float = 0.20,
) -> str | None:
    """Measured warning when a roll's STO leg lands on a warning/critical
    put bucket — the "Don't: roll multiple positions INTO this date" red
    flag, enforced at the ticket (observed 2026-08-17: action #2 rolled
    QCOM $180P into Thu Jun 17 '27, the $257K / 22.9%-NLV cluster red flag
    #5 flagged the same morning).

    Returns e.g.::

        ⚠ rolls INTO the Jun 17 '27 cluster ($257,000 → $275,000, 24.5%
        NLV) — see the expiration-cluster red flag; prefer an adjacent
        monthly if fills allow

    Every number is measured (rule #19): existing bucket obligation, the
    projected total after adding strike × qty × 100, and the projected
    %-of-NLV. None when the target bucket is below ``warning_pct`` (or
    inputs are unmeasurable — fail-open)."""
    if not put_buckets or not nlv or nlv <= 0:
        return None
    try:
        new_iso = None
        if isinstance(new_expiration, date):
            new_iso = new_expiration.isoformat()
        elif isinstance(new_expiration, str) and new_expiration:
            new_iso = str(new_expiration)[:10]
        if new_iso is None:
            return None
        add = float(new_strike) * abs(int(qty)) * 100.0
    except (TypeError, ValueError):
        return None
    for b in put_buckets or []:
        if _bucket_exp_iso(b) != new_iso:
            continue
        obligation = getattr(b, "total_obligation", None)
        if obligation is None and isinstance(b, dict):
            obligation = b.get("total_obligation")
        pct = getattr(b, "pct_of_nlv", None)
        if pct is None and isinstance(b, dict):
            pct = b.get("pct_of_nlv")
        try:
            obligation = float(obligation)
            pct = float(pct)
        except (TypeError, ValueError):
            return None
        if pct < warning_pct:
            return None
        new_total = obligation + add
        new_pct = new_total / nlv * 100.0
        try:
            from datetime import datetime as _dt
            pretty = _dt.strptime(new_iso, "%Y-%m-%d").strftime("%b %d '%y")
        except (ValueError, TypeError):
            pretty = new_iso
        return (
            f"⚠ rolls INTO the {pretty} cluster "
            f"(${obligation:,.0f} → ${new_total:,.0f}, {new_pct:.1f}% NLV) — "
            f"see the expiration-cluster red flag; prefer an adjacent "
            f"monthly if fills allow"
        )
    return None


def analyze_expiration_ladder(
    positions: list[dict],
    nlv: float,
    concentration_threshold: float = 0.15,
) -> list[ExpirationCluster]:
    """Group option positions by expiration date; flag dates concentrating > 15% NLV."""
    clusters = {}

    for p in positions:
        if not p.get("expiration"):
            continue
        if p.get("position_type") not in ("short_put", "short_call", "long_put", "long_call"):
            continue

        exp = p.get("expiration")
        if exp not in clusters:
            clusters[exp] = {
                "contracts": [],
                "total_notional": 0.0,
            }

        # Notional: for options, strike × qty × 100
        qty = abs(p.get("quantity", 0))
        strike = p.get("strike", 0.0)
        notional = strike * qty * 100.0

        clusters[exp]["contracts"].append(p)
        clusters[exp]["total_notional"] += notional

    # Convert to sorted list of alerts for concentrated dates
    alerts = []
    for exp, data in clusters.items():
        pct = data["total_notional"] / nlv if nlv > 0 else 0.0
        if pct >= concentration_threshold:
            alerts.append(ExpirationCluster(
                expiration=exp,
                contract_count=len(data["contracts"]),
                total_notional=data["total_notional"],
                pct_of_nlv=pct,
                contracts=data["contracts"],
            ))

    alerts.sort(key=lambda a: a.expiration)
    return alerts
