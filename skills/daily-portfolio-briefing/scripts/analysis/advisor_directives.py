"""Contract-level advisor directives (bug #25, 2026-07-22).

Parses per-CONTRACT hold directives from ``state/fable_advisor_memory.md``
(the user-editable head above "## Recent reviews") and answers one question:
"does a standing directive suppress the CLOSE recommendation on this contract
right now?"

Motivating symptom: action item "CLOSE MSFT_PUT_380_20270319 — +32%
⏳ IGNORED 5 DAYS · ⛔ DECISION REQUIRED" rendered in the same briefing where
Fable's review said "your directive holds". The Fable layer respected the
directive; the deterministic CLOSE recommender and the rec-aging stalled
promotion did not. Following a documented directive is NOT "ignoring" a
recommendation — the aging clock must not tick on it.

Relationship to ``entry_exit_recommender.directive_hold_tickers`` (checked
per bug #25 step 5): that helper is TICKER-level (one keyword scan per bullet
header line; used to suppress TRIM/EXIT and the playbook's close side). This
module is CONTRACT-level and carries the machine-readable RELEASE CONDITIONS
("until capture > 55% AND DTE < 90d", "capture < 20%", "spot breaks $360")
so a directive stops suppressing exactly when the user said it should. The
two are complementary, not duplicates — do not consolidate them into one
keyword scan (ticker-level has no release semantics; contract-level must not
accidentally hold a whole ticker).

Fail-open everywhere: unparseable text → no directives → nothing suppressed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# Contract idents like MSFT_PUT_380_20270319 (expiry part optional).
_CONTRACT_HEADER_RE = re.compile(
    r"^\s*[-*]\s*\*\*([A-Z.]{1,6}_(?:PUT|CALL)_\d+(?:\.\d+)?(?:_\d{6,8})?)\b"
)
# Any top-level bullet (used to find where a directive block ends).
_BULLET_RE = re.compile(r"^\s*[-*]\s*\*\*")

# Release-condition patterns. Tolerant of whitespace/newlines between tokens,
# "~" approximations, "+" signs and "%" suffixes.
_CAPTURE_AND_DTE_RE = re.compile(
    r"capture\s+rises\s+past\s+~?\s*\+?(\d+(?:\.\d+)?)\s*%?\s+AND\s+DTE\s*<\s*(\d+)\s*d",
    re.IGNORECASE | re.DOTALL,
)
_CAPTURE_BELOW_RE = re.compile(
    r"capture\s+drops\s+below\s+~?\s*\+?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE | re.DOTALL,
)
_SPOT_BELOW_RE = re.compile(
    r"spot\s+(?:breaks|drops?\s+(?:below|through)|falls?\s+(?:below|through))\s+\$?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)

# A directive block counts as a HOLD when its text signals one — same keyword
# family entry_exit_recommender.directive_hold_tickers uses, plus the
# "stop escalating" phrasing the winner-close overrides use.
_HOLD_KEYWORDS = ("hold", "not ready to sell", "don't", "do not", "stop",
                  "same treatment")


@dataclass
class HoldDirective:
    contract: str           # "MSFT_PUT_380_20270319"
    ticker: str             # "MSFT"
    type: str               # "hold"  (future: "override", "exit_now")
    # release conditions (any-of):
    release_capture_and_dte: tuple[float, int] | None  # (0.55, 90) = capture > 55% AND dte < 90
    release_capture_below: float | None                # 0.20 = capture < 20%
    release_spot_below: float | None                   # 360.0 = spot breaks $360
    raw_text: str                                       # for auditability


def parse_directives(memory_md_text: str | None) -> list[HoldDirective]:
    """Parse contract-level hold directives from the memory markdown.

    Only the user-editable head (above "## Recent reviews") is scanned so
    contracts merely mentioned in past auto-maintained Fable reviews never
    become accidental CLOSE suppressors. Fail-open: any parse error → [].
    """
    if not memory_md_text:
        return []
    try:
        head = str(memory_md_text).split("## Recent reviews", 1)[0]
        lines = head.splitlines()
        out: list[HoldDirective] = []
        i = 0
        while i < len(lines):
            m = _CONTRACT_HEADER_RE.match(lines[i])
            if not m:
                i += 1
                continue
            # Block = header bullet + continuation lines until the next
            # top-level bullet or section header.
            block_lines = [lines[i]]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if _BULLET_RE.match(nxt) or nxt.lstrip().startswith("#"):
                    break
                block_lines.append(nxt)
                j += 1
            i = j
            block = "\n".join(block_lines)
            low = block.lower()
            if not any(k in low for k in _HOLD_KEYWORDS):
                continue                    # not a hold directive — skip
            contract = m.group(1).upper()
            cap_dte = _CAPTURE_AND_DTE_RE.search(block)
            cap_below = _CAPTURE_BELOW_RE.search(block)
            spot_below = _SPOT_BELOW_RE.search(block)
            out.append(HoldDirective(
                contract=contract,
                ticker=contract.split("_", 1)[0],
                type="hold",
                release_capture_and_dte=(
                    (float(cap_dte.group(1)) / 100.0, int(cap_dte.group(2)))
                    if cap_dte else None),
                release_capture_below=(
                    float(cap_below.group(1)) / 100.0 if cap_below else None),
                release_spot_below=(
                    float(spot_below.group(1)) if spot_below else None),
                raw_text=block.strip()[:800],
            ))
        return out
    except Exception as e:  # noqa: BLE001 — hard fail-open per bug #25 spec
        print(f"[advisor-directives] parse failed (non-fatal): {e}",
              file=sys.stderr)
        return []


def _norm_capture(capture_pct: float | None) -> float | None:
    """Normalize capture to a 0..1 fraction (lenient: 32 → 0.32)."""
    if capture_pct is None:
        return None
    try:
        c = float(capture_pct)
    except (TypeError, ValueError):
        return None
    return c / 100.0 if abs(c) > 1.5 else c


def should_suppress_close(
    directive: HoldDirective,
    capture_pct: float,
    dte: int,
    spot: float | None,
) -> bool:
    """True while the directive HOLDS (CLOSE suppressed); False once ANY
    release condition fires. Missing inputs never fire a release — the
    directive is the user's explicit word, so uncertainty defers to it.
    """
    try:
        cap = _norm_capture(capture_pct)
        rel = directive.release_capture_and_dte
        if (rel is not None and cap is not None and dte is not None
                and cap > rel[0] and int(dte) < rel[1]):
            return False
        if (directive.release_capture_below is not None and cap is not None
                and cap < directive.release_capture_below):
            return False
        if (directive.release_spot_below is not None and spot is not None
                and float(spot) > 0
                and float(spot) < directive.release_spot_below):
            return False
        return True
    except Exception:  # noqa: BLE001 — fail toward respecting the directive
        return True


# Task #43 defect 3 (2026-07-31, AMD_PUT_420_20261218): a hold directive's
# release conditions were written before an earnings print entered the
# window — "capture 40%, DTE 140d — release conditions not met" suppressed
# the CLOSE while the same briefing's Watch panel said "Earnings in 4d with
# 40% captured. Consider closing before report." Universal implicit release:
# the suppression is PAUSED (not deleted) when earnings are within
# `directives.earnings_release_days` (default 7) AND capture ≥ 25%
# (profitable positions facing a binary deserve the surfaced decision;
# deep-underwater positions near prints are owned by loss-stop/urgent
# paths). A directive containing the phrase "through earnings" is exempt —
# explicit user intent wins.
_EARNINGS_PAUSE_MIN_CAPTURE = 0.25
_THROUGH_EARNINGS_PHRASE = "through earnings"
_DEFAULT_EARNINGS_RELEASE_DAYS = 7


def earnings_pause(
    directive: HoldDirective,
    capture_pct: float,
    days_to_earnings: int | None,
    config: dict | None = None,
) -> bool:
    """True when the directive's CLOSE suppression is PAUSED for an
    imminent earnings print (the CLOSE surfaces with a "directive paused"
    note; the directive itself is untouched and resumes after the print).

    Fires only when ALL of:
    - the directive text does NOT contain "through earnings" (explicit
      user intent to hold across prints is exempt);
    - earnings are measured within ``directives.earnings_release_days``
      (default 7) days from today (missing earnings date → no pause);
    - capture ≥ 25% (underwater positions near prints are handled by the
      loss-stop / urgent paths, not this pause).

    Fail-open toward the directive: any error / missing input → False.
    """
    try:
        if directive is None:
            return False
        if _THROUGH_EARNINGS_PHRASE in (directive.raw_text or "").lower():
            return False
        if days_to_earnings is None:
            return False
        try:
            release_days = int(((config or {}).get("directives") or {})
                               .get("earnings_release_days",
                                    _DEFAULT_EARNINGS_RELEASE_DAYS))
        except (TypeError, ValueError, AttributeError):
            release_days = _DEFAULT_EARNINGS_RELEASE_DAYS
        cap = _norm_capture(capture_pct)
        if cap is None or cap < _EARNINGS_PAUSE_MIN_CAPTURE:
            return False
        return 0 <= int(days_to_earnings) <= release_days
    except Exception:  # noqa: BLE001 — fail toward respecting the directive
        return False


def directive_holds_contract(
    contract: str,
    directives: list[HoldDirective],
    capture_pct: float,
    dte: int,
    spot: float | None,
) -> HoldDirective | None:
    """The directive currently holding ``contract`` (release conditions not
    met), or None when no directive applies / a release condition fired."""
    if not contract or not directives:
        return None
    ident = str(contract).strip().upper()
    for d in directives:
        if d.contract == ident and d.type == "hold":
            if should_suppress_close(d, capture_pct, dte, spot):
                return d
            return None                    # matched but released — CLOSE allowed
    return None
