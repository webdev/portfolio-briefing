"""
Step 8.7: Red Flags & Priorities synthesis (Wave 30)

Consolidates cross-cutting risks the user should think about — surfaced in
plain English at the end of the briefing. Sources:
  - Stress coverage ratio (from analytics)
  - Imminent earnings on losing positions
  - Position-level moneyness (calls at/near strike on core)
  - Capital plan over-commitment (Tier 3 LT CSPs vs available cash)
  - Concentration breaches
  - Third-party SELL on held positions (uncommitted exits)
  - Put-stack concentration
  - Wide bid-ask spreads on proposed trades

Each red flag has: severity, headline, "what to do", "what NOT to do".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RedFlag:
    severity: str   # CRITICAL | HIGH | MEDIUM
    headline: str   # one-line summary
    detail: str     # multi-line explanation
    do: list = field(default_factory=list)      # what to do (1-2 actions)
    dont: list = field(default_factory=list)    # what NOT to do (1-2 items)


_SEVERITY_EMOJI = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "📊"}
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}


def compute_red_flags(
    *,
    snapshot_data: dict,
    analytics: dict | None,
    options_reviews: list | None,
    equity_reviews: list | None,
    capital_plan: dict | None,
    recommendations_list: list | None,
) -> list[RedFlag]:
    flags: list[RedFlag] = []

    balance = snapshot_data.get("balance", {}) or {}
    nlv = float(balance.get("accountValue", 0) or 0)
    cash = float(balance.get("cash", 0) or 0)
    positions = snapshot_data.get("positions") or []
    quotes = snapshot_data.get("quotes", {}) or {}

    # ----------------------------------------------------------------
    # 1. Stress coverage red / yellow
    # ----------------------------------------------------------------
    if analytics:
        sc = analytics.get("stress_coverage") if isinstance(analytics, dict) else getattr(analytics, "stress_coverage", None)
        coverage = None
        total_put_oblig = 0.0
        if sc:
            coverage = sc.get("coverage_ratio") if isinstance(sc, dict) else getattr(sc, "coverage_ratio", None)
            # The stress_coverage dataclass uses `total_put_obligations` (plural).
            # Fall back to several plausible names for robustness.
            for fld in ("total_put_obligations", "total_put_obligation",
                        "put_obligation", "obligation"):
                val = sc.get(fld) if isinstance(sc, dict) else getattr(sc, fld, None)
                if val:
                    try:
                        total_put_oblig = float(val)
                    except (TypeError, ValueError):
                        total_put_oblig = 0
                    break
        if coverage is not None and coverage < 0.5:
            severity = "CRITICAL" if coverage < 0.3 else "HIGH"
            short_put_count = sum(
                1 for p in positions
                if p.get("assetType") == "OPTION"
                and (p.get("type") or "").upper() == "PUT"
                and float(p.get("qty", 0) or 0) < 0
            )
            flags.append(RedFlag(
                severity=severity,
                headline=f"Stress coverage {coverage:.2f}× — below 0.50 floor",
                detail=(
                    f"You have **{short_put_count} short puts open** totaling "
                    f"**${total_put_oblig:,.0f} of cash-secured obligation** against "
                    f"**${cash:,.0f} of cash** (coverage {coverage:.2f}×). Note: this is "
                    f"the worst-case 'all puts assign at once' metric — see the "
                    f"separate Expiration-bucket flag for the realistic single-Friday "
                    f"concentration that drives time-weighted risk on a laddered book."
                ),
                do=[
                    "Close winners with ≥30% capture — frees collateral immediately.",
                    "Buy the recommended SPY put hedge — fat-tail insurance for 0.5-1% of NLV.",
                    "Do not open any new short puts until coverage > 0.5×.",
                ],
                dont=[
                    "Stack more cash-secured puts on AI/semi names; they move together.",
                    "Execute all Tier 3 LT CSPs — they make coverage worse, not better.",
                ],
            ))

    # ----------------------------------------------------------------
    # 1b. Expiration-bucket concentration on a single Friday
    # ----------------------------------------------------------------
    # Even when puts are well distributed in dollar terms, a single
    # expiration date that holds a fat share of NLV is a real liquidity
    # cluster. analyze_put_buckets tiers these into critical (≥30%) and
    # warning (≥20%). Critical → CRITICAL flag; warning → MEDIUM.
    put_buckets = (analytics or {}).get("put_buckets") if isinstance(analytics, dict) else None
    for bucket in (put_buckets or []):
        sev = getattr(bucket, "severity", None)
        if sev not in ("critical", "warning"):
            continue
        exp = bucket.expiration
        days = bucket.days_to_expiry
        days_str = f"{days}d out" if days is not None else "no DTE"
        names_sorted = sorted(bucket.names.items(), key=lambda kv: -kv[1])
        top_names = ", ".join(f"{t} (${o/1000:.0f}K)" for t, o in names_sorted[:5])
        more = f", + {len(names_sorted) - 5} more" if len(names_sorted) > 5 else ""
        rf_sev = "CRITICAL" if sev == "critical" else "MEDIUM"
        flags.append(RedFlag(
            severity=rf_sev,
            headline=(
                f"Expiration cluster on {exp.strftime('%a %b %d %y')} ({days_str}) — "
                f"{bucket.contract_count} short puts, "
                f"${bucket.total_obligation:,.0f} obligation, "
                f"{bucket.pct_of_nlv*100:.1f}% NLV"
            ),
            detail=(
                f"A single Friday holds **{bucket.contract_count} short puts** with "
                f"**${bucket.total_obligation:,.0f} of cash-secured obligation** "
                f"({bucket.pct_of_nlv*100:.1f}% of NLV). If a broad drop lands within "
                f"the week of {exp.strftime('%b %d')}, multiple positions assign on the "
                f"same day. Distribution-wise the book may look healthy elsewhere — "
                f"this bucket is where the real correlated-event risk concentrates.\n\n"
                f"Top positions in the bucket: {top_names}{more}"
            ),
            do=[
                f"Close the highest-capture put in this bucket first — moves obligation off {exp.strftime('%b %d')}.",
                f"Roll one position out to a later expiration — de-clusters with one ticket.",
                f"Don't add new puts in this expiration bucket — it's already concentrated.",
            ],
            dont=[
                f"Roll multiple positions INTO this date — it amplifies the cluster.",
                f"Treat the per-name 10% concentration cap as the only flag — bucket concentration is orthogonal.",
            ],
        ))

    # ----------------------------------------------------------------
    # 2. Imminent earnings on losing positions (binary risk)
    # ----------------------------------------------------------------
    today = date.today()
    binary_risk: list[dict] = []
    for r in (options_reviews or []):
        dte_earn = r.get("days_to_earnings")
        if dte_earn is None or not (0 <= dte_earn <= 14):
            continue
        cur = float(r.get("current_mid", 0) or 0)
        ent = float(r.get("entry_price", 0) or 0)
        pl_pct = (ent - cur) / ent if ent else 0
        # Only flag if position is meaningfully underwater OR near strike
        opt_type = (r.get("type") or "").upper()
        strike = float(r.get("strike") or 0)
        und = r.get("underlying") or ""
        spot = float(quotes.get(und, {}).get("last") or 0)
        moneyness = (spot / strike) if (strike and spot) else 1.0
        near_strike = (
            (opt_type == "PUT" and moneyness < 1.10)
            or (opt_type == "CALL" and moneyness > 0.92)
        )
        if pl_pct < -0.20 or near_strike:
            binary_risk.append({
                "contract": r.get("contract", ""),
                "earnings_in": dte_earn,
                "profit_pct": pl_pct,
                "moneyness": moneyness,
                "type": opt_type,
                "strike": strike,
                "spot": spot,
                "underlying": und,
            })

    if binary_risk:
        # Take the worst (most underwater + closest to strike + soonest earnings)
        binary_risk.sort(key=lambda x: (
            x["earnings_in"], -abs(x["profit_pct"]),
            abs(x["moneyness"] - 1.0),
        ))
        worst = binary_risk[0]
        flags.append(RedFlag(
            severity="HIGH",
            headline=f"{worst['underlying']} earnings in {worst['earnings_in']}d on a "
                     f"{worst['profit_pct']*100:+.0f}% short {worst['type'].lower()}",
            detail=(
                f"`{worst['contract']}` — spot ${worst['spot']:.2f} vs strike "
                f"${worst['strike']:.0f} (moneyness {worst['moneyness']:.2f}). "
                f"A binary event on a position that's already underwater is the worst "
                f"setup. {len(binary_risk)} earnings-window position(s) total."
            ),
            do=[
                f"Close `{worst['contract']}` now if the embedded tax cost of assignment "
                f"is unacceptable to you.",
                "Otherwise, roll up-and-out past the earnings date.",
            ],
            dont=[
                "Hold the original position through earnings unless you genuinely want assignment.",
                "Roll into a contract that ALSO has earnings inside its life.",
            ],
        ))

    # ----------------------------------------------------------------
    # 3. Capital plan would over-commit
    # ----------------------------------------------------------------
    if capital_plan:
        ending = capital_plan.get("ending_cash_projected")
        starting = capital_plan.get("starting_cash")
        if ending is not None and starting and ending < 0:
            flags.append(RedFlag(
                severity="HIGH",
                headline=f"Capital plan projects negative cash (${ending:,.0f})",
                detail=(
                    f"Executing every Tier 1/2/3 action would take cash from "
                    f"${starting:,.0f} to **${ending:,.0f}** — you'd be short on "
                    f"collateral. Cherry-pick from Tier 1 only, defer Tier 3."
                ),
                do=[
                    "Execute only Tier 1 (CRITICAL) actions first — they're cash-positive.",
                    "Re-evaluate Tier 3 long-term CSPs after closes free up cash.",
                ],
                dont=["Execute Tier 3 LT CSPs while cash is below the projected ending balance."],
            ))

    # ----------------------------------------------------------------
    # 4. Concentration over single-name cap (non-core)
    # ----------------------------------------------------------------
    config = (snapshot_data.get("_config") or {})
    # 2026-08-04 (PLTR): core = core_positions ∪ Tier A — a Tier A name
    # "within Tier A bounds" per the drift alert must not simultaneously
    # trip the non-core concentration red flag.
    try:
        from analysis.position_tiers import core_union as _core_union
        core = _core_union(config)
    except Exception:
        core = set((config.get("core_positions") or []))
    cap = float(config.get("concentration_cap_pct", 10)) / 100.0
    concentrated_noncore: list[tuple[str, float]] = []
    for er in (equity_reviews or []):
        w = float(er.get("weight", 0) or 0)
        t = er.get("ticker") or ""
        if t and t not in core and w > cap:
            concentrated_noncore.append((t, w))
    if concentrated_noncore:
        flags.append(RedFlag(
            severity="MEDIUM",
            headline=(
                f"Non-core concentration over {cap*100:.0f}% cap: "
                + ", ".join(f"{t} {w*100:.1f}%" for t, w in concentrated_noncore)
            ),
            detail=(
                "These names aren't on your core list so the standard "
                f"{cap*100:.0f}% cap applies. A single-name 20% gap on a name "
                "this size hits NLV disproportionately."
            ),
            do=[
                "Trim the position to bring weight under the cap, OR add to core list.",
            ],
            dont=["Add new short puts on the same name."],
        ))

    # ----------------------------------------------------------------
    # 5. Third-party SELL on held positions (uncommitted exits)
    # ----------------------------------------------------------------
    held_tickers = {
        (p.get("symbol") or "").upper()
        for p in positions
        if p.get("assetType") == "EQUITY" and float(p.get("qty", 0) or 0) > 0
    }
    sells: list[str] = []
    for r in (recommendations_list or []):
        t = (r.get("ticker") or "").upper()
        rec = (r.get("recommendation") or "").upper()
        if t in held_tickers and rec in ("SELL", "STRONG_SELL", "UNDERPERFORM"):
            sells.append(t)
    if sells:
        flags.append(RedFlag(
            severity="MEDIUM",
            headline=f"Third-party SELL recs still held: {', '.join(sorted(set(sells)))}",
            detail=(
                "These names had their thesis downgraded by your third-party source "
                "but the positions are still open. Decide actively — sell, hold with "
                "a documented reason, or override the directive in briefing-directives."
            ),
            do=[
                "Review each name's current technicals before acting.",
                "If holding past a SELL, document why (e.g., embedded LTCG tax, recent print).",
            ],
            dont=["Ignore — at minimum write a directive so future briefings don't keep flagging."],
        ))

    # ----------------------------------------------------------------
    # 6. Put-stack concentration (≥2 short puts on a single name)
    # ----------------------------------------------------------------
    stack: dict = {}
    for p in positions:
        if p.get("assetType") != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        if float(p.get("qty", 0) or 0) >= 0:
            continue
        und = (p.get("underlying") or "").upper()
        strike = float(p.get("strike") or 0)
        entry = stack.setdefault(und, {"count": 0, "strikes": [], "collateral": 0.0})
        entry["count"] += 1
        entry["strikes"].append(strike)
        entry["collateral"] += strike * 100
    stacked = {t: v for t, v in stack.items() if v["count"] >= 2}
    if stacked:
        total = sum(v["collateral"] for v in stacked.values())
        names = ", ".join(
            f"{t} {v['count']}× (${v['collateral']:,.0f})"
            for t, v in sorted(stacked.items(), key=lambda x: -x[1]["collateral"])
        )
        flags.append(RedFlag(
            severity="MEDIUM",
            headline=f"Layered put stacks: {names} → ${total:,.0f} of single-name collateral",
            detail=(
                f"{len(stacked)} name(s) have ≥2 short puts open. If any one drops 20% "
                f"all puts in that ladder assign at once — concentrated assignment risk."
            ),
            do=[
                "Take profits on the higher-capture put in each ladder when ≥30%.",
                "Don't add a 3rd put on these names (the system now blocks this automatically).",
            ],
            dont=["Roll out further — extending the ladder doesn't reduce concentration."],
        ))

    # Sort by severity then headline
    flags.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.headline))
    return flags


def render_red_flags_md(flags: list[RedFlag]) -> list[str]:
    """Render as a Markdown section. Always emits the header even if empty."""
    if not flags:
        return [
            "## 🚦 Red Flags & Priorities",
            "",
            "✅ No major cross-cutting risks detected.",
            "",
        ]

    lines = ["## 🚦 Red Flags & Priorities", ""]
    lines.append(
        f"_{len(flags)} risk(s) identified. Ranked by severity. "
        "Each item includes what to do and what NOT to do — read carefully before placing today's trades._"
    )
    lines.append("")

    for i, f in enumerate(flags, 1):
        emoji = _SEVERITY_EMOJI.get(f.severity, "•")
        lines.append(f"### {emoji} {i}. {f.severity} — {f.headline}")
        lines.append("")
        lines.append(f.detail)
        lines.append("")
        if f.do:
            lines.append("**Do:**")
            for d in f.do:
                lines.append(f"- {d}")
            lines.append("")
        if f.dont:
            lines.append("**Don't:**")
            for d in f.dont:
                lines.append(f"- {d}")
            lines.append("")
    return lines
