"""
Briefing data-source verifier.

Scans the rendered briefing markdown for live-data attribution on every action.
Flags or suppresses stub-derived recommendations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    verified: bool
    live_actions: int = 0
    stubbed_actions: int = 0
    flagged_lines: list = field(default_factory=list)
    panel_md: str = ""


# Patterns that prove an action ticked through live chain data.
_LIVE_INDICATORS = [
    r"current bid \$\d+\.?\d* / mid \$\d+\.?\d* / ask \$\d+\.?\d*",  # roll bid/ask tuple
    r"\*\*Source:\*\*\s+Live E\*TRADE chain",
    r"price_source.*live_chain",
    r"Buy-to-Close.*current mid",  # roll close-leg with real mid
]

# Patterns that mark an action as stub-derived.
_STUB_INDICATORS = [
    r"would re-acquire 100 shares @ -\d+% below spot",  # pullback CSP formula text
    r"premium.*~\$\d+\.\d+",                            # "~$X.XX" formula prefix
]


def _is_action_line(line: str) -> bool:
    """Top-level numbered action: '1. **EXECUTE ROLL** ...' etc."""
    return bool(re.match(r"^\s*\d+\.\s+\*\*", line))


def _collect_action_blocks(md: str) -> list[tuple[str, list[str]]]:
    """
    Split the action-list section into blocks: each block = the numbered headline
    + all sub-bullet lines until the next numbered action.
    """
    if "## Today's Action List" not in md:
        return []
    section = md.split("## Today's Action List", 1)[1].split("\n## ", 1)[0]
    blocks = []
    current_head = None
    current_bullets: list[str] = []
    for line in section.split("\n"):
        if _is_action_line(line):
            if current_head:
                blocks.append((current_head, current_bullets))
            current_head = line
            current_bullets = []
        elif current_head and line.strip():
            current_bullets.append(line)
    if current_head:
        blocks.append((current_head, current_bullets))
    return blocks


def _block_text(head: str, bullets: list[str]) -> str:
    return head + "\n" + "\n".join(bullets)


def _is_live_backed(block_text: str) -> bool:
    return any(re.search(p, block_text, re.IGNORECASE) for p in _LIVE_INDICATORS)


def _has_stub_marker(block_text: str) -> bool:
    return any(re.search(p, block_text, re.IGNORECASE) for p in _STUB_INDICATORS)


def verify_live_data(md: str, strict_mode: bool = False) -> VerificationResult:
    """
    Scan a rendered briefing for live-data attribution on every action.

    Returns a VerificationResult with counts and a markdown panel summarizing
    what's verified vs. flagged. In strict_mode, the caller should suppress
    flagged lines from the briefing.
    """
    result = VerificationResult(verified=True)
    blocks = _collect_action_blocks(md)

    # Action types that DON'T require chain attribution:
    # - CLOSE: based on existing position (entry already known from broker)
    # - TRIM: equity action, no options chain involved
    # - HEDGE: aggregated SPY recommendation (sized from delta math, not chain)
    # - URGENT: existing-position warning, not a new order ticket
    # - REVIEW ROLL: advisory ("look at chain at broker"), not an executable order
    # - PULLBACK CSP, EXECUTE ROLL, DEFENSIVE COLLAR: NEW orders → MUST have chain
    EXEMPT_PATTERNS = re.compile(
        r"\*\*(CLOSE|TRIM|HEDGE|URGENT|REVIEW ROLL|REVIEW|HOLD|CONSIDER|EQUITY)\*\*",
        re.IGNORECASE,
    )

    for head, bullets in blocks:
        text = _block_text(head, bullets)
        if EXEMPT_PATTERNS.search(head):
            result.live_actions += 1
            continue

        # Roll, CSP, COLLAR — these need live-chain attribution
        is_live = _is_live_backed(text)
        has_stub = _has_stub_marker(text)
        if is_live and not has_stub:
            result.live_actions += 1
        else:
            result.stubbed_actions += 1
            # Extract action name (e.g., "PULLBACK CSP AMZN")
            m = re.search(r"\*\*([^*]+)\*\*\s+([A-Z]+)", head)
            label = f"{m.group(1).strip()} {m.group(2)}" if m else head[:80]
            result.flagged_lines.append(label)

    if result.stubbed_actions > 0:
        result.verified = False
        lines = [
            "",
            "## 🔴 Live-Data Verification",
            "",
            f"**{result.stubbed_actions} action(s) lack live E*TRADE chain attribution:**",
            "",
        ]
        for label in result.flagged_lines:
            lines.append(f"- {label}")
        lines.append("")
        if strict_mode:
            lines.append(
                "**Strict mode:** these actions have been **SUPPRESSED** from the briefing."
            )
        else:
            lines.append(
                "**Advisory:** treat these prices as estimates. Verify at the broker before placing."
            )
        lines.append("")
        result.panel_md = "\n".join(lines)

    return result
