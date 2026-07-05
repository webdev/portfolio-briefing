"""Pydantic V1/V2 models for the briefing JSON.

Forward coercion: older snapshots (pre-2026-06-30) don't have the
`actions` array. Default to [] so V1 documents validate as V2.

All fields are deliberately permissive (`default=None` / `default_factory=list`)
because the briefing JSON evolves and we never want a missing optional
field to crash a route. Hard rule #19: fail closed (render n/a, never
fabricate) — not the same as raise.
"""

from __future__ import annotations

from typing import Any
from datetime import date as _date

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Base(BaseModel):
    """Permissive base — allow extra keys silently so unknown fields don't break us."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EquityReview(_Base):
    ticker: str
    price: float | None = None
    qty: float | None = None
    market_value: float | None = None
    weight: float | None = None
    pl_pct: float | None = None
    thesis_status: str | None = None
    technical_status: str | None = None
    recommendation: str | None = None
    rationale: str | None = None
    third_party_rec: str | None = None
    matrix_cell_id: str | None = None


class RollCandidate(_Base):
    id: str | None = None
    description: str | None = None
    closeCost: float | None = None
    newCredit: float | None = None
    netDollars: float | None = None
    dteExtension: int | None = None
    notes: str | None = None
    current_strike: float | None = None


class OptionsReview(_Base):
    underlying: str
    contract: str | None = None
    type: str | None = None
    qty: float | None = None
    strike: float | None = None
    expiration: str | None = None
    current_mid: float | None = None
    entry_price: float | None = None
    days_to_expiry: int | None = None
    iv_rank: float | None = None
    recommendation: str | None = None
    rationale: str | None = None
    matrix_cell_id: str | None = None
    roll_target: dict[str, Any] | None = None
    roll_candidates: list[RollCandidate] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)


class NewIdea(_Base):
    ticker: str | None = None
    name: str | None = None
    source: str | None = None
    instruction: Any | None = None
    rationale: str | None = None
    capacity_blocked: bool | None = None


class Opportunity(_Base):
    kind: str | None = None
    ticker: str | None = None
    trigger_reasons: list[str] = Field(default_factory=list)
    concrete_trade: str | None = None
    rationale: str | None = None
    yield_or_cost: str | None = None
    source: str | None = None


class StrategyUpgrade(_Base):
    type: str | None = None
    underlying: str | None = None
    tier: str | None = None
    shares_held: int | None = None
    current_price: float | None = None
    current_weight_pct: float | None = None
    rationale: str | None = None


class ConsistencyReport(_Base):
    total_positions: int | None = None
    recommendations_unchanged: int | None = None
    recommendations_changed: int | None = None
    changes_with_trigger: int | None = None
    inconsistencies_found: int | None = None
    note: str | None = None


class ActionItem(_Base):
    """The 2026-06-30+ action-list entry. Older snapshots lack this array."""
    key: str | None = None
    kind: str | None = None
    ident: str | None = None
    summary: str | None = None
    first_flagged: str | None = None
    days_flagged: int | None = None
    recon_status: str | None = None


class Briefing(_Base):
    """Top-level briefing model. V1 + V2 unified."""

    date: _date
    regime: str | None = None
    nlv: float | None = None
    cash: float | None = None
    snapshot_dir: str | None = None
    directives_active_count: int | None = None
    directives_expired_count: int | None = None
    equity_reviews: list[EquityReview] = Field(default_factory=list)
    options_reviews: list[OptionsReview] = Field(default_factory=list)
    new_ideas: list[NewIdea] = Field(default_factory=list)
    long_term_opportunities: list[Opportunity] = Field(default_factory=list)
    strategy_upgrades: list[StrategyUpgrade] = Field(default_factory=list)
    consistency_report: ConsistencyReport | None = None
    # V2 forward-coercion: default to [] if missing.
    actions: list[ActionItem] = Field(default_factory=list)
    # Rotation Advisor output (V3+ pipeline). Untyped dict since the structure
    # is nested and stable enough to consume as-is; the web app renders from it.
    # Empty dict when the briefing predates rotation-advisor.
    rotation_opportunities: dict[str, Any] = Field(default_factory=dict)
    # Task #16 — benchmark tracking + P/L attribution:
    # {"benchmark": BenchmarkReport.to_dict(), "attribution":
    #  AttributionReport.to_dict()}. Empty dict when the briefing predates
    # the feature or the panel was unavailable that cycle.
    benchmark_report: dict[str, Any] = Field(default_factory=dict)

    @field_validator("date", mode="before")
    @classmethod
    def _coerce_date(cls, v: Any) -> Any:
        """Accept either an ISO string or a date object."""
        if isinstance(v, str):
            return v[:10]   # YYYY-MM-DD
        return v

    # Derived convenience properties — used by templates and route helpers
    @property
    def cash_pct(self) -> float | None:
        if self.cash is None or not self.nlv:
            return None
        try:
            return float(self.cash) / float(self.nlv)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def top_holdings(self, n: int = 5) -> list[EquityReview]:
        """Equity reviews sorted by market_value descending, capped to n."""
        rows = [
            r for r in self.equity_reviews
            if (r.market_value or 0) > 0
        ]
        rows.sort(key=lambda r: (r.market_value or 0), reverse=True)
        return rows[:n]

    def actionable_count(self) -> int:
        """How many entries in the action list (V2). 0 for V1 briefings."""
        return len(self.actions)


def load_briefing(raw: dict[str, Any]) -> Briefing:
    """Coerce a raw JSON dict (potentially V1 shape) into Briefing.

    Principle (architecture doc §3.4): history is upgraded forward, never
    the reverse. Missing optional fields default to None / [].
    """
    # Defensive defaults for V1 → V2 forward compatibility
    raw = dict(raw)  # don't mutate caller
    raw.setdefault("actions", [])
    raw.setdefault("equity_reviews", [])
    raw.setdefault("options_reviews", [])
    raw.setdefault("new_ideas", [])
    raw.setdefault("long_term_opportunities", [])
    raw.setdefault("strategy_upgrades", [])
    return Briefing.model_validate(raw)
