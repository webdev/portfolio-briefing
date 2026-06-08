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
