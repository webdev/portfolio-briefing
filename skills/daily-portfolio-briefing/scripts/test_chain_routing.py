#!/usr/bin/env python3
"""Chain/quote routing dry-run — verify the E*TRADE fetch plan (task #36).

RUNBOOK (George — before tomorrow's briefing):
  1. python3 skills/daily-portfolio-briefing/scripts/test_chain_routing.py --dry-run     # offline: prints the fetch plan from the recorded fixture
  2. python3 skills/daily-portfolio-briefing/scripts/test_chain_routing.py --live-probe  # 2-request E*TRADE sanity check (needs OAuth tokens): SPY expirations + one chain
  3. python3 skills/daily-portfolio-briefing/scripts/run_briefing.py --etrade-live       # real run — confirm the "chains: N etrade · M yfinance-fallback" line and provenance split

--dry-run replays the routing logic against the recorded fixture
(tests/fixtures/chain_routing_snapshot.json — 36 held underlyings +
115 candidates, the 2026-08-06 snapshot shape) with NO network: it prints
the priority tiers, the E*TRADE budget vs labeled yfinance fallback, the
quote batch plan, and the ≤4 req/s throttle schedule.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

FIXTURE = _SCRIPTS / "tests" / "fixtures" / "chain_routing_snapshot.json"
WATCHLIST = ["SPY", "QQQ", "^VIX"]


def dry_run(max_chains: int, as_of: str | None) -> int:
    from analysis import chain_routing as cr
    from steps.snapshot_inputs import _build_chain_pairs

    fixture = json.loads(FIXTURE.read_text())
    today = date.fromisoformat(as_of or fixture["as_of"])
    positions = fixture["positions"]
    exps_by_sym = fixture["available_expirations"]

    held_unds = sorted({p["underlying"] for p in positions
                        if p.get("assetType") == "OPTION"})
    candidates = [c for c in fixture["candidates"] if c not in held_unds]
    all_unds = sorted(set(held_unds) | set(candidates))

    print(f"=== Chain-routing dry run (fixture recorded {fixture['recorded']}, "
          f"as-of {today}) ===")
    print(f"held underlyings: {len(held_unds)} · candidates: {len(candidates)}")

    # Pair building runs entirely off the fixture's recorded expirations —
    # the lister always returns data, so no yfinance call is attempted.
    pairs = _build_chain_pairs(
        positions, all_unds,
        list_expirations_fn=lambda u: exps_by_sym.get(u, []),
    )
    plan = cr.build_chain_plan(pairs, positions,
                               max_chains=max_chains, today=today)

    print()
    print(plan.describe())

    print("\n--- fetch order (tier · dte · source) — first 20 ---")
    for it in plan.items[:20]:
        print(f"  tier{it.tier}  {it.underlying:<6} {it.expiration}  "
              f"dte={it.dte:<4} → {it.planned_source}")
    if len(plan.items) > 20:
        print(f"  ... {len(plan.items) - 20} more")

    fb = plan.yfinance_items
    if fb:
        print(f"\n--- yfinance-fallback (beyond budget cap) — {len(fb)} chains ---")
        print("  " + ", ".join(f"{i.underlying} {i.expiration}" for i in fb[:12])
              + (" ..." if len(fb) > 12 else ""))

    # Quote batch plan: full symbol set (held + candidates + watchlist)
    quote_symbols = sorted(set(all_unds) | set(WATCHLIST))
    batches = cr.plan_quote_batches(quote_symbols)
    yf_only = [s for s in quote_symbols if s.startswith("^")]
    print(f"\n--- quote plan ---")
    print(f"symbols: {len(quote_symbols)} → {len(batches)} E*TRADE batch calls "
          f"(≤{cr.QUOTE_BATCH_SIZE}/call) · yfinance-only: "
          f"{', '.join(yf_only) or 'none'}")

    sched = plan.throttle_schedule()
    total_reqs = plan.estimated_requests() + len(batches)
    print(f"\n--- throttle schedule (≤{plan.rate_per_sec:.0f} req/s aggregate) ---")
    print(f"first slots (s): {sched[:8]} ...")
    print(f"total E*TRADE requests: {plan.estimated_requests()} chain-side + "
          f"{len(batches)} quote batches = {total_reqs} "
          f"→ ≥{math.ceil(total_reqs / plan.rate_per_sec)}s wall-clock minimum")
    print("\nDry run OK — no network calls were made.")
    return 0


def live_probe() -> int:
    """Two real E*TRADE requests to confirm tokens/adapter before a live run."""
    from analysis import chain_routing as cr

    mod, cache = cr._load_chain_fetcher()
    if mod is None:
        print("E*TRADE fetcher UNAVAILABLE (no tokens / adapter import failed).")
        print("A live run would fall back to yfinance, labeled per-chain.")
        return 0
    exps = mod.list_expirations("SPY", cache=cache)
    if not exps:
        print("E*TRADE reachable but SPY expirations came back empty — "
              "check tokens (renew_etrade_token.py).")
        return 0
    print(f"SPY expirations: {len(exps)} listed, first {exps[0]}")
    chain = mod.get_chain("SPY", exps[0], strike_near=600.0, n_strikes=10,
                          cache=cache)
    if chain:
        print(f"SPY {exps[0]} chain: {len(chain['calls'])} calls / "
              f"{len(chain['puts'])} puts — source {chain['source']}. "
              f"Live routing is GO.")
    else:
        print("Chain fetch failed — live run would label fallbacks yfinance.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the fetch plan from the recorded fixture (no network)")
    ap.add_argument("--live-probe", action="store_true",
                    help="2-request live E*TRADE sanity check")
    ap.add_argument("--max-chains", type=int, default=150,
                    help="E*TRADE chain budget (chains.etrade_max_chains)")
    ap.add_argument("--as-of", default=None,
                    help="override the fixture's as-of date (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.live_probe:
        return live_probe()
    return dry_run(args.max_chains, args.as_of)


if __name__ == "__main__":
    sys.exit(main())
