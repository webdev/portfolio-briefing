#!/usr/bin/env python3
"""Pre-trade validation CLI.

Lets you check any proposed CSP or CC against the briefing's discipline rules
WITHOUT placing the trade. Reads the latest snapshot (or pulls live from
E*TRADE) and runs the proposed trade through ``pre_trade_validator``.

Usage examples:

  # Quick check using the most recent local snapshot (fastest)
  uv run python scripts/validate_trade.py \\
      --ticker MU --strike 960 --expiration 2026-08-21

  # Force a fresh E*TRADE + yfinance pull before validating
  uv run python scripts/validate_trade.py \\
      --ticker MU --strike 960 --expiration 2026-08-21 --etrade-live

  # Validate a covered call write instead of a put sale
  uv run python scripts/validate_trade.py \\
      --ticker NVDA --strike 230 --expiration 2026-07-17 \\
      --type CALL --action SELL_OPEN

Exit codes:
  0 — proposed trade passes all checks (or only WARN-level findings)
  1 — at least one BLOCK-severity finding fires
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Make the scripts package importable when invoked directly
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from analysis import pre_trade_validator as ptv  # noqa: E402


def _latest_snapshot_dir(state_root: Path) -> Path | None:
    """Find the most recently-modified briefing snapshot directory."""
    if not state_root.exists():
        return None
    candidates = [p for p in state_root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _load_snapshot_from_disk(snapshot_dir: Path) -> dict:
    """Reconstruct a snapshot_data dict from the on-disk JSON files."""
    out: dict = {}
    files = {
        "balance": "balance.json",
        "positions": "positions.json",
        "technicals": "technicals.json",
        "earnings_calendar": "earnings.json",
        "recommendations_list": "recommendations_list.json",
        "open_orders": "open_orders.json",
        "quotes": "quotes.json",
        "iv_ranks": "iv_ranks.json",
    }
    for key, fname in files.items():
        p = snapshot_dir / fname
        if p.exists():
            try:
                out[key] = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                print(f"  [warn] could not parse {fname}: {e}", file=sys.stderr)
    return out


def _compute_stress_coverage(snapshot_data: dict) -> Optional[float]:
    """Compute the stress-coverage ratio from snapshot positions + cash.

    Mirrors what analysis/stress_coverage.compute_stress_coverage does at a
    high level — sum of cash-secured short-put obligations vs cash. We bypass
    the full module here to avoid pulling its larger dependency surface.
    """
    balance = snapshot_data.get("balance", {}) or {}
    cash = float(balance.get("cash") or balance.get("cashBalance") or 0)
    obligation = 0.0
    for p in snapshot_data.get("positions", []) or []:
        if p.get("assetType") != "OPTION":
            continue
        if (p.get("type") or p.get("option_type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty") or 0)
            strike = float(p.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if qty < 0 and strike > 0:
            obligation += strike * abs(qty) * 100
    if obligation <= 0:
        return None
    return cash / obligation


# Imports used by the helpers above
from typing import Optional  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate a proposed CSP/CC against the briefing's discipline rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--ticker", required=True, help="Underlying symbol, e.g. MU")
    ap.add_argument("--strike", required=True, type=float, help="Strike price")
    ap.add_argument("--expiration", required=True,
                    help="Expiration date as YYYY-MM-DD (e.g. 2026-08-21)")
    ap.add_argument("--type", default="PUT", choices=["PUT", "CALL"],
                    help="Option type (default: PUT)")
    ap.add_argument("--action", default="SELL_OPEN",
                    choices=["SELL_OPEN", "BUY_CLOSE"],
                    help="Order action (default: SELL_OPEN)")
    ap.add_argument("--quantity", type=int, default=1, help="Contracts (default 1)")
    ap.add_argument("--limit-price", type=float, default=None,
                    help="Limit price (optional — used for sanity checks)")
    ap.add_argument("--etrade-live", action="store_true",
                    help="(deprecated) refresh data by running a briefing "
                         "first: `uv run python scripts/run_briefing.py --etrade-live --force`")
    ap.add_argument("--snapshot-dir", type=Path, default=None,
                    help="Override snapshot directory path")
    ap.add_argument("--show-passing", action="store_true",
                    help="Print a line even when the trade passes all checks")
    args = ap.parse_args()

    # The CLI always validates against the latest on-disk snapshot. For fresh
    # data, run the full briefing pipeline first (it handles the full snapshot
    # + analytics + scout cache flow). One-off snapshot refreshes from the CLI
    # are fragile and duplicate that logic.
    if args.etrade_live:
        print("[info] --etrade-live no longer triggers an in-process snapshot. "
              "Run a briefing to refresh:\n"
              "  uv run python scripts/run_briefing.py --etrade-live --force",
              file=sys.stderr)
        print("[info] Continuing with the latest on-disk snapshot below.", file=sys.stderr)

    if args.snapshot_dir is not None:
        snap_dir = args.snapshot_dir
    else:
        state_root = _SCRIPTS_DIR.parent / "state" / "briefing_snapshots"
        snap_dir = _latest_snapshot_dir(state_root)
        if snap_dir is None:
            print(f"ERROR: no snapshot found under {state_root}. Run a briefing "
                  f"first to create one:\n  uv run python scripts/run_briefing.py "
                  f"--etrade-live --force", file=sys.stderr)
            return 2
    print(f"[info] Using snapshot: {snap_dir}", file=sys.stderr)
    snapshot_data = _load_snapshot_from_disk(snap_dir)

    if not snapshot_data:
        print("ERROR: snapshot is empty — re-run a briefing first.", file=sys.stderr)
        return 2

    # Parse expiration
    try:
        y, m, d = args.expiration.split("-")
        exp = date(int(y), int(m), int(d))
    except (ValueError, IndexError):
        print(f"ERROR: invalid --expiration '{args.expiration}'. Use YYYY-MM-DD.",
              file=sys.stderr)
        return 2

    # Build the validator context from the snapshot
    stress_cov = _compute_stress_coverage(snapshot_data)
    ctx = ptv.build_context_from_snapshot(
        snapshot_data,
        ticker=args.ticker,
        strike=args.strike,
        expiration=exp,
        option_type=args.type,
        action=args.action,
        quantity=args.quantity,
        limit_price=args.limit_price,
        stress_coverage=stress_cov,
        recommendations_list=snapshot_data.get("recommendations_list"),
    )

    # Show what the validator is seeing — helps with "why did it fire?"
    print()
    print("=" * 78)
    print(f"PRE-TRADE VALIDATION: {args.action} {args.quantity}× {args.ticker} "
          f"${args.strike:g} {args.type} exp {args.expiration}"
          + (f" @ ${args.limit_price}" if args.limit_price else ""))
    print("=" * 78)
    print()
    print(f"Context snapshot:")
    print(f"  Spot:             ${ctx.spot:,.2f}" if ctx.spot else "  Spot:             (n/a)")
    print(f"  RSI:              {ctx.rsi:.0f}" if ctx.rsi is not None else "  RSI:              (n/a)")
    print(f"  IV rank:          {ctx.iv_rank}" if ctx.iv_rank else "  IV rank:          (n/a)")
    print(f"  Earnings:         {ctx.earnings_date}" if ctx.earnings_date else "  Earnings:         (none in window)")
    print(f"  NLV:              ${ctx.nlv:,.0f}")
    print(f"  Cash:             ${ctx.cash:,.0f} ({ctx.cash/ctx.nlv*100:.1f}% NLV)" if ctx.nlv else "")
    print(f"  Stress coverage:  {ctx.stress_coverage:.2f}×" if ctx.stress_coverage is not None else "  Stress coverage:  (n/a)")
    if ctx.existing_short_puts:
        strikes = sorted({sp["strike"] for sp in ctx.existing_short_puts})
        print(f"  Existing SHORT PUTs on {ctx.ticker}: {strikes}")
    if ctx.existing_long_puts:
        strikes = sorted({lp["strike"] for lp in ctx.existing_long_puts})
        print(f"  Existing LONG PUTs on {ctx.ticker}: {strikes} (collar)")
    # Bucket-impact display only makes sense for cash-secured PUT writes
    # (calls cap upside but don't tie up cash collateral on a Friday).
    if args.type == "PUT" and args.action == "SELL_OPEN":
        bucket = ctx.obligation_by_expiration.get(exp, 0)
        if bucket > 0:
            bp = bucket / ctx.nlv * 100 if ctx.nlv else 0
            new_obl = ctx.strike * ctx.quantity * 100
            proj = (bucket + new_obl) / ctx.nlv * 100 if ctx.nlv else 0
            print(f"  {args.expiration} bucket: ${bucket:,.0f} ({bp:.1f}% NLV) → "
                  f"${bucket+new_obl:,.0f} ({proj:.1f}%) after this trade")
    # For CC writes, surface existing short-call exposure on the same name
    if args.type == "CALL" and ctx.existing_short_calls:
        strikes = sorted({sc["strike"] for sc in ctx.existing_short_calls})
        print(f"  Existing SHORT CALLs on {ctx.ticker}: {strikes}")
    if args.type == "CALL" and ctx.held_shares:
        contracts_writable = ctx.held_shares // 100
        print(f"  Held shares: {ctx.held_shares} (max {contracts_writable} CC contracts)")
    print()

    # Run the validator
    findings = ptv.validate_proposed_trade(ctx)
    rendered = ptv.format_findings_md(findings, trade_label=f"{args.ticker} ${args.strike:g}{args.type[0]} {args.expiration}",
                                       show_passing=args.show_passing)
    for line in rendered:
        print(line)
    if not findings:
        print("✅ All discipline checks pass. Trade is consistent with the system's rules.")
    else:
        # Bottom-line verdict
        print()
        if ptv.has_blockers(findings):
            print(f"VERDICT: 🚫 DO NOT EXECUTE — {sum(1 for f in findings if f.severity==ptv.SEV_BLOCK)} BLOCK-level "
                  f"discipline violation(s). The trade fails the system's rules.")
        else:
            print(f"VERDICT: ⚠️ EXECUTE WITH CAUTION — {sum(1 for f in findings if f.severity==ptv.SEV_WARN)} "
                  f"WARN-level finding(s). No BLOCKs, but read each warning before placing.")

    return 1 if ptv.has_blockers(findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
