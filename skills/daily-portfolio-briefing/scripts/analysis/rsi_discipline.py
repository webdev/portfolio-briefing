"""RSI discipline — single source of truth for how RSI gates and annotates
wheel recommendations.

A disciplined wheel trader reads RSI *asymmetrically* across the two sides of
the wheel:

  Selling cash-secured PUTS (you get paid to maybe buy the dip):
    - Favourable in a moderate pullback (RSI ~30-50): premium is elevated and
      you're being paid to buy lower.
    - Dangerous when OVERBOUGHT (RSI > 70): you collect the thinnest premium
      right before a reversal can whip the stock down through your strike.
      → BLOCK new opens.
    - Risky in a momentum break (RSI < 25, "falling knife"): the dip may not
      hold. → WARN.

  Selling covered CALLS (you cap upside for premium):
    - Favourable when EXTENDED / overbought (RSI > 60): premium is rich and the
      move up is statistically stretched, so the cap is cheap.
    - Poor when OVERSOLD (RSI < 35): you'd cap a name right before a likely
      bounce. → BLOCK new opens.

The hard gate applies ONLY to NEW opens. Existing-position management (rolls /
closes / trims / collars) is NEVER blocked — RSI is shown for context only.

Bands are config-overridable via ``briefing.yaml`` ``rsi_discipline``. The
defaults below are the "standard wheel bands."
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

PUT = "put"
CALL = "call"
BUY = "buy"        # buying shares (equity ADD, sub-lot completion, thematic buy)

# Recommendation "sides" that are NEW exposure and therefore gated. Anything
# not in here (close / roll / trim / exit / hedge / collar / hold) is treated
# as position management — annotated, never removed.
NEW_OPEN_SIDES = {PUT, CALL, BUY}

# "Standard wheel bands" — see module docstring.
DEFAULT_THRESHOLDS = {
    "enabled": True,
    "put": {
        "block_above": 70.0,          # overbought — suppress new put-sales
        "caution_above": 60.0,        # getting extended — warn
        "favored_high": 50.0,
        "favored_low": 30.0,
        "falling_knife_below": 25.0,  # momentum break — warn
    },
    "call": {
        "favored_above": 60.0,        # extended — rich premium, favored for CC
        "caution_below": 60.0,        # mid-range — warn (caps upside cheaply)
        "block_below": 35.0,          # oversold — suppress new covered calls
    },
    "buy": {
        "block_above": 70.0,          # overbought — don't chase a new equity buy
        "caution_above": 60.0,        # extended — warn (prefer a deeper pullback)
    },
    # Extended-band demotion for NEW put-sales (rule #43 batch, 2026-07-31
    # AMZN card): RSI 60-70 = "extended — wait for a pullback to enter"
    # (asymmetric framework, hard rule #11 + "Everything actionable"). A new
    # put-sale in this band is demoted to a "⏸ CSPs — wait for a pullback"
    # subsection — full ticket shown (rule #24), never a numbered green-lit
    # rec. Set to null in briefing.yaml to disable. The >70 hard block is
    # unchanged and takes precedence.
    "put_extended_wait_band": [60.0, 70.0],
}


def load_thresholds(config: dict | None) -> dict:
    """Merge the user's ``rsi_discipline`` config over the standard-wheel defaults."""
    merged = copy.deepcopy(DEFAULT_THRESHOLDS)
    if not config:
        return merged
    user = config.get("rsi_discipline") or {}
    if "enabled" in user:
        merged["enabled"] = bool(user["enabled"])
    for side in ("put", "call", "buy"):
        for key, val in (user.get(side) or {}).items():
            if val is not None and key in merged[side]:
                merged[side][key] = float(val)
    # Nullable: `put_extended_wait_band: null` disables the extended-band
    # demotion of new put-sales entirely (the >70 hard block stays).
    if "put_extended_wait_band" in user:
        band = user["put_extended_wait_band"]
        try:
            merged["put_extended_wait_band"] = (
                [float(band[0]), float(band[1])] if band else None
            )
        except (TypeError, ValueError, IndexError):
            merged["put_extended_wait_band"] = None
    return merged


@dataclass
class RsiAssessment:
    """Result of evaluating an RSI value for one side of the wheel."""

    rsi: float | None
    side: str       # "put" or "call"
    zone: str       # favored | neutral | caution | blocked | unknown
    action: str     # allow | warn | block   (only meaningful for NEW opens)
    label: str      # human word: overbought / pullback / oversold / ...
    reason: str     # full sentence for footers / analyst brief
    tag: str        # compact display: "RSI 72 🔴 overbought"

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def _signal_word(rsi: float, side: str | None, th: dict) -> tuple[str, str]:
    """Return (word, emoji) describing what this RSI means for ``side``.

    ``side`` None → side-agnostic read (used for equities / management lines).
    Returns ("", "") when there is no notable signal (so the tag stays terse).
    """
    if side == PUT:
        p = th["put"]
        if rsi >= p["block_above"]:
            return "overbought", "🔴"
        if rsi >= p["caution_above"]:
            return "extended", "🟡"
        if p["favored_low"] <= rsi <= p["favored_high"]:
            return "pullback", "🟢"
        if rsi < p["falling_knife_below"]:
            return "deeply oversold", "🟡"
        return "", ""
    if side == CALL:
        c = th["call"]
        if rsi < c["block_below"]:
            return "oversold", "🔴"
        if rsi < c["caution_below"]:
            return "mid-range", "🟡"
        return "extended", "🟢"
    if side == BUY:
        b = th["buy"]
        if rsi >= b["block_above"]:
            return "overbought", "🔴"
        if rsi >= b["caution_above"]:
            return "extended", "🟡"
        return "pullback", "🟢"
    # side-agnostic (equity / management context)
    if rsi >= 70:
        return "overbought", "🔴"
    if rsi >= 60:
        return "extended", "🟡"
    if rsi <= 30:
        return "oversold", "🟡"
    return "", ""


def tag(rsi: float | None, side: str | None = None, thresholds: dict | None = None) -> str:
    """Compact display string, e.g. ``RSI 72 🔴 overbought`` or ``RSI 54``."""
    if rsi is None:
        return "RSI n/a"
    th = thresholds or DEFAULT_THRESHOLDS
    base = f"RSI {rsi:.0f}"
    word, emoji = _signal_word(float(rsi), side, th)
    return f"{base} {emoji} {word}" if word else base


def assess(rsi: float | None, side: str, thresholds: dict | None = None) -> RsiAssessment:
    """Evaluate ``rsi`` for one side of the wheel ("put" = selling CSPs,
    "call" = selling covered calls)."""
    th = thresholds or DEFAULT_THRESHOLDS
    side = (side or "").lower()
    if side not in (PUT, CALL, BUY):
        side = PUT

    if rsi is None:
        return RsiAssessment(
            None, side, "unknown", "allow", "n/a",
            "RSI unavailable — no RSI gate applied; verify momentum manually.",
            "RSI n/a",
        )

    rsi = float(rsi)
    if side == BUY:
        b = th["buy"]
        if rsi >= b["block_above"]:
            zone, action, label = "blocked", "block", "overbought"
            reason = (
                f"RSI {rsi:.0f} is overbought (>{b['block_above']:.0f}) — buying here "
                "chases an extended move; wait for a pullback. New buy blocked."
            )
        elif rsi >= b["caution_above"]:
            zone, action, label = "caution", "warn", "extended"
            reason = (
                f"RSI {rsi:.0f} is extended (>{b['caution_above']:.0f}) — a better entry "
                "usually comes on a pullback. Scale in small or wait."
            )
        else:
            zone, action, label = "favored", "allow", "pullback"
            reason = (
                f"RSI {rsi:.0f} is in the pullback/neutral zone — a favourable spot to "
                "add to the position."
            )
        return RsiAssessment(rsi, side, zone, action, label, reason, tag(rsi, side, th))

    if side == PUT:
        p = th["put"]
        if rsi >= p["block_above"]:
            zone, action, label = "blocked", "block", "overbought"
            reason = (
                f"RSI {rsi:.0f} is overbought (>{p['block_above']:.0f}) — selling a "
                "put here collects the thinnest premium right before a reversal can "
                "whip the stock down through the strike. New put-sale blocked."
            )
        elif rsi >= p["caution_above"]:
            zone, action, label = "caution", "warn", "extended"
            reason = (
                f"RSI {rsi:.0f} is extended (>{p['caution_above']:.0f}) — put premium is "
                "getting thin and a pullback would pressure the strike. Size down or wait."
            )
        elif rsi < p["falling_knife_below"]:
            zone, action, label = "caution", "warn", "deeply oversold"
            reason = (
                f"RSI {rsi:.0f} is deeply oversold (<{p['falling_knife_below']:.0f}) — "
                "momentum is breaking and the dip may not hold (falling-knife risk). "
                "Confirm support before selling the put."
            )
        elif p["favored_low"] <= rsi <= p["favored_high"]:
            zone, action, label = "favored", "allow", "pullback"
            reason = (
                f"RSI {rsi:.0f} is in the pullback zone "
                f"({p['favored_low']:.0f}-{p['favored_high']:.0f}) — elevated premium and a "
                "favourable spot to get paid to maybe buy lower."
            )
        else:  # 25-30 or 50-60
            zone, action, label = "neutral", "allow", "neutral"
            reason = f"RSI {rsi:.0f} is neutral — no strong momentum signal for a new put-sale."
    else:  # CALL
        c = th["call"]
        if rsi < c["block_below"]:
            zone, action, label = "blocked", "block", "oversold"
            reason = (
                f"RSI {rsi:.0f} is oversold (<{c['block_below']:.0f}) — writing a new "
                "covered call here caps the name right before a likely bounce. New CC blocked."
            )
        elif rsi < c["caution_below"]:
            zone, action, label = "caution", "warn", "mid-range"
            reason = (
                f"RSI {rsi:.0f} is mid-range (<{c['caution_below']:.0f}) — premium is only "
                "average and you'd cap upside without much compensation. Prefer waiting for strength."
            )
        else:
            zone, action, label = "favored", "allow", "extended"
            reason = (
                f"RSI {rsi:.0f} is extended (≥{c['favored_above']:.0f}) — rich call premium and a "
                "statistically stretched move up make this a favourable spot to write."
            )

    return RsiAssessment(rsi, side, zone, action, label, reason, tag(rsi, side, th))


def put_extended_wait(rsi: float | None, thresholds: dict | None = None) -> str | None:
    """Extended-band demotion check for NEW put-sales (single source of truth).

    When ``rsi`` sits in the ``put_extended_wait_band`` (default 60-70), a new
    put-sale rec (LT_CSP / LONG_DATED_CSP / PULLBACK_CSP / income opportunity /
    candidate CSP entry) must NOT render as a numbered actionable rec — it
    demotes to a "⏸ CSPs — wait for a pullback" subsection with the full
    ticket still shown (hard rule #24). Returns the ⏸ reason string, or None
    when: RSI unknown (fail-open), the band is disabled (config null), or RSI
    is outside the band. The ≥70 hard block (``assess``/``hook``) is separate
    and unchanged — this band is half-open [lo, hi) so the two never overlap.

    Origin (rule #43, 2026-07-31 briefing): AMZN LONG DATED CSP rendered as
    numbered rec #6 with "RSI 66 🟡 extended" — annotated but still green-lit,
    selling a put into a +13.7% earnings-gap green streak.
    """
    th = thresholds or DEFAULT_THRESHOLDS
    band = th.get("put_extended_wait_band", DEFAULT_THRESHOLDS["put_extended_wait_band"])
    if not band or rsi is None:
        return None
    try:
        lo, hi = float(band[0]), float(band[1])
    except (TypeError, ValueError, IndexError):
        return None
    r = float(rsi)
    if lo <= r < hi:
        return (
            f"⏸ RSI {r:.0f} extended — selling puts into a green streak sets the "
            f"strike against an inflated spot; wait for RSI 35-55 / a red day."
        )
    return None


# ---------------------------------------------------------------------------
# Lookup + rendered-line helpers (used by the renderers)
# ---------------------------------------------------------------------------

def rsi_for(ticker: str | None, technicals: dict | None) -> float | None:
    """Return rsi_14 for ``ticker`` from a snapshot ``technicals`` dict, or None."""
    if not ticker or not technicals:
        return None
    entry = technicals.get(ticker) or technicals.get(ticker.upper())
    if isinstance(entry, dict):
        return entry.get("rsi_14")
    return None


def market_state(rsi: float | None) -> str:
    """Side-agnostic market-state word for an RSI value.

    Used by strategy explainers (e.g. the "CSP — PAID-TO-WAIT" card) that must
    state the stock's CURRENT state plainly — separate from the side-aware
    entry-band language ("pullback zone — favourable") which describes the
    ENTRY BAND, not the market. The observed confusion (2026-08-04): George
    read "PULLBACK CSP NVDA" as "NVDA is in a pullback now" when NVDA sat at
    RSI 53 — a neutral tape. Bands mirror the "Everything actionable" rule:
    ≥70 overbought · 60-70 extended · 50-60 neutral · 35-50 pullback ·
    25-35 oversold · <25 falling knife.
    """
    if rsi is None:
        return "unknown"
    r = float(rsi)
    if r >= 70:
        return "overbought"
    if r >= 60:
        return "extended"
    if r >= 50:
        return "neutral"
    if r >= 35:
        return "pullback"
    if r >= 25:
        return "oversold"
    return "falling knife"


def first_known_ticker(text: str, tickers: list[str]) -> str | None:
    """Return the first known ticker mentioned in ``text``.

    ``tickers`` should be pre-sorted longest-first so GOOGL matches before GOOG.
    Boundaries treat anything outside ``[A-Za-z0-9]`` as a separator, so the
    ticker is found inside an option contract token like ``NVDA_PUT_195`` while
    GOOG still won't match inside GOOGL. The boundary is case-INSENSITIVE on
    purpose: with an uppercase-only boundary, single-letter ticker ``T``
    matched the capital T of prose words like "Triggers"/"Time" and attached
    AT&T's data to other tickers' cards (the 2026-07-30 MSFT FV bug — DCF $257
    / PT $25 are T's values). A ticker followed or preceded by a lowercase
    letter is part of an English word, not a ticker mention.
    """
    for tk in tickers:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(tk)}(?![A-Za-z0-9])", text):
            return tk
    return None


def infer_side(text: str) -> str | None:
    """Infer the wheel side from a rendered recommendation line.

    Returns "put", "call", or None (equity / non-option line). Explicit
    put-open phrases win first; otherwise we read the option-type token.
    """
    t = text.upper()
    if "CSP" in t or "PULLBACK CSP" in t or "NEW CSP" in t:
        return PUT
    if "_PUT_" in t or " PUT" in t or re.search(r"\$\d+(?:\.\d+)?P\b", t):
        return PUT
    if (
        "_CALL_" in t
        or " CALL" in t
        or "COVERED CALL" in t
        or re.search(r"\$\d+(?:\.\d+)?C\b", t)
    ):
        return CALL
    return None


@dataclass
class RecVerdict:
    """The central RSI hook's decision for one recommendation.

    decision ∈ {"remove", "keep", "promote"}:
      - remove  : RSI is unfavorable for this NEW open → drop from the action
                  list into a transparency footer (use `reason`).
      - promote : RSI is favorable → render with `badge` ("✅ RSI favourable")
                  and sort ahead of neutral peers.
      - keep    : neutral / caution (or a management line) → render as-is, with
                  `badge` carrying a soft "⚠ RSI caution" when applicable.
    """

    decision: str
    side: str
    rsi: float | None
    tag: str
    reason: str       # full sentence (display + removal-footer text)
    badge: str        # "✅ RSI favourable" | "⚠ RSI caution" | ""

    @property
    def removed(self) -> bool:
        return self.decision == "remove"

    @property
    def promoted(self) -> bool:
        return self.decision == "promote"


def hook(side: str | None, rsi: float | None, thresholds: dict | None = None) -> RecVerdict:
    """The single gate EVERY recommendation passes through.

    `side` is the recommendation's wheel side: "put"/"call"/"buy" for NEW opens
    (gated), or anything else (None, "manage", "close", "roll", "trim", "exit",
    "hedge", "collar", "hold") for position management (annotated, never removed).

    When the RSI gate is disabled in config, callers should bypass this (it
    still returns a sensible "keep" so nothing breaks).
    """
    th = thresholds or DEFAULT_THRESHOLDS
    s = (side or "").lower()

    if s not in NEW_OPEN_SIDES:
        # Management line — annotate, never gate.
        return RecVerdict("keep", "manage", rsi, tag(rsi, None, th), "", "")

    a = assess(rsi, s, th)
    if a.blocked:
        return RecVerdict("remove", a.side, a.rsi, a.tag, a.reason, "")
    if a.zone == "favored":
        return RecVerdict("promote", a.side, a.rsi, a.tag, a.reason, "✅ RSI favourable")
    badge = "⚠ RSI caution" if a.zone == "caution" else ""
    return RecVerdict("keep", a.side, a.rsi, a.tag, a.reason, badge)


# Recommendation-line signatures that MUST carry RSI. Used by audit_missing_rsi
# to enforce "RSI on every recommendation". Kept tight to avoid false positives
# on prose / context lines.
_REC_LINE_PATTERNS = [
    re.compile(r"\bSELL TO OPEN\b", re.I),
    re.compile(r"\bPULLBACK CSP\b"),
    re.compile(r"\bCSP — PAID-TO-WAIT\b"),         # renamed PULLBACK CSP label
    re.compile(r"\bNEW CSP\b"),
    re.compile(r"\bCSP ENTRY\b", re.I),
    re.compile(r"\bREADY TO WRITE\b", re.I),
    re.compile(r"BUY ~\$"),                        # equity add
    re.compile(r"\bSELL \d+×"),                    # covered call / strangle write
    re.compile(r"^\s*\d+\.\s+\*\*(CLOSE|EXECUTE ROLL|DEFENSIVE|HEDGE|TRIM|REVIEW CORE|"
               r"PULLBACK CSP|CSP — PAID-TO-WAIT|NEW CSP)\*\*"),
]


def audit_missing_rsi(md: str, context_lines: int = 3) -> list[str]:
    """Scan rendered briefing markdown and return recommendation lines that do
    NOT carry an RSI annotation within `context_lines` (in either direction).
    This is the "every recommendation includes RSI" hook.

    A recommendation block can carry its RSI on a header line, the recommendation
    line itself, or a sub-bullet — so we look both backward and forward.

    Excluded from the check (not actionable recommendations):
      - Italic transparency notes (lines starting with '_') — skip/footer text.
      - The Capital Plan section — a rollup of recs detailed (with RSI) above.
    """
    lines = md.splitlines()
    offenders: list[str] = []
    in_excluded_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            # Section boundary — exclude the Capital Plan rollup.
            # Rollup sections are exempt: their items are detailed (with
            # RSI) elsewhere in the briefing. Capital Plan + Money Plan.
            in_excluded_section = ("Capital Plan" in stripped
                                   or "Money Plan" in stripped)
        if in_excluded_section:
            continue
        if stripped.startswith("_"):       # italic transparency / footer note
            continue
        if not any(p.search(line) for p in _REC_LINE_PATTERNS):
            continue
        lo = max(0, i - context_lines)
        hi = i + context_lines + 1
        window = " ".join(lines[lo:hi])
        if "RSI" not in window:
            offenders.append(stripped)
    return offenders


_HEADER_NUM_RE = re.compile(r"^\s*\d+\.\s")


def annotate_action_lines(
    items: list[str],
    technicals: dict | None,
    thresholds: dict | None = None,
) -> list[str]:
    """Append a compact RSI tag to each numbered action-list header line.

    A header line is one that begins with ``"N. "``. We find the first known
    ticker on the line, look up its RSI, infer the trade side, and append a
    side-aware tag. Lines that already mention RSI are left untouched (so a
    richer upstream annotation is never double-tagged). Returns a NEW list;
    the input is not mutated (callers that feed ``items`` to the summary card
    keep the un-annotated original).
    """
    if not technicals:
        return list(items)
    tickers = sorted(technicals.keys(), key=len, reverse=True)
    out: list[str] = []
    for line in items:
        if _HEADER_NUM_RE.match(line) and "RSI" not in line:
            tk = first_known_ticker(line, tickers)
            if tk:
                rsi = rsi_for(tk, technicals)
                if rsi is not None:
                    out.append(f"{line}  · {tag(rsi, infer_side(line), thresholds)}")
                    continue
        out.append(line)
    return out
