---
name: briefing-quality-gate
description: Deterministic validator that gates briefings through four expert personas (financial advisor, options trader, tax CPA, risk manager) before release. Failed briefings get a warnings panel auto-prepended.
---

# Briefing Quality Gate

Gate every briefing through structural validators that replicate four expert personas. Each persona inspects the rendered markdown for completeness, correctness, and compliance with domain-specific standards. The briefing must score ≥70 on each persona to pass. Failed briefings get an auto-prepended warnings panel.

## When to Use

- After `aggregate_briefing()` renders markdown, before displaying to user
- As a pre-release quality check in the briefing pipeline
- To catch systematic issues: missing yield calculations, invalid expirations, wash-sale gaps, stress-test omissions

## The Four Personas

**Financial Advisor (100 → score):**
- Every action item has `**Why:**` and `**Gain:**` bullets
- Every action has `Yield:` line OR explicit "no yield (one-time)"
- Total Impact summary card present
- Header shows long-form date AND ISO date
- Critical fail: no actions surfaced

**Options Trader (100 → score):**
- Every EXECUTE ROLL has both legs (BTC + STO) with strikes, ISO expirations, limit prices
- Every EXECUTE ROLL shows Delta line
- Cap buffer % shown for diagonals
- ALL expirations are Mon-Fri (no Sat/Sun; VIX Wednesday-only)
- Critical fail: any weekend expiration

**Tax CPA (100 → score):**
- Every NEW CSP has `Wash-sale check:` line
- Every ROLL/NEW CSP has `Earnings check:` line
- Every TRIM mentions LTCG tax cost
- Account routing line on new entries
- Tax-avoided $ in Total Impact card

**Risk Manager (100 → score):**
- Stress Test panel present and names positions in scenarios
- Net Greeks line shows non-zero theta when shorts present
- Hedge Book panel present
- Concentration breaches (>10%) have TRIM or COLLAR action

## Scoring & Gates

- Start: 100 per persona
- Critical issue: −30 (one critical fail = auto-fail regardless of numeric score)
- Major issue: −15
- Minor issue: −5
- Pass threshold: ≥70 per persona
- Final decision: all 4 must pass

## Output

Returns dict with:
- `passed`: bool
- `personas`: {name: {score, issues: [{severity, text}], strengths}}
- `blocking_issues`: list of critical failures
- `recommended_action`: "release" | "auto-fix" | "block-and-show-issues"

If failed, `gate_and_render()` prepends a `## ⚠️ Quality Gate Issues` panel to the briefing markdown before returning.

## Implementation

Pure Python, deterministic (regex + structural inspection). No LLM calls. All thresholds in `references/persona_checks.yaml` for tuning.
