"""Open-orders audit — runs the pre-trade validator against every pending GTC
order on E*TRADE.

Motivating case: the user had a stale MU $960P Aug 21 SELL_OPEN GTC sitting at
a $154 limit (from when MU was at $985) — three discipline violations would
have fired if the system had checked it. The Risk Alerts section now surfaces
that automatically by walking through E*TRADE's open_orders list every
briefing run.

This module is INTENTIONALLY tolerant of input shape — the E*TRADE adapter is
expected to grow `open_orders` over time (pyetrade's orders API has a
different signature than positions/balance, so the v1 adapter leaves it
empty). The shape normalizer below handles the typical fields the API
returns and skips orders it can't parse cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from analysis import pre_trade_validator as ptv


@dataclass
class OpenOrderAudit:
    """One pending order with its validation result."""
    label: str                         # human-readable trade description
    order_id: Optional[str]            # broker order id if available
    findings: list[ptv.TradeValidation]
    raw: dict                          # original order dict for debugging


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_date(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            y, m, d = v.split("-")
            return date(int(y), int(m), int(d))
        except (ValueError, IndexError):
            return None
    return None


def _normalize_order(order: dict) -> Optional[dict]:
    """Extract the fields we need from an E*TRADE open-order payload.

    E*TRADE's order JSON nests trade legs inside ``orderDetail[0].instrument[0]``
    in the V0 shape, but the adapter may flatten that. We accept either:

    Flattened keys we look for first:
      ticker, symbol, underlying, type/option_type, action, strike, expiration,
      quantity, limit_price/price, order_id

    Nested orderDetail shape:
      orderDetail[].instrument[].{Product.symbol/securityType/strikePrice/
        callPut/expiryDay+expiryMonth+expiryYear}, quantity, orderAction

    Returns a dict with: ticker, strike, expiration (date), option_type,
    action, quantity, limit_price, order_id. Returns None if we can't
    extract enough to validate.
    """
    if not isinstance(order, dict):
        return None

    # ── Flattened shape ──
    ticker = (order.get("ticker") or order.get("underlying") or order.get("symbol", "")).upper().split("_")[0]
    strike = _safe_float(order.get("strike"))
    expiration = _safe_date(order.get("expiration"))
    option_type = (order.get("option_type") or order.get("type") or "").upper()
    action = (order.get("action") or order.get("orderAction") or "").upper().replace("-", "_").replace(" ", "_")
    quantity = int(order.get("quantity") or order.get("orderedQuantity") or 0)
    limit_price = _safe_float(order.get("limit_price") or order.get("limitPrice") or order.get("price"))
    order_id = order.get("orderId") or order.get("order_id")

    # ── Nested E*TRADE shape (best-effort) ──
    if not ticker or not strike or not expiration:
        details = order.get("orderDetail") or []
        if details and isinstance(details, list):
            d = details[0]
            instruments = d.get("instrument") or []
            if instruments:
                inst = instruments[0]
                product = inst.get("Product") or {}
                ticker = ticker or (product.get("symbol") or "").upper()
                strike = strike or _safe_float(product.get("strikePrice"))
                cp = (product.get("callPut") or "").upper()
                option_type = option_type or ("PUT" if cp.startswith("P") else "CALL" if cp.startswith("C") else "")
                y = product.get("expiryYear")
                m = product.get("expiryMonth")
                dd = product.get("expiryDay")
                if not expiration and y and m and dd:
                    try:
                        expiration = date(int(y), int(m), int(dd))
                    except (ValueError, TypeError):
                        pass
                quantity = quantity or int(inst.get("quantity", 0) or 0)
                action = action or (inst.get("orderAction") or "").upper().replace("-", "_").replace(" ", "_")
            limit_price = limit_price or _safe_float(d.get("limitPrice"))

    # Normalize action strings — broker varies between e.g. SELL_OPEN, SELL_TO_OPEN
    if "SELL" in action and "OPEN" in action:
        action = "SELL_OPEN"
    elif "BUY" in action and "CLOSE" in action:
        action = "BUY_CLOSE"

    if not ticker or not strike or not expiration or option_type not in ("PUT", "CALL"):
        return None

    return {
        "ticker": ticker,
        "strike": strike,
        "expiration": expiration,
        "option_type": option_type,
        "action": action or "SELL_OPEN",
        "quantity": abs(quantity) or 1,
        "limit_price": limit_price,
        "order_id": str(order_id) if order_id else None,
    }


def audit_open_orders(
    snapshot_data: dict,
    *,
    config: Optional[dict] = None,
    analytics: Optional[dict] = None,
    recommendations_list: Optional[list] = None,
) -> list[OpenOrderAudit]:
    """Walk the snapshot's open_orders list and validate each one.

    Returns one OpenOrderAudit per parseable order. Orders we can't parse
    are silently dropped (will be visible in the count delta).
    """
    raw_orders = snapshot_data.get("open_orders", []) or []
    audits: list[OpenOrderAudit] = []
    for raw in raw_orders:
        norm = _normalize_order(raw)
        if not norm:
            continue
        try:
            ctx = ptv.build_context_from_snapshot(
                snapshot_data,
                ticker=norm["ticker"],
                strike=norm["strike"],
                expiration=norm["expiration"],
                option_type=norm["option_type"],
                action=norm["action"],
                quantity=norm["quantity"],
                limit_price=norm.get("limit_price"),
                analytics=analytics,
                recommendations_list=recommendations_list,
            )
            findings = ptv.validate_proposed_trade(ctx, config)
        except Exception:
            continue
        label = (
            f"{norm['action']} {norm['quantity']}× {norm['ticker']} "
            f"${norm['strike']:g}{norm['option_type'][0]} exp "
            f"{norm['expiration']}"
            + (f" @ ${norm['limit_price']:.2f}" if norm.get("limit_price") else "")
        )
        audits.append(OpenOrderAudit(
            label=label,
            order_id=norm.get("order_id"),
            findings=findings,
            raw=raw,
        ))
    return audits


def render_audit_panel(audits: list[OpenOrderAudit], *, header_level: str = "##") -> list[str]:
    """Render the open-orders audit as a markdown panel for the briefing.

    Format:
      ## 🔍 Open Orders Audit
      _N pending orders checked. K with discipline violations._
      ### 🚫 BLOCK — MU $960P Aug 21 (Order #672)
        - 🚫 BLOCK ...
        - ⚠️ WARN  ...

    Returns empty list when there are no orders to audit (clean briefing).
    """
    if not audits:
        return []

    lines: list[str] = [f"{header_level} 🔍 Open Orders Audit", ""]

    with_blockers = [a for a in audits if ptv.has_blockers(a.findings)]
    with_warns = [a for a in audits if (not ptv.has_blockers(a.findings)
                  and any(f.severity == ptv.SEV_WARN for f in a.findings))]
    clean = [a for a in audits if not a.findings]

    lines.append(
        f"_{len(audits)} pending order(s) checked: "
        f"🚫 {len(with_blockers)} BLOCKed · "
        f"⚠️ {len(with_warns)} flagged · "
        f"✅ {len(clean)} clean._"
    )
    lines.append("")

    if with_blockers:
        lines.append(f"{header_level}# 🚫 BLOCK — these orders violate discipline; cancel or fix them")
        lines.append("")
        for a in with_blockers:
            tag = f" (Order #{a.order_id})" if a.order_id else ""
            lines.append(f"**{a.label}**{tag}")
            for f in a.findings:
                emoji = "🚫" if f.severity == ptv.SEV_BLOCK else "⚠️"
                lines.append(f"  - {emoji} **{f.severity}** ({f.rule_id}) — {f.reason}")
            lines.append("")

    if with_warns:
        lines.append(f"{header_level}# ⚠️ WARN — review before next fill")
        lines.append("")
        for a in with_warns:
            tag = f" (Order #{a.order_id})" if a.order_id else ""
            lines.append(f"**{a.label}**{tag}")
            for f in a.findings:
                lines.append(f"  - ⚠️ **{f.severity}** ({f.rule_id}) — {f.reason}")
            lines.append("")

    if clean and not with_blockers and not with_warns:
        # If everything's clean, keep the panel tight.
        lines.append("_All pending orders pass discipline checks._")
        lines.append("")

    return lines
