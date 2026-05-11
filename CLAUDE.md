# CLAUDE.md — portfolio-briefing project rules

Canonical rules that the briefing pipeline must follow. Update this file as
constraints evolve. Cross-cutting standards live here so we don't relitigate
them.

## Account scope — INDIVIDUAL only (for now)

The user has multiple E*TRADE accounts (Joint JTWROS, Individual Brokerage,
INDIVIDUAL, Roth IRA, Traditional IRA, etc.). The briefing pipeline is
currently scoped to **just the INDIVIDUAL account**. This is enforced by:

- `config/briefing.yaml` → `account_desc_whitelist: [INDIVIDUAL]`
- `adapters/etrade_adapter.fetch_etrade_snapshot(account_desc_whitelist=...)` —
  filters by `accountDesc` (exact match, case-insensitive) before any
  position/balance aggregation
- `snapshot_inputs.py` passes the config value through

**Why scoped:**
- Cross-account aggregation gave us a bad CC recommendation (SPY: 15 shares
  in "Individual Brokerage" + 91 in "INDIVIDUAL" = 106 aggregate, but no
  single account had a 100-share lot to write against).
- Tax routing rules differ by account (Roth IRA vs Taxable) and we haven't
  built that out fully yet.
- Until per-account ticket routing is wired, scoping to one account avoids
  proposing trades the user can't actually place there.

**To widen later:**
1. Add the new account's accountDesc to `account_desc_whitelist` in briefing.yaml.
2. Add account-routing logic so PULLBACK CSPs / new CCs are correctly
   routed (Roth IRA preference for short-dated premium, etc. — see the
   wheelhouz routing rules in `docs/04-from-wheelhouz-keep-drop.md`).
3. Test with `--etrade-live` and confirm positions de-dup correctly.

## Chain data — E*TRADE only, never yfinance

Every actionable recommendation that includes a strike or limit price MUST
pull its chain data through the `etrade-chain-fetcher` skill. yfinance is
forbidden for tradeable chain prices. See
`skills/etrade-chain-fetcher/SKILL.md` for the canonical surface.

yfinance IS still allowed for:
- IV rank approximation from 252-day historical vol
- RSI(14), 50/200-SMA, drawdown from 52-week high
- Earnings calendar dates
- General quotes/prices for non-option context

## Roll discipline — moneyness-based, not P&L%

Surface EXECUTE ROLL only when the position is at/past strike (genuine
assignment risk):
- Short PUT: spot ≤ 1.03 × strike
- Short CALL: spot ≥ 0.97 × strike

P&L% bleed from a rally is NOT a defensive trigger by itself. A -70% short
call that's still 12% OTM is "the rally happened, theta will recover," not
"I'm about to lose my shares." Same rule applies to puts.

See `skills/wheel-roll-advisor/` decision matrix.

## Core holdings — no force-sell

Tickers listed in `core_positions` are exempt from the standard 10% NLV
concentration trim. Use `core_concentration_cap_pct` (default 18%) as the
soft cap; only roll covered calls up at this stage, never sell shares.
At `core_runaway_cap_pct` (default 22%) flag REVIEW CORE with full option
menu, but still don't auto-recommend outright sale.

Current core list (`config/briefing.yaml`): GOOG, GOOGL, NVDA, MSFT, VRT,
ADBE, AMZN, META. TSLA is NOT on the core list — third-party SELL recs
on TSLA can fire EXIT.

## Trade-validator filtering — categorical

POOR/BLOCK trade-validator verdicts have asymmetric treatment:
- **NEW trades** (PULLBACK CSP, new CSP, new BTO LEAP, opening strangle):
  POOR/BLOCK → filter from action list, surface only in a transparency footer.
- **POSITION MANAGEMENT** (DEFENSIVE ROLL, DEFENSIVE COLLAR, EXECUTE ROLL,
  CLOSE NOW, TAKE PROFIT): render the badge but never filter. User may
  need to act regardless of EV.

Rationale: new exposure is discretionary; existing positions are committed.

## CC writability — per-account, not aggregate

Covered call recommendations must check that at least 100 shares are held
**within a single account**, not just aggregate across accounts. The CC
contract sits at the broker level; you can't combine a 15-share lot in
account A with a 91-share lot in account B to back one CC.

Implementation: `strategy_upgrades.py` reads each position's
`accountsBreakdown` field, parses per-account share counts, and uses
`max_qty_in_one_account` as the writability threshold. (With the INDIVIDUAL
account scope above, this is mostly moot — but the check stays in for
when we widen scope.)

## Live-data backing on every actionable line

Every actionable trade ticket MUST be backed by real market data fetched
this cycle. If yfinance or E*TRADE returns nothing, surface "data
unavailable, verify before placing" and skip the order ticket. Don't
substitute defaults.

This is enforced by the `briefing-data-verifier` skill, which scans the
rendered briefing for chain-attribution markers.

## Hard rules summary (one-liners)

1. **Account scope:** INDIVIDUAL only until further notice.
2. **Chain data:** E*TRADE via `etrade-chain-fetcher`, never yfinance.
3. **Roll trigger:** moneyness (at/past strike), not P&L%.
4. **Core trim:** roll CCs up; never auto-sell.
5. **POOR EV:** filter from new trades; render-with-badge for management.
6. **CC writability:** check per-account, not aggregate.
7. **No order placement:** read-only against brokerages.
8. **Tax framing:** rolling defers tax conditionally — never says "saves" tax.
9. **No directional forecasts:** flag conditions, don't predict prices.
10. **Fail closed:** missing data → suppress action, never fill with defaults.

## Pipeline overview

The briefing orchestrator runs these steps (see `scripts/run_briefing.py`):

1. Pre-flight (config, yesterday's briefing for diffing)
2. Load directives
3. Fetch third-party recommendations
4. Snapshot inputs (E*TRADE positions + parallel yfinance technicals + chains)
5. Classify regime (VIX/SPY)
6. Review equities (per-position decision)
7. Review options (wheel-roll-advisor matrix walk)
8. Generate new ideas (PULLBACK CSPs)
9. Long-term opportunities (3-12mo horizon)
10. Thematic scout (cached 24h; semis/nuclear/quantum/etc)
11. Day-over-day consistency check
12. Capital plan (rank by risk-reward tier)
13. Aggregate + render
14. Quality gate (4-persona structural check + pre-flight verifier)
15. Surface + deliver

## Key file locations

- `skills/daily-portfolio-briefing/config/briefing.yaml` — tunable thresholds
- `skills/daily-portfolio-briefing/scripts/run_briefing.py` — orchestrator
- `skills/daily-portfolio-briefing/scripts/etrade_auth.py` — in-repo OAuth
- `skills/etrade-chain-fetcher/scripts/fetch.py` — canonical chain access
- `skills/<name>/SKILL.md` — per-skill spec; read this for any skill behavior
- `~/.config/portfolio-briefing/etrade_tokens.json` — OAuth tokens (0600)
- `~/Documents/briefings/latest.md` — delivered briefing
