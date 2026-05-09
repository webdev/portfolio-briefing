"""
Broker position reconciler — compares briefing snapshot positions against live
broker truth. Detects mismatches that would cause recommendations to fail or,
worse, succeed in dangerous ways (e.g., creating naked short calls).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import re


@dataclass
class PositionRef:
    """Normalized position reference for comparison."""
    underlying: str
    asset_type: str  # EQUITY or OPTION
    option_type: str = ""  # CALL or PUT (empty for equity)
    strike: float = 0.0
    expiration: str = ""  # ISO YYYY-MM-DD
    qty: float = 0.0
    cost_basis: float = 0.0
    contract_id: str = ""

    def key(self) -> tuple:
        """Comparison key — exact match required on all fields except basis/qty."""
        return (
            self.underlying,
            self.asset_type,
            self.option_type,
            round(self.strike, 2),
            self.expiration,
        )


@dataclass
class Mismatch:
    action: str           # The briefing action that referenced this position
    issue: str            # Human-readable description
    snapshot: str         # What the snapshot says
    broker: str           # What the broker actually has


@dataclass
class ReconciliationResult:
    verified: bool
    mismatches: list = field(default_factory=list)
    missing_at_broker: list = field(default_factory=list)
    missing_in_snapshot: list = field(default_factory=list)
    panel_md: str = ""
    block_actions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _parse_contract_id(contract_id: str) -> dict:
    """
    Parse a contract id like 'GOOG_CALL_450_20270917' into its parts.
    Returns {underlying, type, strike, expiration} or empty dict if unparseable.
    """
    if not contract_id:
        return {}
    parts = contract_id.split("_")
    if len(parts) < 4:
        return {}
    underlying = parts[0]
    option_type = parts[1].upper()
    if option_type not in ("CALL", "PUT"):
        return {}
    # Strike is the third token (or numeric token)
    try:
        strike = float(parts[2])
    except (ValueError, IndexError):
        return {}
    # Expiration is the last token (YYYYMMDD)
    exp_token = parts[-1]
    if len(exp_token) == 8 and exp_token.isdigit():
        expiration = f"{exp_token[0:4]}-{exp_token[4:6]}-{exp_token[6:8]}"
    else:
        expiration = exp_token
    return {
        "underlying": underlying,
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
    }


def _position_to_ref(p: dict) -> PositionRef:
    """Convert a position dict (from snapshot or broker) into a PositionRef."""
    asset_type = (p.get("assetType") or p.get("asset_type") or "").upper()
    if not asset_type:
        # Infer from option contract
        if p.get("type") in ("CALL", "PUT") or "CALL" in p.get("symbol", "") or "PUT" in p.get("symbol", ""):
            asset_type = "OPTION"
        else:
            asset_type = "EQUITY"

    underlying = p.get("underlying") or p.get("ticker") or ""
    if not underlying and p.get("symbol"):
        # For equities the symbol IS the underlying; for options, parse it
        if asset_type == "EQUITY":
            underlying = p["symbol"]
        else:
            parts = p["symbol"].split("_")
            if parts:
                underlying = parts[0]

    if asset_type == "EQUITY":
        return PositionRef(
            underlying=underlying.upper(),
            asset_type="EQUITY",
            qty=float(p.get("qty") or p.get("quantity") or 0),
            cost_basis=float(p.get("cost_basis") or p.get("costBasis") or 0),
            contract_id=p.get("symbol", underlying),
        )

    # OPTION
    contract_id = p.get("symbol") or ""
    parsed = _parse_contract_id(contract_id) if contract_id else {}
    option_type = (p.get("type") or parsed.get("option_type") or "").upper()
    strike = float(p.get("strike") or parsed.get("strike") or 0)
    expiration = p.get("expiration") or parsed.get("expiration") or ""
    if hasattr(expiration, "isoformat"):
        expiration = expiration.isoformat()
    return PositionRef(
        underlying=underlying.upper(),
        asset_type="OPTION",
        option_type=option_type,
        strike=strike,
        expiration=str(expiration),
        qty=float(p.get("qty") or p.get("quantity") or 0),
        cost_basis=float(p.get("entry_price") or p.get("entryPrice") or
                          p.get("premiumReceived") or p.get("cost_basis") or 0),
        contract_id=contract_id,
    )


def _format_pos(ref: PositionRef) -> str:
    if ref.asset_type == "EQUITY":
        return f"{ref.underlying} {ref.qty:+.0f} sh"
    return (
        f"{ref.underlying} {ref.option_type} ${ref.strike:g} "
        f"{ref.expiration} qty={ref.qty:+.0f}"
    )


def _extract_action_position_refs(briefing_md: str) -> list[tuple[str, str]]:
    """
    Walk the action list and pull out (action_label, contract_id) pairs for any
    action that references an existing position.
    """
    refs: list[tuple[str, str]] = []
    if "## Today's Action List" not in briefing_md:
        return refs
    section = briefing_md.split("## Today's Action List", 1)[1].split("\n## ", 1)[0]
    for line in section.split("\n"):
        # Top-level numbered actions only (1. **CLOSE** XYZ_CALL_...)
        m = re.match(r"^\s*\d+\.\s+\*\*([A-Z ]+)\*\*\s+([A-Z][A-Z0-9_]+)", line)
        if m:
            action_kind = m.group(1).strip()
            target = m.group(2).strip()
            # Only flag actions that target an existing contract id
            if action_kind in ("CLOSE", "EXECUTE ROLL", "DEFENSIVE COLLAR", "DEFENSIVE ROLL-UP"):
                refs.append((f"{action_kind} {target}", target))
    return refs


def reconcile_positions(
    briefing_md: str,
    snapshot_positions: list,
    broker_positions: list,
    basis_tolerance_pct: float = 5.0,
) -> ReconciliationResult:
    """
    Compare snapshot vs broker positions and verify briefing actions reference
    real, currently-held contracts.

    Returns a ReconciliationResult with mismatches + a panel_md to prepend if issues.
    """
    snap_refs = [_position_to_ref(p) for p in (snapshot_positions or [])]
    broker_refs = [_position_to_ref(p) for p in (broker_positions or [])]

    snap_keys = {r.key(): r for r in snap_refs}
    broker_keys = {r.key(): r for r in broker_refs}

    # Set diffs
    missing_at_broker = [r for k, r in snap_keys.items() if k not in broker_keys]
    missing_in_snapshot = [r for k, r in broker_keys.items() if k not in snap_keys]

    # Quantity / basis verification on common positions
    mismatches: list[Mismatch] = []
    for k, snap_ref in snap_keys.items():
        if k not in broker_keys:
            continue
        broker_ref = broker_keys[k]
        if abs(snap_ref.qty - broker_ref.qty) > 0.5:
            mismatches.append(Mismatch(
                action=f"position {snap_ref.contract_id or _format_pos(snap_ref)}",
                issue=f"quantity mismatch (snapshot {snap_ref.qty:+.0f}, broker {broker_ref.qty:+.0f})",
                snapshot=_format_pos(snap_ref),
                broker=_format_pos(broker_ref),
            ))
        # Basis tolerance check (only if both nonzero)
        if snap_ref.cost_basis and broker_ref.cost_basis:
            drift_pct = abs(snap_ref.cost_basis - broker_ref.cost_basis) / max(broker_ref.cost_basis, 0.01) * 100
            if drift_pct > basis_tolerance_pct:
                mismatches.append(Mismatch(
                    action=f"position {snap_ref.contract_id or _format_pos(snap_ref)}",
                    issue=f"cost basis drift {drift_pct:.1f}% (snap ${snap_ref.cost_basis:.2f} vs broker ${broker_ref.cost_basis:.2f})",
                    snapshot=f"basis ${snap_ref.cost_basis:.2f}",
                    broker=f"basis ${broker_ref.cost_basis:.2f}",
                ))

    # Action-by-action: does each referenced contract exist at broker?
    action_refs = _extract_action_position_refs(briefing_md)
    block_actions: list[str] = []
    for action_label, contract_id in action_refs:
        # Find this contract in broker positions
        snap_pos = next((r for r in snap_refs if r.contract_id == contract_id), None)
        broker_pos = next((r for r in broker_refs if r.contract_id == contract_id), None)
        if snap_pos and not broker_pos:
            # Snapshot says we own it, broker doesn't
            mismatches.append(Mismatch(
                action=action_label,
                issue="snapshot contract NOT FOUND at broker (stale data or wrong expiration)",
                snapshot=_format_pos(snap_pos),
                broker="(no matching contract)",
            ))
            block_actions.append(action_label)

    # Verdict
    verified = (len(mismatches) == 0 and len(missing_in_snapshot) == 0)

    # Build the warning panel
    panel_lines: list[str] = []
    if not verified:
        panel_lines.append("")
        panel_lines.append("## 🚫 Position Data Mismatch — DO NOT TRADE")
        panel_lines.append("")
        panel_lines.append(
            "The briefing's snapshot disagrees with the live broker state. "
            "Recommendations below may target contracts you don't actually hold, "
            "or omit positions you do."
        )
        panel_lines.append("")
        if mismatches:
            panel_lines.append("### Mismatches detected")
            panel_lines.append("")
            for m in mismatches:
                panel_lines.append(f"- **{m.action}** — {m.issue}")
                panel_lines.append(f"  - snapshot: {m.snapshot}")
                panel_lines.append(f"  - broker: {m.broker}")
            panel_lines.append("")
        if missing_in_snapshot:
            panel_lines.append("### ⚠️ Positions at broker that the briefing missed")
            panel_lines.append("")
            for r in missing_in_snapshot:
                panel_lines.append(f"- {_format_pos(r)}")
            panel_lines.append("")
        if missing_at_broker:
            panel_lines.append("### Positions in snapshot not at broker (likely stale)")
            panel_lines.append("")
            for r in missing_at_broker:
                panel_lines.append(f"- {_format_pos(r)}")
            panel_lines.append("")
        if block_actions:
            panel_lines.append("### Blocked actions")
            panel_lines.append("")
            panel_lines.append(
                "These actions are SUPPRESSED until the snapshot is refreshed:"
            )
            panel_lines.append("")
            for a in block_actions:
                panel_lines.append(f"- 🚫 {a}")
            panel_lines.append("")
        panel_lines.append("**Required action:** refresh the briefing snapshot from live E*TRADE positions before placing any orders.")
        panel_lines.append("")

    return ReconciliationResult(
        verified=verified,
        mismatches=[asdict(m) for m in mismatches],
        missing_at_broker=[_format_pos(r) for r in missing_at_broker],
        missing_in_snapshot=[_format_pos(r) for r in missing_in_snapshot],
        panel_md="\n".join(panel_lines),
        block_actions=block_actions,
    )
