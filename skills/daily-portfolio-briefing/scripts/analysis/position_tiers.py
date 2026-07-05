"""Position tier framework — classify holdings into A/B/C buckets that
control how aggressively the system writes covered calls and manages
concentration on each name.

CLAUDE.md hard rule #29 contract: every equity position is assigned a tier
that gates covered-call recommendations and tunes concentration caps:

  • Tier A — LT Core compounders (NVDA, GOOG, MSFT, META, PLTR, AMZN, SPY/VOO):
        NO CC recommendations EVER. Concentration cap raised to ~22%
        (concentration in conviction IS the strategy).
  • Tier B — Income holdings (MU, SMH):
        Conservative CC only — RSI ≥ 70, ≤ 0.15 delta, ≥ 10% OTM,
        ≤ 50% coverage cap of held shares, ≤ 30 DTE. Cap ~12%.
  • Tier C — Active wheel (default — everything not listed elsewhere):
        Current discipline — RSI ≥ 60, ~0.25 delta, ~6% OTM, 100% coverage
        cap, ≤ 45 DTE. Cap ~8% (or default 10%).

Fail-closed: missing/empty `position_tiers` config → every ticker defaults
to tier C (current behavior). This guarantees backward compatibility — if
the config block is removed entirely, the briefing behaves exactly as it
did before the framework was added.

Mirrors the parkev_chip.annotate_parkev_chips post-process pattern: a
single deterministic walk appends ` · {badge}` to each header that already
carries the 🅿️ Parkev chip, so the tier badge sits next to the rating chip.
"""

from __future__ import annotations

import re


# Tier-name constants — single source of truth so downstream code doesn't
# scatter raw 'A' / 'B' / 'C' literals around.
TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

_VALID_TIERS = frozenset({TIER_A, TIER_B, TIER_C})

# Default tier when a ticker isn't explicitly listed in any bucket. Tier C
# matches the briefing's pre-framework behavior, so an unconfigured ticker
# is indistinguishable from the legacy default.
_DEFAULT_TIER = TIER_C

# Compact visual badges. Colors map roughly to "leave it alone" (green) →
# "tread carefully" (yellow) → "active wheel" (blue / neutral).
_TIER_BADGES = {
    TIER_A: "🟢 Tier A",
    TIER_B: "🟡 Tier B",
    TIER_C: "🔵 Tier C",
}

# Hard-coded fallbacks for the per-tier CC discipline knobs. Used when a
# config doesn't carry `covered_call_tiers` — same defaults as documented in
# CLAUDE.md hard rule #29 and the briefing.yaml template.
_FALLBACK_CC_TIER_PARAMS = {
    TIER_A: {
        "enabled": False,
        "rsi_floor": 999,        # never triggers
        "min_otm_pct": 999.0,
        "max_delta": 0.0,
        "coverage_cap_pct": 0,
        "max_dte": 0,
        "roll_up_trigger": 0.92,
        "tax_aware_assignment_block": True,
    },
    TIER_B: {
        "enabled": True,
        "rsi_floor": 70,
        "min_otm_pct": 10.0,
        "max_delta": 0.15,
        "coverage_cap_pct": 50,
        "max_dte": 30,
        "roll_up_trigger": 0.93,
        "tax_aware_assignment_block": True,
    },
    TIER_C: {
        "enabled": True,
        "rsi_floor": 60,
        "min_otm_pct": 4.0,
        "max_delta": 0.30,
        "coverage_cap_pct": 100,
        "max_dte": 45,
        "roll_up_trigger": 0.97,
        "tax_aware_assignment_block": False,
    },
}

# Default per-tier concentration caps (% of NLV). Tier A is intentionally
# permissive — capping a conviction compounder at 10% fights the strategy.
_FALLBACK_CONCENTRATION_CAPS = {
    TIER_A: 22.0,
    TIER_B: 12.0,
    TIER_C: 8.0,
    "default": 10.0,
}


# ─── Config helpers ────────────────────────────────────────────────────────


def _position_tiers_block(config: dict | None) -> dict:
    """Pull the `position_tiers` block from config (or empty dict)."""
    if not config or not isinstance(config, dict):
        return {}
    block = config.get("position_tiers")
    if not isinstance(block, dict):
        return {}
    return block


def _cc_tiers_block(config: dict | None) -> dict:
    """Pull the `covered_call_tiers` block from config (or empty dict)."""
    if not config or not isinstance(config, dict):
        return {}
    block = config.get("covered_call_tiers")
    if not isinstance(block, dict):
        return {}
    return block


def _conc_caps_block(config: dict | None) -> dict:
    """Pull the `concentration_caps` block from config (or empty dict)."""
    if not config or not isinstance(config, dict):
        return {}
    block = config.get("concentration_caps")
    if not isinstance(block, dict):
        return {}
    return block


def _normalize_tier_list(raw) -> set[str]:
    """Coerce a config list of tickers into an upper-case set; tolerant of
    None, scalar string, or non-list input."""
    if not raw:
        return set()
    if isinstance(raw, str):
        return {raw.upper().strip()}
    if isinstance(raw, (list, tuple, set, frozenset)):
        out = set()
        for v in raw:
            if isinstance(v, str) and v.strip():
                out.add(v.upper().strip())
        return out
    return set()


# ─── Public API ────────────────────────────────────────────────────────────


def tier_for(ticker: str, config: dict | None) -> str:
    """Return 'A' | 'B' | 'C' for a given ticker.

    Lookup precedence:
      1. `position_tiers.tier_a_core` (explicit Tier A)
      2. `position_tiers.tier_b_income` (explicit Tier B)
      3. `position_tiers.tier_c_active` (explicit Tier C — optional, rarely set)
      4. fallback: `_DEFAULT_TIER` (= Tier C — preserves legacy behavior)

    Missing/empty `position_tiers` config → every ticker → Tier C. This is
    the backward-compatibility guarantee: the framework is a no-op until
    the config opts in.
    """
    if not ticker:
        return _DEFAULT_TIER
    tk = ticker.upper().strip()
    if not tk:
        return _DEFAULT_TIER

    block = _position_tiers_block(config)
    if not block:
        return _DEFAULT_TIER

    if tk in _normalize_tier_list(block.get("tier_a_core")):
        return TIER_A
    if tk in _normalize_tier_list(block.get("tier_b_income")):
        return TIER_B
    if tk in _normalize_tier_list(block.get("tier_c_active")):
        return TIER_C
    return _DEFAULT_TIER


def cc_settings_for_tier(tier: str, config: dict | None) -> dict:
    """Return the covered-call discipline dict for a tier.

    Reads `covered_call_tiers.tier_{a,b,c}` from config, falling back to
    the canonical defaults baked into this module for any missing key.
    The returned dict has shape:
        {
            enabled: bool,
            rsi_floor: int,
            min_otm_pct: float,
            max_delta: float,
            coverage_cap_pct: int,   # 0-100, % of held shares to cover
            max_dte: int,
            roll_up_trigger: float,  # spot/strike ratio that triggers roll-up
            tax_aware_assignment_block: bool,
        }
    """
    t = (tier or _DEFAULT_TIER).upper().strip()
    if t not in _VALID_TIERS:
        t = _DEFAULT_TIER
    fallback = dict(_FALLBACK_CC_TIER_PARAMS[t])
    cfg = _cc_tiers_block(config)
    user = cfg.get(f"tier_{t.lower()}") if isinstance(cfg, dict) else None
    if not isinstance(user, dict):
        return fallback
    # Merge user-overrides on top of fallback; user wins per-key.
    out = dict(fallback)
    for k, v in user.items():
        if v is not None:
            out[k] = v
    return out


def engineered_cc_eligible(
    ticker: str,
    config: dict | None,
    *,
    spot: float | None,
    rsi: float | None,
    iv_rank: float | None,
    drawdown_pct: float | None,
    sma_200: float | None,
    analyst_pt: float | None,
) -> tuple[bool, dict, list[str]]:
    """Evaluate whether a Tier A engineered CC fires on `ticker` today.

    Returns (eligible, envelope_dict, reasons_or_blockers).
    envelope_dict includes the *minimum* rebound-proof strike so the
    caller (strategy_upgrades.py) can pick the actual strike from the
    live chain at or above it.

    See CLAUDE.md hard rule #34 + engineered mode block in briefing.yaml.
    """
    reasons: list[str] = []
    settings = cc_settings_for_tier("A", config) or {}
    eng = (settings.get("engineered") or {}) if isinstance(settings, dict) else {}
    empty_envelope = {"applies": False}

    if not eng or not eng.get("enabled"):
        return (False, empty_envelope, ["engineered mode disabled"])

    # Ticker must still be on the willingness list (belt + suspenders)
    whitelist = settings.get("willing_to_write_cc_on") or []
    whitelist_upper = {str(t).upper() for t in whitelist if t}
    if ticker.upper() not in whitelist_upper:
        return (False, empty_envelope,
                [f"{ticker} not on willing_to_write_cc_on whitelist"])

    # Numeric gates
    if rsi is None or rsi < float(eng.get("rsi_floor", 40)):
        reasons.append(f"RSI {rsi} below floor {eng.get('rsi_floor', 40)}")
    if iv_rank is None or iv_rank < float(eng.get("min_iv_rank", 70)):
        reasons.append(f"IV rank {iv_rank} below {eng.get('min_iv_rank', 70)}")
    if drawdown_pct is None or drawdown_pct < float(eng.get("min_drawdown_pct", 10)):
        reasons.append(
            f"drawdown {drawdown_pct}% below {eng.get('min_drawdown_pct', 10)}% "
            "— no rebound premium in the setup"
        )

    if reasons:
        return (False, empty_envelope, reasons)

    # Compute the minimum rebound-proof strike.
    #
    # The 200-SMA is the practical 21-day snapback target; analyst PT is a
    # 12-month target. Using analyst PT as a HARD floor pushes strikes so
    # far OTM (35-45%) that premium collapses to pennies. So:
    #   - HARD floor:  max(spot × (1 + min_otm_pct/100),  200-SMA)
    #   - SOFT flag:   note when strike is also above analyst PT — that's
    #                  a bonus safety marker, not a gate.
    # Callers that want the ultra-conservative "above analyst PT too"
    # behavior can set `min_strike_above_analyst_pt: true` in engineered.
    if not spot or spot <= 0:
        return (False, empty_envelope, ["spot missing"])
    baseline = spot * (1 + float(eng.get("min_otm_pct", 13)) / 100.0)
    candidates = [baseline]
    if eng.get("require_rebound_proof", True):
        if sma_200 and sma_200 > spot:
            candidates.append(sma_200)
    # Optional ultra-conservative: also require above analyst PT
    if eng.get("min_strike_above_analyst_pt", False):
        if analyst_pt and analyst_pt > spot:
            candidates.append(analyst_pt)
    min_strike = max(candidates)
    # Round up to nearest $5 for cleaner chain matching
    min_strike = ((int(min_strike / 5) + 1) * 5)

    # Note whether the resulting strike is also above analyst PT — a "bonus"
    # safety signal the caller can surface in the recommendation.
    above_analyst_pt = bool(analyst_pt and min_strike > analyst_pt)

    envelope = {
        "applies": True,
        "ticker": ticker.upper(),
        "min_strike": min_strike,
        "min_strike_pct_otm": round((min_strike / spot - 1) * 100, 1),
        "max_delta": float(eng.get("max_delta", 0.15)),
        "max_dte": int(eng.get("max_dte", 21)),
        "coverage_cap_pct": float(eng.get("coverage_cap_pct", 20)),
        "above_analyst_pt": above_analyst_pt,
        "rationale": (
            f"Tier A ENGINEERED CC — spot ${spot:.2f}, "
            f"strike ≥ ${min_strike} ({round((min_strike/spot-1)*100)}% OTM), "
            + (f"rebound-proof above 200-SMA ${sma_200:.0f}"
               if sma_200 else f"≥{eng.get('min_otm_pct', 13)}% OTM baseline")
            + (f" (bonus: also above analyst PT ${analyst_pt:.0f})"
               if above_analyst_pt and analyst_pt else "")
            + f". Cover ≤{eng.get('coverage_cap_pct', 20)}% of shares, "
            f"DTE ≤{eng.get('max_dte', 21)}."
        ),
    }
    return (True, envelope, ["engineered gates all pass"])


def is_cc_enabled_for_tier(tier: str, config: dict | None,
                            ticker: str | None = None) -> bool:
    """True if covered-call recommendations are allowed for this tier
    OR (Tier A only) for the specific ticker via per-name override.

    Tier A's default is False (no CCs on long-term core compounders).
    But the user can OPT IN for specific Tier A names via config:

        covered_call_tiers:
          tier_a:
            enabled: false                # default: no
            willing_to_write_cc_on:       # explicit opt-in list
              - NVDA                      # write conservative CCs
              - MSFT

    When the ticker is on the willing list, we return True regardless of
    the tier default — but the STRICT envelope (max_delta ≤ 0.10,
    min_otm_pct ≥ 20, coverage_cap_pct ≤ 20, max_dte ≤ 30, rsi_floor ≥ 75)
    still applies via cc_settings_for_tier. Callers MUST use the strict
    envelope for these opt-in tier-A writes.

    Tier B / C default to True. Ticker override is Tier A only — for
    Tier B/C, the ticker parameter is ignored.
    """
    settings = cc_settings_for_tier(tier, config)
    base_enabled = bool(settings.get("enabled", True))
    if base_enabled:
        return True
    # Tier A opt-in check
    if tier and tier.upper() == "A" and ticker:
        whitelist = settings.get("willing_to_write_cc_on") or []
        if isinstance(whitelist, list):
            whitelist_upper = {str(t).upper() for t in whitelist}
            if ticker.upper() in whitelist_upper:
                return True
    return False


def concentration_cap_for_tier(tier: str, config: dict | None) -> float:
    """Return the per-tier concentration cap as a percentage of NLV.

    Reads `concentration_caps.tier_{a,b,c}_max_pct` from config, falling
    back to the canonical defaults. Tier A's default (~22%) is intentionally
    permissive — capping a conviction compounder at 10% fights the strategy.

    Returns a float percent (e.g. 22.0 means 22% of NLV), NOT a 0..1 ratio.
    """
    t = (tier or _DEFAULT_TIER).upper().strip()
    if t not in _VALID_TIERS:
        t = _DEFAULT_TIER
    fallback = float(_FALLBACK_CONCENTRATION_CAPS[t])
    cfg = _conc_caps_block(config)
    if not cfg:
        return fallback
    key = f"tier_{t.lower()}_max_pct"
    v = cfg.get(key)
    if v is None:
        # Honor a generic `default_max_pct` if specified
        v = cfg.get("default_max_pct")
    try:
        return float(v) if v is not None else fallback
    except (TypeError, ValueError):
        return fallback


def format_tier_badge(tier: str) -> str:
    """Return the compact visual badge for a tier (e.g. '🟢 Tier A')."""
    t = (tier or _DEFAULT_TIER).upper().strip()
    return _TIER_BADGES.get(t, _TIER_BADGES[_DEFAULT_TIER])


# ─── Markdown annotator — mirrors parkev_chip.annotate_parkev_chips ───────


# Re-use the Parkev marker so we only annotate lines that have a chip.
_PARKEV_MARK = "🅿️"

# Match any line that already carries our tier badge so we never
# double-annotate.
_TIER_BADGE_RE = re.compile(r"🟢 Tier A|🟡 Tier B|🔵 Tier C")

# Extract the FIRST plausible ticker on a line — same precedence as
# parkev_chip._extract_ticker (backticks > bold > options-prefix). We
# duplicate (rather than import) to keep this module standalone.
_TICKER_BACKTICK = re.compile(r"`([A-Z][A-Z0-9]{0,4})`")
_TICKER_BOLD = re.compile(r"\*\*([A-Z][A-Z0-9]{0,4})\*\*")
_TICKER_OPT = re.compile(r"\b([A-Z][A-Z0-9]{0,4})_(?:PUT|CALL)(?=_|\s|$)")
_TICKER_BARE = re.compile(r"\b([A-Z][A-Z0-9]{0,4})\b")

# Same stoplist of action verbs / option types / unit words that
# parkev_chip._extract_ticker excludes from the bare-token fallback.
_NON_TICKER_TOKENS = frozenset({
    "CLOSE", "OPEN", "ROLL", "BUY", "SELL", "HOLD", "EXIT", "HEDGE", "TRIM",
    "ADD", "EXEC", "ENTER", "WAIT", "WATCH", "AVOID", "DEFER", "SKIP",
    "PUT", "CALL", "ITM", "OTM", "ATM", "CSP", "CC", "DTE", "ER",
    "RSI", "SMA", "EMA", "ATR", "ADX", "MACD", "PE", "PEG", "EPS", "FCF",
    "IV", "DCF", "FV", "PT", "EV", "PL", "ROI",
    "PASS", "FAIL", "BLOCK", "WARN", "OK", "GOOD", "BAD", "MEH",
    "TOP", "MED", "LOW", "HIGH", "NEW", "OLD", "GTC", "DAY",
    "NA", "TBD", "FOMC", "CPI", "PPI", "GDP", "PMI",
    "NLV", "MV", "BTC", "STO", "BTO", "STC",
    "LTCG", "STCG", "IRA", "ROTH", "API", "ETF",
    "MCP", "FMP", "FAQ", "AKA", "TLDR", "TLR",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "USD", "EUR", "JPY", "GBP", "CAD",
    "AI", "ML", "LLM", "AGI", "OS", "UX", "UI", "VR", "AR", "MR",
    "S", "R", "P", "C", "A", "B", "K", "M", "T",
    # Tier labels — could appear on lines we're trying to annotate
    "TIER",
})


def _extract_ticker(line: str) -> str | None:
    """Find the first plausible ticker on the line, using the same
    precedence as parkev_chip._extract_ticker (backticks > bold > options-
    prefix > bare). Returns None if nothing plausible is found."""
    m = _TICKER_BACKTICK.search(line)
    if m:
        return m.group(1)
    m = _TICKER_BOLD.search(line)
    if m and m.group(1) not in _NON_TICKER_TOKENS:
        return m.group(1)
    m = _TICKER_OPT.search(line)
    if m and m.group(1) not in _NON_TICKER_TOKENS:
        return m.group(1)
    for tok in _TICKER_BARE.findall(line):
        if tok not in _NON_TICKER_TOKENS and len(tok) >= 2:
            return tok
    return None


def annotate_tier_badges(md: str, config: dict | None) -> str:
    """Walk the rendered briefing markdown and append ` · 🟢 Tier A` (or B/C)
    to every line that already carries a 🅿️ Parkev chip.

    Behavior:
      - When `position_tiers` config is missing/empty, this is a NO-OP —
        the briefing renders byte-identical to the pre-framework baseline.
        Without explicit tier assignments the default-everything-to-Tier-C
        annotation would noisily mark every chip line with `🔵 Tier C`,
        which isn't useful information and breaks backward compatibility
        for users who haven't opted in to the framework.
      - Lines without a Parkev chip are left alone (the chip annotator
        precedes us, so a chip-less header is something the chip annotator
        intentionally skipped — usually prose or a sub-bullet).
      - Lines already carrying a tier badge are left alone (no double-
        annotation).
      - Ticker extracted with the same precedence as parkev_chip
        (backticks → bold → options-prefix → bare-token + stoplist).
      - When no ticker can be extracted, the line passes through unchanged.
    """
    if not md:
        return md
    # Backward-compatibility guarantee: no tier config → no tier badges.
    if not _position_tiers_block(config):
        return md
    out_lines = []
    for line in md.splitlines():
        # Must have a Parkev chip already AND not already have a tier badge.
        if _PARKEV_MARK in line and not _TIER_BADGE_RE.search(line):
            tk = _extract_ticker(line)
            if tk:
                tier = tier_for(tk, config)
                badge = format_tier_badge(tier)
                line = f"{line} · {badge}"
        out_lines.append(line)
    return "\n".join(out_lines)
