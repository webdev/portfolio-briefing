"""Support / Resistance discipline — single source of truth for how
horizontal price levels are computed, scored, and rendered alongside the RSI
discipline.

The trader reads S/R *asymmetrically*, mirroring the RSI discipline:

  Support → buy / CSP side. Sell puts AT support, not at a flat %OTM. Anchor
  equity BUY entries to the nearest support cluster below spot.

  Resistance → covered-call / trim / exit side. Write CCs AT or just above
  the next resistance. Trim/EXIT triggers tighten when price tags a major
  resistance with stretched RSI.

S/R is computed from real OHLC fetched this cycle. Two sources:

  1. **Classic pivot points** — deterministic math from prior period H/L/C.
     Computed daily, weekly, and monthly. Every chartist knows these.

  2. **Swing-point clusters** — local maxima/minima over a 180d lookback,
     grouped into clusters within ``cluster_pct`` of each other, scored by
     touch count + recency.

After candidate levels are generated, each is *confluence-checked* against the
50-SMA, 200-SMA, 52w high/low, and the dominant fib retracement levels (38.2%,
50%, 61.8%). Confluence boosts the strength score — the levels chartists
actually watch are the ones with multiple things pointing at them.

S/R **refines but never overrides** the RSI gate. A name at support with RSI
75 is still RSI-blocked from new put-sales / equity buys (hard rule #11). The
gate only loosens when BOTH agree.

Fail-closed (hard rule #10): if OHLC is missing or insufficient, no S/R is
surfaced for that name — never a fabricated level.

Bands are config-overridable via ``briefing.yaml`` ``support_resistance``.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, asdict
from typing import Iterable, Sequence

import pandas as pd

# Source tags for Level.source — pinned so the audit + tests can be precise.
SRC_SWING = "swing"
SRC_PIVOT_D = "pivot_d"
SRC_PIVOT_W = "pivot_w"
SRC_PIVOT_M = "pivot_m"
SRC_SMA50 = "sma_50"
SRC_SMA200 = "sma_200"
SRC_HIGH_52W = "52w_high"
SRC_LOW_52W = "52w_low"
SRC_FIB = "fib"

SUPPORT = "support"
RESISTANCE = "resistance"

# Defaults — the "standard S/R discipline".
DEFAULT_CONFIG = {
    "enabled": True,
    "lookback_days": 180,
    "swing_window": 5,          # bars on each side for swing detection
    "cluster_pct": 0.020,       # 2% — levels within this band merge
    "confluence_pct": 0.020,    # 2% — distance to an MA / 52w / fib that boosts strength
    "min_touches": 2,           # swing clusters need ≥2 touches to count
    "min_strength": 1.5,        # rendering threshold (touches + confluence bonus)
    "max_levels_each_side": 2,  # render at most N supports / N resistances
    "anchor_proximity_pct": 0.03,  # strike selection anchors when within 3% of a cluster
    # Data-artifact sanity bounds (rule #43, 2026-08-14: SNDK rendered
    # "S: $44.4 (52w low, 1 touch)" on a $1,625 stock — a post-spinoff /
    # short-history OHLC artifact). A SINGLE-TOUCH 52w-extreme-only level
    # with NO other confluence is dropped when it sits below this fraction
    # of spot (supports) / above this multiple of spot (resistances).
    # Multi-touch or confluent levels are NEVER dropped (fail-open).
    "artifact_support_min_pct_of_spot": 0.50,
    "artifact_resistance_max_pct_of_spot": 2.00,
    "recency_weights": {
        "30d": 1.0,
        "90d": 0.6,
        "180d": 0.3,
    },
    "confluence_bonus": 1.0,    # added to strength per confluence hit
}


def load_config(config: dict | None) -> dict:
    """Merge the user's ``support_resistance`` config over the defaults."""
    merged = copy.deepcopy(DEFAULT_CONFIG)
    if not config:
        return merged
    user = config.get("support_resistance") or {}
    for k, v in user.items():
        if k == "recency_weights" and isinstance(v, dict):
            merged["recency_weights"].update({kk: float(vv) for kk, vv in v.items()})
        elif k in merged and v is not None:
            if isinstance(merged[k], bool):
                merged[k] = bool(v)
            elif isinstance(merged[k], int) and not isinstance(merged[k], bool):
                merged[k] = int(v)
            else:
                merged[k] = type(merged[k])(v)
    return merged


@dataclass
class Level:
    """One horizontal price level — support or resistance.

    ``strength`` is the score used both for rendering selection and for
    deciding whether a level is "real" enough to anchor a strike against.
    It's the sum of:
      - recency-weighted touch count (1 touch in past 30d = 1.0, in past 180d = 0.3)
      - confluence bonuses (each SMA/52w/fib alignment adds ``confluence_bonus``)
    """

    price: float
    side: str             # SUPPORT | RESISTANCE
    source: str           # SRC_* — primary source
    touches: int = 1
    age_days: int | None = None
    strength: float = 1.0
    confluence: list[str] = field(default_factory=list)

    def with_confluence(self, source: str, bonus: float) -> "Level":
        if source in self.confluence:
            return self
        self.confluence.append(source)
        self.strength = round(self.strength + bonus, 2)
        return self

    def label(self) -> str:
        """Compact human label for rendering: '$247 (200-SMA + Apr swing, 3 touches)'."""
        bits: list[str] = []
        # primary source label
        primary = _source_label(self.source)
        if primary:
            bits.append(primary)
        # confluence sources — skip any that match the primary (so we never
        # render "sma_50 + sma_50" when the source IS the SMA).
        seen_sources = {self.source}
        for conf in self.confluence:
            if conf in seen_sources:
                continue
            seen_sources.add(conf)
            lbl = _source_label(conf)
            if lbl and lbl not in bits:
                bits.append(lbl)
        if self.touches >= 2:
            bits.append(f"{self.touches} touches")
        elif self.touches == 1:
            bits.append("1 touch")
        suffix = ", ".join(bits)
        return f"${_fmt_price(self.price)}" + (f" ({suffix})" if suffix else "")


def _source_label(source: str) -> str:
    return {
        SRC_SWING: "swing",
        SRC_PIVOT_D: "daily pivot",
        SRC_PIVOT_W: "weekly pivot",
        SRC_PIVOT_M: "monthly pivot",
        SRC_SMA50: "50-SMA",
        SRC_SMA200: "200-SMA",
        SRC_HIGH_52W: "52w high",
        SRC_LOW_52W: "52w low",
        SRC_FIB: "fib",
    }.get(source, source)


def _fmt_price(p: float) -> str:
    """Trim trailing zeros, but keep sub-$10 prices precise to 2 decimals."""
    if p < 10:
        return f"{p:.2f}"
    if p < 100:
        return f"{p:.2f}".rstrip("0").rstrip(".")
    return f"{p:.0f}"


@dataclass
class SupportResistance:
    """All S/R data computed for one ticker on one cycle."""

    spot: float
    supports: list[Level] = field(default_factory=list)        # filtered + sorted
    resistances: list[Level] = field(default_factory=list)     # filtered + sorted
    all_levels: list[Level] = field(default_factory=list)      # raw clustered levels
    pivots: dict[str, dict[str, float]] = field(default_factory=dict)
    confidence: str = "low"                                    # low / medium / high
    note: str | None = None                                    # populated when fail-closed

    def to_dict(self) -> dict:
        return {
            "spot": self.spot,
            "supports": [asdict(level) for level in self.supports],
            "resistances": [asdict(level) for level in self.resistances],
            "pivots": self.pivots,
            "confidence": self.confidence,
            "note": self.note,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Pivot points (classic)
# ─────────────────────────────────────────────────────────────────────────────

def compute_pivots(hist: pd.DataFrame, *, period: str = "daily") -> dict[str, float] | None:
    """Classic pivot points for the prior period.

    ``hist`` must be a DataFrame with columns ``High``, ``Low``, ``Close`` and
    a DatetimeIndex. ``period`` is one of ``daily``, ``weekly``, ``monthly``.

    Returns ``{P, R1, R2, R3, S1, S2, S3}`` or ``None`` when there's not enough
    history to slice a complete prior period.
    """
    if hist is None or hist.empty or len(hist) < 2:
        return None
    if not {"High", "Low", "Close"}.issubset(hist.columns):
        return None

    df = hist.dropna(subset=["High", "Low", "Close"])
    if df.empty:
        return None

    if period == "daily":
        # Prior trading day = second-to-last row
        if len(df) < 2:
            return None
        prior = df.iloc[-2]
        h, l, c = float(prior["High"]), float(prior["Low"]), float(prior["Close"])
    elif period == "weekly":
        weekly = df.resample("W").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(weekly) < 2:
            return None
        prior = weekly.iloc[-2]
        h, l, c = float(prior["High"]), float(prior["Low"]), float(prior["Close"])
    elif period == "monthly":
        monthly = df.resample("ME").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(monthly) < 2:
            return None
        prior = monthly.iloc[-2]
        h, l, c = float(prior["High"]), float(prior["Low"]), float(prior["Close"])
    else:
        return None

    p = (h + l + c) / 3.0
    rng = h - l
    return {
        "P": round(p, 2),
        "R1": round(2 * p - l, 2),
        "S1": round(2 * p - h, 2),
        "R2": round(p + rng, 2),
        "S2": round(p - rng, 2),
        "R3": round(h + 2 * (p - l), 2),
        "S3": round(l - 2 * (h - p), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Swing-point detection + clustering
# ─────────────────────────────────────────────────────────────────────────────

def find_swing_points(
    hist: pd.DataFrame, *, window: int = 5, lookback_days: int = 180
) -> tuple[list[tuple[pd.Timestamp, float]], list[tuple[pd.Timestamp, float]]]:
    """Find swing highs and swing lows over the lookback window.

    A swing high at bar ``i`` is one where ``high[i]`` is strictly greater than
    ``high[i-window:i]`` AND ``high[i+1:i+window+1]``. Symmetric for lows.

    Returns ``(swing_highs, swing_lows)`` — each a list of ``(timestamp, price)``.
    """
    if hist is None or hist.empty or window < 1:
        return [], []
    if not {"High", "Low"}.issubset(hist.columns):
        return [], []

    df = hist.dropna(subset=["High", "Low"]).tail(lookback_days)
    if len(df) < window * 2 + 1:
        return [], []

    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    idx = df.index

    swing_highs: list[tuple[pd.Timestamp, float]] = []
    swing_lows: list[tuple[pd.Timestamp, float]] = []

    n = len(df)
    for i in range(window, n - window):
        h = highs[i]
        l = lows[i]
        left_h = highs[i - window:i]
        right_h = highs[i + 1:i + 1 + window]
        if h > left_h.max() and h > right_h.max():
            swing_highs.append((idx[i], float(h)))
        left_l = lows[i - window:i]
        right_l = lows[i + 1:i + 1 + window]
        if l < left_l.min() and l < right_l.min():
            swing_lows.append((idx[i], float(l)))

    return swing_highs, swing_lows


def cluster_levels(
    points: list[tuple[pd.Timestamp, float]],
    *,
    side: str,
    cluster_pct: float,
    recency_weights: dict[str, float],
    now: pd.Timestamp,
) -> list[Level]:
    """Cluster nearby swing points into ``Level`` objects with strength scores.

    Two points belong in the same cluster when their prices are within
    ``cluster_pct`` of each other. Strength = sum of recency-weighted touches.
    """
    if not points:
        return []

    # Sort points by price ascending so cluster traversal is linear.
    sorted_pts = sorted(points, key=lambda x: x[1])

    clusters: list[list[tuple[pd.Timestamp, float]]] = []
    for ts, price in sorted_pts:
        if clusters and abs(price - clusters[-1][-1][1]) / max(clusters[-1][-1][1], 1e-9) <= cluster_pct:
            clusters[-1].append((ts, price))
        else:
            clusters.append([(ts, price)])

    levels: list[Level] = []
    for cluster in clusters:
        if not cluster:
            continue
        # Cluster price = mean of constituent prices (could weight by recency).
        prices = [p for _, p in cluster]
        cluster_price = sum(prices) / len(prices)
        # Strength via recency weights.
        strength = 0.0
        most_recent_age: int | None = None
        for ts, _ in cluster:
            age_days = (now - ts).days if isinstance(now, pd.Timestamp) and isinstance(ts, pd.Timestamp) else None
            if age_days is None or age_days < 0:
                w = recency_weights.get("30d", 1.0)
                age_days = 0
            elif age_days <= 30:
                w = recency_weights.get("30d", 1.0)
            elif age_days <= 90:
                w = recency_weights.get("90d", 0.6)
            elif age_days <= 180:
                w = recency_weights.get("180d", 0.3)
            else:
                w = 0.0
            strength += w
            if most_recent_age is None or age_days < most_recent_age:
                most_recent_age = age_days

        levels.append(
            Level(
                price=round(cluster_price, 2),
                side=side,
                source=SRC_SWING,
                touches=len(cluster),
                age_days=most_recent_age,
                strength=round(strength, 2),
            )
        )

    return levels


# ─────────────────────────────────────────────────────────────────────────────
# Confluence boost
# ─────────────────────────────────────────────────────────────────────────────

def add_confluence(
    levels: list[Level],
    *,
    spot: float,
    sma_50: float | None,
    sma_200: float | None,
    high_52w: float | None,
    low_52w: float | None,
    confluence_pct: float,
    confluence_bonus: float,
) -> list[Level]:
    """Bump strength on any level within ``confluence_pct`` of an SMA / 52w / fib."""
    references: list[tuple[str, float]] = []
    if sma_50 is not None and sma_50 > 0:
        references.append((SRC_SMA50, float(sma_50)))
    if sma_200 is not None and sma_200 > 0:
        references.append((SRC_SMA200, float(sma_200)))
    if high_52w is not None and high_52w > 0:
        references.append((SRC_HIGH_52W, float(high_52w)))
    if low_52w is not None and low_52w > 0:
        references.append((SRC_LOW_52W, float(low_52w)))

    # Fib retracements of the 52w move
    if high_52w is not None and low_52w is not None and high_52w > low_52w > 0:
        rng = high_52w - low_52w
        for pct, name in ((0.382, "fib_382"), (0.5, "fib_500"), (0.618, "fib_618")):
            references.append((SRC_FIB, round(high_52w - pct * rng, 2)))

    for level in levels:
        for src, ref_price in references:
            if ref_price <= 0:
                continue
            if abs(level.price - ref_price) / ref_price <= confluence_pct:
                level.with_confluence(src, confluence_bonus)

    return levels


def _is_far_extreme_artifact(level: Level, spot: float, cfg: dict) -> bool:
    """True when ``level`` is a single-touch 52w-extreme-only level with no
    other confluence sitting absurdly far from spot — a data artifact
    (post-spinoff / short-history OHLC), not a tradeable level.

    Rule #43 origin (2026-08-14): SNDK's action card rendered
    "S: … $44.4 (52w low, 1 touch)" on a $1,625 stock. Fail-open by
    design: multi-touch levels, swing/pivot/SMA levels, and levels with any
    NON-SELF confluence are never dropped; unresolvable config disables the
    filter entirely (never drops on missing data)."""
    try:
        sup_min = float(cfg.get("artifact_support_min_pct_of_spot", 0.50))
        res_max = float(cfg.get("artifact_resistance_max_pct_of_spot", 2.00))
    except (TypeError, ValueError):
        return False
    if sup_min <= 0 or res_max <= 0 or spot <= 0:
        return False
    if level.touches > 1:
        return False
    if level.source not in (SRC_LOW_52W, SRC_HIGH_52W):
        return False
    # Self-confluence (the 52w reference matching itself) doesn't count.
    if any(c != level.source for c in level.confluence):
        return False
    if level.side == SUPPORT:
        return level.price < spot * sup_min
    return level.price > spot * res_max


def standalone_reference_levels(
    *,
    side: str,
    spot: float,
    sma_50: float | None,
    sma_200: float | None,
    high_52w: float | None,
    low_52w: float | None,
    pivots: dict[str, float] | None,
) -> list[Level]:
    """Synthesize Level objects directly from the reference points (SMAs, pivots,
    52w extremes). Used to surface key levels even when no swing cluster sits
    nearby. Each is filtered to the requested ``side`` of ``spot``."""
    out: list[Level] = []

    def add(source: str, price: float | None) -> None:
        if price is None or price <= 0 or spot <= 0:
            return
        if side == SUPPORT and price >= spot:
            return
        if side == RESISTANCE and price <= spot:
            return
        out.append(
            Level(
                price=round(float(price), 2),
                side=side,
                source=source,
                touches=1,
                strength=1.0,
            )
        )

    add(SRC_SMA50, sma_50)
    add(SRC_SMA200, sma_200)
    if side == SUPPORT:
        add(SRC_LOW_52W, low_52w)
    else:
        add(SRC_HIGH_52W, high_52w)

    if pivots:
        # Daily pivots — surface S1/S2 as supports, R1/R2 as resistances.
        if side == SUPPORT:
            for key in ("S1", "S2"):
                add(SRC_PIVOT_D, pivots.get(key))
        else:
            for key in ("R1", "R2"):
                add(SRC_PIVOT_D, pivots.get(key))

    return out


def merge_levels(levels: list[Level], *, cluster_pct: float) -> list[Level]:
    """Merge same-side levels within ``cluster_pct`` of each other into one.

    The merged level inherits the highest-priority source, the union of
    confluence tags, sum of touches, and the max strength + a small bonus
    for cross-source confirmation.
    """
    if not levels:
        return []
    # Group by side to be safe; the call sites split by side anyway.
    by_side: dict[str, list[Level]] = {}
    for level in levels:
        by_side.setdefault(level.side, []).append(level)
    out: list[Level] = []
    for side, side_levels in by_side.items():
        side_levels.sort(key=lambda lv: lv.price)
        clusters: list[list[Level]] = []
        for level in side_levels:
            if clusters and abs(level.price - clusters[-1][-1].price) / max(clusters[-1][-1].price, 1e-9) <= cluster_pct:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        for cluster in clusters:
            if len(cluster) == 1:
                out.append(cluster[0])
                continue
            # Prefer swing as primary if present (most "real"); else the highest-strength source.
            primary = next((lv for lv in cluster if lv.source == SRC_SWING), max(cluster, key=lambda lv: lv.strength))
            confluence: list[str] = list(primary.confluence)
            for lv in cluster:
                if lv is primary:
                    continue
                if lv.source not in confluence and lv.source != primary.source:
                    confluence.append(lv.source)
                for c in lv.confluence:
                    if c not in confluence:
                        confluence.append(c)
            merged = Level(
                price=round(sum(lv.price for lv in cluster) / len(cluster), 2),
                side=side,
                source=primary.source,
                touches=sum(lv.touches for lv in cluster),
                age_days=min((lv.age_days for lv in cluster if lv.age_days is not None), default=primary.age_days),
                strength=round(max(lv.strength for lv in cluster) + 0.5 * (len(cluster) - 1), 2),
                confluence=confluence,
            )
            out.append(merged)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Top-level orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def compute_sr(
    hist: pd.DataFrame,
    *,
    spot: float,
    sma_50: float | None = None,
    sma_200: float | None = None,
    config: dict | None = None,
    now: pd.Timestamp | None = None,
) -> SupportResistance:
    """Compute the full S/R picture for one ticker from a price-history DataFrame.

    Fail-closed: when ``hist`` is missing, too short, or doesn't have the
    expected columns, returns a SupportResistance with an explanatory ``note``
    and empty levels — never a fabricated one.
    """
    cfg = load_config({"support_resistance": config or {}})
    if not cfg.get("enabled", True):
        return SupportResistance(spot=spot, note="S/R disabled in config")

    if hist is None or len(hist) == 0:
        return SupportResistance(spot=spot, note="no OHLC history available")
    if not {"High", "Low", "Close"}.issubset(hist.columns):
        return SupportResistance(spot=spot, note="OHLC missing High/Low/Close columns")
    if len(hist) < cfg["swing_window"] * 2 + 5:
        return SupportResistance(spot=spot, note="not enough history for swing detection")

    if now is None:
        now = hist.index[-1] if isinstance(hist.index, pd.DatetimeIndex) else pd.Timestamp.utcnow()

    # 1) Pivots
    pivots = {
        "daily": compute_pivots(hist, period="daily") or {},
        "weekly": compute_pivots(hist, period="weekly") or {},
        "monthly": compute_pivots(hist, period="monthly") or {},
    }

    # 2) Swing points → clustered levels (split by side relative to spot)
    swing_highs, swing_lows = find_swing_points(
        hist, window=cfg["swing_window"], lookback_days=cfg["lookback_days"]
    )

    # Filter swing points by whether they're above/below current spot.
    resistance_pts = [(ts, p) for ts, p in swing_highs if p > spot]
    support_pts = [(ts, p) for ts, p in swing_lows if p < spot]

    supports = cluster_levels(
        support_pts,
        side=SUPPORT,
        cluster_pct=cfg["cluster_pct"],
        recency_weights=cfg["recency_weights"],
        now=now,
    )
    resistances = cluster_levels(
        resistance_pts,
        side=RESISTANCE,
        cluster_pct=cfg["cluster_pct"],
        recency_weights=cfg["recency_weights"],
        now=now,
    )

    # Filter clusters that don't meet min_touches.
    min_touches = max(1, int(cfg.get("min_touches", 2)))
    supports = [lv for lv in supports if lv.touches >= min_touches]
    resistances = [lv for lv in resistances if lv.touches >= min_touches]

    # 3) Compute 52w high/low from the hist itself.
    closes = hist["Close"].dropna()
    window_252 = closes.tail(252)
    high_52w = float(window_252.max()) if not window_252.empty else None
    low_52w = float(window_252.min()) if not window_252.empty else None

    # 4) Reference levels (SMA / 52w / pivots) seeded onto the right side.
    daily_p = pivots.get("daily") or {}
    ref_supports = standalone_reference_levels(
        side=SUPPORT, spot=spot,
        sma_50=sma_50, sma_200=sma_200,
        high_52w=high_52w, low_52w=low_52w,
        pivots=daily_p,
    )
    ref_resistances = standalone_reference_levels(
        side=RESISTANCE, spot=spot,
        sma_50=sma_50, sma_200=sma_200,
        high_52w=high_52w, low_52w=low_52w,
        pivots=daily_p,
    )

    # 5) Merge swing clusters with reference levels (confluence happens via merge).
    all_supports = merge_levels(
        supports + ref_supports, cluster_pct=cfg["cluster_pct"]
    )
    all_resistances = merge_levels(
        resistances + ref_resistances, cluster_pct=cfg["cluster_pct"]
    )

    # 6) Confluence boost — bump strength when a level is near an SMA / 52w / fib
    #    (this handles levels that didn't merge but still align).
    add_confluence(
        all_supports,
        spot=spot, sma_50=sma_50, sma_200=sma_200,
        high_52w=high_52w, low_52w=low_52w,
        confluence_pct=cfg["confluence_pct"],
        confluence_bonus=cfg["confluence_bonus"],
    )
    add_confluence(
        all_resistances,
        spot=spot, sma_50=sma_50, sma_200=sma_200,
        high_52w=high_52w, low_52w=low_52w,
        confluence_pct=cfg["confluence_pct"],
        confluence_bonus=cfg["confluence_bonus"],
    )

    # 6b) Data-artifact sanity filter (rule #43, 2026-08-14): drop a
    #     SINGLE-TOUCH 52w-extreme-only level with no other confluence when
    #     it is absurdly far from spot (support < ~50% of spot, resistance
    #     > ~200% of spot) — a post-spinoff/short-history OHLC artifact, not
    #     a level (the SNDK "$44.4 (52w low, 1 touch)" on a $1,625 stock).
    #     Fail-open: multi-touch or genuinely-confluent levels always stay.
    all_supports = [lv for lv in all_supports
                    if not _is_far_extreme_artifact(lv, spot, cfg)]
    all_resistances = [lv for lv in all_resistances
                       if not _is_far_extreme_artifact(lv, spot, cfg)]

    # 7) Filter by strength + side + sort by proximity to spot.
    min_strength = float(cfg.get("min_strength", 1.5))
    supports_kept = sorted(
        [lv for lv in all_supports if lv.strength >= min_strength and lv.price < spot],
        key=lambda lv: spot - lv.price,
    )
    resistances_kept = sorted(
        [lv for lv in all_resistances if lv.strength >= min_strength and lv.price > spot],
        key=lambda lv: lv.price - spot,
    )

    max_each = int(cfg.get("max_levels_each_side", 2))
    supports_kept = supports_kept[:max_each]
    resistances_kept = resistances_kept[:max_each]

    # 8) Confidence rating.
    n_real = sum(1 for lv in supports_kept + resistances_kept if lv.source == SRC_SWING or lv.touches >= 2)
    if n_real >= 3:
        confidence = "high"
    elif n_real >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return SupportResistance(
        spot=spot,
        supports=supports_kept,
        resistances=resistances_kept,
        all_levels=all_supports + all_resistances,
        pivots=pivots,
        confidence=confidence,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rendering + strike-anchoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def format_sr_note(sr: SupportResistance | None, *, prefix: str = "") -> str:
    """One-line annotation for a single-stock card.

    Returns '' if there's nothing to render (fail-closed; never a placeholder).

    Format: ``S: $247 (200-SMA, 3 touches) · $238 (Apr swing) · R: $265 · $278 (52w high)``
    """
    if sr is None or sr.note:
        return ""
    bits: list[str] = []
    if sr.supports:
        s_str = " · ".join(level.label() for level in sr.supports)
        bits.append(f"S: {s_str}")
    if sr.resistances:
        r_str = " · ".join(level.label() for level in sr.resistances)
        bits.append(f"R: {r_str}")
    if not bits:
        return ""
    return prefix + " · ".join(bits)


def nearest_support_in_range(
    sr: SupportResistance | None,
    *,
    min_price: float,
    max_price: float,
    min_strength: float = 1.5,
) -> Level | None:
    """Return the strongest support whose price sits in ``[min_price, max_price]``.

    Used by CSP strike selection: when the delta band yields a strike range,
    if a support cluster sits inside that range we anchor to it.
    """
    if sr is None or not sr.supports:
        return None
    candidates = [lv for lv in sr.supports if min_price <= lv.price <= max_price and lv.strength >= min_strength]
    if not candidates:
        return None
    return max(candidates, key=lambda lv: lv.strength)


def nearest_resistance_in_range(
    sr: SupportResistance | None,
    *,
    min_price: float,
    max_price: float,
    min_strength: float = 1.5,
) -> Level | None:
    """Mirror of :func:`nearest_support_in_range` for covered-call selection."""
    if sr is None or not sr.resistances:
        return None
    candidates = [lv for lv in sr.resistances if min_price <= lv.price <= max_price and lv.strength >= min_strength]
    if not candidates:
        return None
    return max(candidates, key=lambda lv: lv.strength)


# ─────────────────────────────────────────────────────────────────────────────
# Audit pass — verify every actionable equity rec carries an S/R read
# ─────────────────────────────────────────────────────────────────────────────

# Actionable patterns: lines that propose a NEW equity action on a single ticker.
# Mirrors audit_missing_rsi in rsi_discipline.py.
#
# The rendered format varies by surface:
#   ``**Trade:** BUY ~$5,000 of SOFI (~300 shares @ ~$16.64)``  (LT_ADD path)
#   ``**Trade:** TRIM PLTR ~47% of position``                   (LT_TRIM path)
#   ``**Trade:** SELL TSLA — exit position``                    (LT_EXIT path)
# Ticker sits either after ``of`` (BUY) or directly after the verb (TRIM/SELL).
_ACTIONABLE_BUY_PATTERN = re.compile(
    r"^\s*\*\*Trade:\*\*\s*(?:BUY\s+(?:~?\$[\d,]+\s+of\s+)?|TRIM\s+|SELL\s+)([A-Z]{1,6})\b",
    re.MULTILINE,
)

# Watch panel equity line — ``- **AMZN** @ $253.13 — 2.2% (+20.1%) → **HOLD**``.
# Captures the ticker so we can append the S/R note at the end of the line.
_WATCH_EQUITY_PATTERN = re.compile(
    r"^\s*-\s*\*\*([A-Z]{1,6})\*\*\s*@\s*\$[\d,.]+", re.MULTILINE
)

# LT_CSP and PULLBACK CSP — ``**Trade:** SELL 1× AMD $460P exp Fri Aug 21 '26``.
# The ticker is the 2nd word after the size; we capture it.
_CSP_TRADE_PATTERN = re.compile(
    r"^\s*\*\*Trade:\*\*\s*SELL\s+\d+x?×?\s+([A-Z]{1,6})\s+\$[\d.]+P\b",
    re.MULTILINE | re.IGNORECASE,
)

# Candidate Trades surface — ``**🎯 CANDIDATE · `APP` · $576.87** ⚠ RSI caution``.
# We match the header line and capture the backticked ticker.
_CANDIDATE_PATTERN = re.compile(
    r"^\s*\*\*[^*]*CANDIDATE\s*·\s*`([A-Z]{1,6})`",
    re.MULTILINE,
)

# Today's Action List items. Numbered, bold action verb, then the ticker — which
# can appear either as part of an option contract (``AMD_PUT_420_20261218``) or
# as a bare ticker after "Buy Nx" for hedges (``Buy 15× SPY put $718P``).
# Examples:
#   ``1. **CLOSE** AMD_PUT_420_20261218 — +30% ...``
#   ``9. **ROLL_OUT_AND_UP** SOXX_CALL_600_20261016 — roll UP and out``
#   ``10. **HEDGE** Buy 15× SPY put $718P Fri Jul 10 '26 (~$11,349; ...)``
#   ``5. **EXECUTE ROLL** GOOG_CALL_20270917_450 — roll up``
# The lookahead ``(?=[_\s])`` ensures the captured ticker is followed by either
# an underscore (option contract) or whitespace (hedge / bare ticker).
_ACTION_LIST_PATTERN = re.compile(
    r"^\s*\d+\.\s*\*\*(?:CLOSE|ROLL[A-Z_\s]*|EXECUTE[\s_]ROLL|HEDGE|TAKE[\s_]PROFIT|TRIM|EXIT|DEFENSIVE[\s_][A-Z_]+)\*\*"
    r"\s+(?:Buy\s+\d+x?×?\s+)?"
    r"([A-Z]{1,6})(?=[_\s])",
    re.MULTILINE,
)
_SR_NOTE_HINT = re.compile(r"\bS:\s*\$|\bR:\s*\$")


def audit_missing_sr(md: str) -> list[str]:
    """Return actionable equity rec lines that don't carry an S/R read.

    Trader can post-render this list in an "S/R Coverage Check" panel — same
    pattern as ``audit_missing_rsi``. A miss here is a bug, not a gap.
    """
    misses: list[str] = []
    for line in md.splitlines():
        m = _ACTIONABLE_BUY_PATTERN.match(line)
        if not m:
            continue
        # Look at the next 6 lines (the card body) for an S/R note.
        # Since this is called on the full markdown, do a simple windowed scan.
        # (We do the window check in the wrapper below to keep the per-line check tight.)
        misses.append(line.strip())
    return misses


def annotate_actionable_equity_lines(md: str, levels_by_sym: dict[str, SupportResistance | None]) -> str:
    """Append an S/R note to actionable equity rec lines that don't already carry one.

    Operates only on Trade: BUY/TRIM/SELL <TICKER> action headers found in
    Long-Term Opportunities and similar surfaces. Idempotent — won't re-append
    if a note already exists on the line OR on the immediately-following line.
    The note is emitted as a new indented continuation line below the trade
    header (prefixed with ↳) so the level read is visually obvious.
    """
    if not levels_by_sym:
        return md
    raw_lines = md.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        m = _ACTIONABLE_BUY_PATTERN.match(line)
        next_line = raw_lines[i + 1] if i + 1 < len(raw_lines) else ""
        if not m or _SR_NOTE_HINT.search(line) or _SR_NOTE_HINT.search(next_line):
            out_lines.append(line)
            i += 1
            continue
        ticker = m.group(1)
        sr = _coerce(levels_by_sym.get(ticker))
        note = format_sr_note(sr)
        out_lines.append(line)
        if note:
            out_lines.append(f"  ↳ {note}")
        i += 1
    return "\n".join(out_lines)


def _coerce(value) -> SupportResistance | None:
    """Accept either a SupportResistance object, a dict (the to_dict() shape that
    flows through the snapshot JSON), or None. Returns SupportResistance or None.
    """
    if value is None:
        return None
    if isinstance(value, SupportResistance):
        return value
    if isinstance(value, dict):
        try:
            return SupportResistance(
                spot=float(value.get("spot", 0.0)),
                supports=[
                    Level(
                        price=float(lv["price"]),
                        side=lv.get("side", SUPPORT),
                        source=lv.get("source", SRC_SWING),
                        touches=int(lv.get("touches", 1)),
                        age_days=lv.get("age_days"),
                        strength=float(lv.get("strength", 1.0)),
                        confluence=list(lv.get("confluence", [])),
                    )
                    for lv in (value.get("supports") or [])
                ],
                resistances=[
                    Level(
                        price=float(lv["price"]),
                        side=lv.get("side", RESISTANCE),
                        source=lv.get("source", SRC_SWING),
                        touches=int(lv.get("touches", 1)),
                        age_days=lv.get("age_days"),
                        strength=float(lv.get("strength", 1.0)),
                        confluence=list(lv.get("confluence", [])),
                    )
                    for lv in (value.get("resistances") or [])
                ],
                pivots=value.get("pivots") or {},
                confidence=value.get("confidence", "low"),
                note=value.get("note"),
            )
        except Exception:
            return None
    return None


def annotate_briefing(md: str, levels_by_sym: dict[str, SupportResistance | dict | None]) -> str:
    r"""Single-pass orchestrator — emits an S/R note to every surface that
    surfaces a single-stock recommendation:

      - Watch-panel equity headers (``- **TICKER** @ $X``)
      - LT Opportunities ADD/TRIM/SELL action headers (``**Trade:** BUY ... TICKER``)
      - LT_CSP / PULLBACK CSP trade lines (``**Trade:** SELL Nx TICKER $XXX P``)
      - Candidate Trades headers (``**🎯 CANDIDATE · `TICKER` ...``)

    The note is emitted as a NEW indented line immediately below the actionable
    header (prefixed with ``↳`` so the level read stands out visually). Putting
    it on its own line keeps the data from getting lost in long header lines
    that word-wrap or scroll off-screen.

    Idempotent: lines that already carry an S/R hint — either on themselves or
    on the immediately-following line — are left alone.

    ``levels_by_sym`` may map to either ``SupportResistance`` objects or the
    ``to_dict()`` shape that travels through the snapshot JSON.
    """
    if not levels_by_sym:
        return md
    raw_lines = md.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        next_line = raw_lines[i + 1] if i + 1 < len(raw_lines) else ""

        # Skip if the line itself OR the immediately-following continuation
        # already carries an S/R hint (idempotent).
        if _SR_NOTE_HINT.search(line) or _SR_NOTE_HINT.search(next_line):
            out_lines.append(line)
            i += 1
            continue

        ticker: str | None = None
        m = _ACTIONABLE_BUY_PATTERN.match(line)
        if m:
            ticker = m.group(1)
        if ticker is None:
            m = _WATCH_EQUITY_PATTERN.match(line)
            if m:
                ticker = m.group(1)
        if ticker is None:
            m = _CSP_TRADE_PATTERN.match(line)
            if m:
                ticker = m.group(1)
        if ticker is None:
            m = _CANDIDATE_PATTERN.match(line)
            if m:
                ticker = m.group(1)
        if ticker is None:
            m = _ACTION_LIST_PATTERN.match(line)
            if m:
                ticker = m.group(1)

        if ticker is None:
            out_lines.append(line)
            i += 1
            continue

        sr = _coerce(levels_by_sym.get(ticker.upper()))
        note = format_sr_note(sr)
        out_lines.append(line)
        if note:
            out_lines.append(f"  ↳ {note}")
        i += 1
    return "\n".join(out_lines)


def coverage_stats(md: str, levels_by_sym: dict[str, SupportResistance | dict | None]) -> dict:
    """Return ``{"annotated": N, "missing": [lines], "covered_tickers": [...]}`` —
    used by the audit verifier so the briefing surfaces an "S/R Coverage Check"
    panel mirroring the RSI version.

    Recognises the S/R hint on EITHER the actionable line itself OR the
    immediately-following continuation line (since ``annotate_briefing`` now
    emits the note on a separate ``↳ ...`` line below the header).
    """
    annotated = 0
    missing: list[str] = []
    covered: set[str] = set()

    patterns = (
        _ACTIONABLE_BUY_PATTERN, _WATCH_EQUITY_PATTERN, _CSP_TRADE_PATTERN,
        _CANDIDATE_PATTERN, _ACTION_LIST_PATTERN,
    )
    lines = md.splitlines()
    for idx, line in enumerate(lines):
        ticker = None
        for pat in patterns:
            m = pat.match(line)
            if m:
                ticker = m.group(1)
                break
        if ticker is None:
            continue
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        if _SR_NOTE_HINT.search(line) or _SR_NOTE_HINT.search(next_line):
            annotated += 1
            covered.add(ticker.upper())
            continue
        sr = _coerce(levels_by_sym.get(ticker.upper()))
        # If we genuinely have no data for the ticker (fail-closed), that's not
        # a "miss" — it's the fail-closed contract. Only flag when we had data
        # and still didn't annotate.
        if sr is None or sr.note or (not sr.supports and not sr.resistances):
            continue
        missing.append(line.strip())
    return {"annotated": annotated, "missing": missing, "covered_tickers": sorted(covered)}
