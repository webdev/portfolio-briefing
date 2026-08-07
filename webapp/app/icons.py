"""Action-type iconography for the briefing dashboard.

The pipeline's briefing markdown surfaces action verbs in bare uppercase
(CLOSE, ROLL_OUT, HEDGE, PULLBACK CSP, etc.) — readable but visually flat.
The web app prefixes each verb with a one-glyph icon so the user can scan
the action list and instantly recognize the SHAPE of each line (a HEDGE
buy is visually distinct from a CLOSE close, etc.).

Icon choices intentionally AVOID collision with the existing chip
vocabulary (CLAUDE.md hard rules #27 + #29):
    🅿️  = Parkev attribution marker
    🏆  = TOP CONVICTION (tier 4+5 + High)
    🔥  = High conviction
    ◐ ▽ = Medium / Low conviction
    🟢 🟡 🔵 = Tier A / B / C badges
    🎯  = Candidate marker
    ⏸  = Deferred (capacity gated)
    ⏰  = Stale rec (>14d)
    ⏳  = Ignored N days
    💵  = Fair-value note
    🌟  = STRONG BUY (When-To-Enter label)

Single source of truth for icon mappings — when the pipeline adds a new
action type, add it here in ONE place and every template picks it up.
"""

from __future__ import annotations


# ─── Primary action verbs ────────────────────────────────────────────────
# These are the dominant types shown in the Today's Action List
_ACTION_ICONS = {
    # Closes (lock in profit / exit a contract)
    "CLOSE":             "🔚",   # end / close out
    "CLOSE_NOW":         "🛑",   # urgent stop (loss stop fired)
    "CLOSE NOW":         "🛑",   # alt spelling
    "CLOSE_FOR_PROFIT":  "💰",   # bank profit
    "TAKE_PROFIT":       "💰",
    "TAKE PROFIT":       "💰",

    # Rolls (BTC + STO same name)
    "ROLL_OUT":          "🔄",   # roll forward in time
    "ROLL OUT":          "🔄",
    "ROLL_UP":           "⤴️",  # up-arrow rotation (strike up)
    "ROLL_DOWN":         "⤵️",  # down-arrow rotation (strike down)
    "ROLL_UP_AND_OUT":   "⤴️",
    "ROLL_DOWN_AND_OUT": "⤵️",
    "EXECUTE_ROLL":      "🔁",  # repeat cycle (decisive roll)
    "EXECUTE ROLL":      "🔁",
    "DEFENSIVE_ROLL":    "🛡️",  # shield = defensive
    "DEFENSIVE ROLL":    "🛡️",
    "DEFENSIVE ROLL (core override)": "🛡️",

    # Hedges (buy protection)
    "HEDGE":             "🛡️",  # shield = insurance
    "DEFENSIVE_COLLAR":  "🛡️",
    "DEFENSIVE COLLAR":  "🛡️",
    "COLLAR":            "🛡️",  # strategy-upgrade collar proposal (task #17)

    # Trims (reduce concentration / cash out)
    "TRIM":              "✂️",
    "LT_TRIM":           "📉",  # trim downward
    "LT TRIM":           "📉",

    # Adds (build position)
    "ADD":               "➕",
    "LT_ADD":            "📈",  # add upward
    "LT ADD":            "📈",

    # Exits (sell whole equity position)
    "EXIT":              "🚪",  # door out
    "EXIT_NOW":          "🚪",

    # Reviews / converts / waits
    "REVIEW":            "🔍",
    "REVIEW_CORE":       "🔍",
    "REVIEW CORE":       "🔍",
    "CONVERT":           "🔀",  # swap arrows
    "HOLD":              "✋",  # stop/hold hand
    "WATCH":             "👀",
    "WAIT":              "⌛",
    "AVOID":             "🚫",
    "MONITOR":           "👀",

    # CSP entry verbs (open new short put)
    "PULLBACK_CSP":      "💎",  # gem = quality entry
    "PULLBACK CSP":      "💎",
    "CSP — PAID-TO-WAIT": "💎",  # renamed render label (kind stays PULLBACK_CSP)
    "NEW_CSP":           "✍️",  # write/open new
    "NEW CSP":           "✍️",
    "LONG_DATED_CSP":    "📅",  # calendar = long horizon
    "LONG DATED CSP":    "📅",
    "LT_CSP":            "📅",

    # Strategy upgrades
    "write_covered_call":  "📝",  # write a call against shares
    "WRITE_COVERED_CALL":  "📝",
    "WRITE COVERED CALL":  "📝",
    "index_covered_call":  "🗂",  # index CC (SPY/VOO/QQQ — rule #34 envelope)
    "INDEX_COVERED_CALL":  "🗂",
    "sublot_completion":   "🧩",  # complete the lot
    "SUBLOT_COMPLETION":   "🧩",
    "SUB_LOT":             "🧩",  # merged-ideas kind (task #17)
    "tier_a_no_cc":        "🟢",  # already in chip vocabulary
    "TIER_A_NO_CC":        "🟢",

    # Long-term opportunity kinds
    "DEFERRED_ADD_HAS_CSP": "⏸",
    "SKIPPED_ADD":          "⏭️",
    "SKIPPED_LT_CSP":       "⏭️",
    "SKIPPED_RSI":          "⏭️",
    "SKIPPED_LT_VERDICT":   "⏭️",
    "SKIPPED_MV_FV":        "⏭️",
    "_FUNDING_HINT":        "💵",
    "reference_demoted":    "📎",  # rule #43 — reference-only LTO card
    "REFERENCE_DEMOTED":    "📎",
}


# ─── Human-readable labels for machine identifiers ──────────────────────
# Pipeline emits snake_case / SCREAMING_SNAKE_CASE kinds like
# `tier_a_no_cc`, `sublot_completion`, `DEFERRED_ADD_HAS_CSP`. These are
# fine in JSON/code but UNREADABLE in the UI. CLAUDE.md hard rule #31:
# every machine identifier surfaced to the user must pass through
# humanize_action() to get a proper Title-Cased human label.
_ACTION_LABELS = {
    # Snake-case strategy upgrade types
    "write_covered_call":     "Write Covered Call",
    "index_covered_call":     "Index Covered Call",
    "sublot_completion":      "Complete Lot",
    "SUB_LOT":                "Sub-lot Completion",
    "collar":                 "Collar",
    "tier_a_no_cc":           "Tier A — no CC",
    "tier_b_conservative":    "Tier B — conservative CC",
    "tier_c_standard":        "Tier C — standard CC",
    # SCREAMING_SNAKE long-term opportunity kinds
    "DEFERRED_ADD_HAS_CSP":   "Deferred (held put)",
    "SKIPPED_ADD":            "Skipped — ADD",
    "SKIPPED_LT_CSP":         "Skipped — LT CSP",
    "SKIPPED_RSI":            "Skipped — RSI gate",
    "SKIPPED_LT_VERDICT":     "Skipped — LT-trend gate",
    "SKIPPED_MV_FV":          "Skipped — above MV fair value",
    "reference_demoted":      "Reference — not actionable today",
    "REFERENCE_DEMOTED":      "Reference — not actionable today",
    "LONG_DATED_CSP":         "Long-Dated CSP",
    "_FUNDING_HINT":          "Funding hint",
    # Composite verbs
    "CLOSE_NOW":              "Close Now",
    "CLOSE_FOR_PROFIT":       "Close for Profit",
    "TAKE_PROFIT":            "Take Profit",
    "EXECUTE_ROLL":           "Execute Roll",
    "DEFENSIVE_ROLL":         "Defensive Roll",
    "DEFENSIVE_COLLAR":       "Defensive Collar",
    "ROLL_OUT":               "Roll Out",
    "ROLL_UP":                "Roll Up",
    "ROLL_DOWN":              "Roll Down",
    "ROLL_UP_AND_OUT":        "Roll Up & Out",
    "ROLL_DOWN_AND_OUT":      "Roll Down & Out",
    "LT_TRIM":                "LT Trim",
    "LT_ADD":                 "LT Add",
    "LT_CSP":                 "LT CSP",
    "NEW_CSP":                "New CSP",
    # 2026-08-04 rename: "Pullback CSP" read as "the stock is pulling back
    # now" — it's a strategy name (sell below-spot put, get paid to wait).
    "PULLBACK_CSP":           "CSP — Paid-to-Wait",
    "PULLBACK CSP":           "CSP — Paid-to-Wait",
    "CSP — PAID-TO-WAIT":     "CSP — Paid-to-Wait",
    "REVIEW_CORE":            "Review Core",
    "EXIT_NOW":               "Exit Now",
    # Task #40 (2026-07-30) action kinds
    "HOLD_FOR_BASIS":         "Hold for Basis (assignment OK)",
    "HOLD FOR BASIS":         "Hold for Basis (assignment OK)",
    "CLOSE_INTO_RECOVERY":    "Close into Recovery (pre-print)",
    "CLOSE INTO RECOVERY":    "Close into Recovery (pre-print)",
}


def humanize_action(verb: str | None) -> str:
    """Translate a machine identifier to a human-readable label.

    Examples:
        humanize_action("tier_a_no_cc")  → "Tier A — no CC"
        humanize_action("CLOSE_NOW")     → "Close Now"
        humanize_action("LT_TRIM")       → "LT Trim"
        humanize_action("CLOSE")         → "CLOSE"        # already readable
        humanize_action("unknown_verb")  → "Unknown verb" # generic fallback

    Strategy:
        1. Exact-match against the curated label table (most reliable).
        2. Common-case fallbacks: snake_case → Title Case, ALL_CAPS → Title
           If the verb is already Title or UPPER without underscores
           (CLOSE / HEDGE / TRIM), keep as-is.
        3. None / empty → "" (caller handles gracefully).
    """
    if not verb:
        return ""
    s = str(verb).strip()
    if not s:
        return ""
    # Exact match (preserves case-sensitive lookups like 'tier_a_no_cc')
    if s in _ACTION_LABELS:
        return _ACTION_LABELS[s]
    # Try upper-case lookup
    upper = s.upper()
    if upper in _ACTION_LABELS:
        return _ACTION_LABELS[upper]
    # Generic fallback for snake_case (e.g., new_strategy_type)
    if "_" in s:
        # Lower-case snake → Title with spaces
        return " ".join(word.capitalize() for word in s.lower().split("_"))
    # Plain uppercase verb (CLOSE / HEDGE / TRIM / etc.) — already readable
    return s


# ─── Severity / priority flags (Red Flags + Risk Alerts) ────────────────
_SEVERITY_ICONS = {
    "CRITICAL":   "🚨",
    "HIGH":       "🚨",
    "WARNING":    "⚠️",
    "MEDIUM":     "📊",
    "INFO":       "ℹ️",
    "LOW":        "ℹ️",
    "READY":      "✅",
    "BLOCKED":    "🚫",
    "PASS":       "✅",
    "FAIL":       "❌",
    "OK":         "✅",
    "DEFERRED":   "⏸",
}


# ─── Position / option state ─────────────────────────────────────────────
_STATE_ICONS = {
    "LONG":     "📈",   # bullish/long
    "SHORT":    "📉",   # bearish/short
    "EQUITY":   "🏦",
    "OPTION":   "📜",
    "PUT":      "🔻",
    "CALL":     "🔺",
    "ITM":      "🎯",
    "OTM":      "⊙",
    "ATM":      "═",
}


def action_icon(action_type: str | None) -> str:
    """Return the icon for an action verb, or '' if unknown.

    Case-insensitive on the canonical form; tolerates both underscore and
    space spellings (CLOSE_NOW vs CLOSE NOW). Returns empty string for
    unknown types so the template renders the bare verb instead of a
    placeholder — fail-closed per CLAUDE.md hard rule #19 (no fabricated
    data). Callers can use ``{{ action_type | action_icon or '' }}`` to
    safely default.
    """
    if not action_type:
        return ""
    raw = str(action_type).strip()
    # Direct hit (case-preserving, common)
    if raw in _ACTION_ICONS:
        return _ACTION_ICONS[raw]
    # Case-insensitive hit
    upper = raw.upper()
    if upper in _ACTION_ICONS:
        return _ACTION_ICONS[upper]
    # Normalize underscore ↔ space
    if "_" in upper:
        alt = upper.replace("_", " ")
        if alt in _ACTION_ICONS:
            return _ACTION_ICONS[alt]
    elif " " in upper:
        alt = upper.replace(" ", "_")
        if alt in _ACTION_ICONS:
            return _ACTION_ICONS[alt]
    return ""


def severity_icon(severity: str | None) -> str:
    """Return the icon for a severity flag, or '' if unknown."""
    if not severity:
        return ""
    upper = str(severity).strip().upper()
    return _SEVERITY_ICONS.get(upper, "")


def state_icon(state: str | None) -> str:
    """Return the icon for a position/option state, or '' if unknown."""
    if not state:
        return ""
    upper = str(state).strip().upper()
    return _STATE_ICONS.get(upper, "")


def action_variant(action_type: str | None, summary: str | None = None) -> dict:
    """Classify an action into (icon, label, tone) based on the verb + P&L sign.

    Motivation: the pipeline renders both "loss-stop CLOSE" and "take-profit
    CLOSE" under the same `CLOSE` verb. The user can't tell them apart in the
    action queue. This helper inspects the summary line for a signed P&L
    prefix (e.g. "+51%" or "-12%") and returns a variant-aware label:

      CLOSE with summary "+51% ..." → {"icon": "💰", "label": "CLOSE (profit)", "tone": "ok"}
      CLOSE with summary "-15% ..." → {"icon": "🚨", "label": "CLOSE (loss stop)", "tone": "bad"}
      CLOSE with no P&L in summary  → {"icon": "🔚", "label": "CLOSE", "tone": "muted"}

    Non-CLOSE verbs return their standard icon + humanized label.
    Answers user question 2026-06-30: "why are CLOSEs firing at 30%?"
    """
    if not action_type:
        return {"icon": "", "label": "", "tone": "muted"}
    kind = str(action_type).strip().upper()
    if kind == "CLOSE" and summary:
        import re
        # The pipeline summary format is:
        #   "**CLOSE** IDENT — +51% ($+3,823); buy-to-close ..."
        # The P&L sign lives AFTER the em-dash. Match it there.
        m = re.search(r"[—\-]\s*([+-])(\d+(?:\.\d+)?)%", summary)
        if m:
            sign = m.group(1)
            if sign == "+":
                return {
                    "icon": "💰", "label": "CLOSE (profit)", "tone": "ok",
                    "note": "Take-profit CLOSE — position hit a wheel-take-profit threshold.",
                }
            return {
                "icon": "🚨", "label": "CLOSE (loss stop)", "tone": "bad",
                "note": "Loss-stop CLOSE — position underwater past the loss threshold.",
            }
    # Non-CLOSE or unclassifiable: fall back to standard icon + label
    return {
        "icon": action_icon(kind),
        "label": humanize_action(kind),
        "tone": "muted",
    }


def action_with_icon(action_type: str | None, *, default: str = "") -> str:
    """Return ``f"{icon} {human_label}"`` for a machine action type.

    Used in templates as ``{{ action_type | action_with_icon }}`` to render
    e.g. ``"🔚 CLOSE"`` or ``"🟢 Tier A — no CC"``. The icon comes from
    `action_icon()` and the label comes from `humanize_action()` so snake_case
    and SCREAMING_SNAKE types both render as proper Title Case in the UI
    (CLAUDE.md hard rule #31). Returns just the human label when no icon
    mapping exists — never a bare empty string."""
    if not action_type:
        return default
    icon = action_icon(action_type)
    label = humanize_action(action_type)
    if icon:
        return f"{icon} {label}"
    return label


def register_jinja_filters(env) -> None:
    """Register icon helpers as Jinja filters so templates can use:

        {{ action_type | action_icon }}            # just the icon
        {{ action_type | action_with_icon }}       # "🔚 CLOSE"
        {{ severity   | severity_icon }}           # just the icon
        {{ state      | state_icon }}              # just the icon
    """
    env.filters["action_icon"] = action_icon
    env.filters["severity_icon"] = severity_icon
    env.filters["state_icon"] = state_icon
    env.filters["action_with_icon"] = action_with_icon
    # CLAUDE.md hard rule #31 — machine identifiers like `tier_a_no_cc` get
    # humanized before display via this filter or action_with_icon.
    env.filters["humanize_action"] = humanize_action
    # Variant-aware label: distinguishes CLOSE (profit) vs CLOSE (loss stop)
    # based on the summary's leading P&L sign. Returns a dict with keys
    # icon / label / tone / note — use as {{ (a.kind, a.summary) | action_variant_call }}
    # via a Jinja global (function), not a filter (which only takes one arg).
    env.globals["action_variant"] = action_variant
