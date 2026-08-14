"""Setup Grade badges + filter chips — WHEN to enter, at a glance.

George (2026-08-10): "We need a very clear message as to when I should get
in on every transaction."

Mirrors the rsi_zones pattern exactly: the webapp NEVER redefines grading —
letter floors and the enabled flag are imported from the PIPELINE's
``analysis/setup_grade.py`` merged with briefing.yaml's ``setup_grade``
block via the existing config bridge. Templates and JS only consume what
this module emits (tokens on ``data-setup-grades``, humanized badge dicts,
chip definitions) — never re-derive letters downstream. The drift test
(tests/test_setup_grade_filter.py) pins our values against the pipeline's.

Grades ride on the briefing JSON: new_ideas / strategy_upgrades dicts carry
``setup_grade`` fields directly; long-term opportunities carry the composed
note inside ``trigger_reasons`` (advisor-dataclass compatibility on the
pipeline side), which we parse back out here. Ungraded → token ``none``
(visible under "All" only) and no badge — never a fabricated read
(hard rule #19).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .config import _ensure_pipeline_on_path, briefing_config

_ensure_pipeline_on_path()

from analysis import setup_grade as _sg  # type: ignore  # noqa: E402


# Letters that count as "A/B" for the good-setup filter chips.
GOOD_LETTERS = ("A", "A-", "B")

# Grade letters as they appear in pipeline output / trigger-reason notes.
_GRADE_NOTE_RE = re.compile(r"Setup Grade:\s*(A-|A|B|C|D|—|n/a)")

# Idea kinds that are put-side (CSP) transactions.
_CSP_KINDS = ("NEW_CSP", "LONG_DATED_CSP", "PULLBACK_CSP", "LT_CSP",
              "SCOUT_CSP")
_CC_TYPES = ("write_covered_call", "index_covered_call")


@lru_cache(maxsize=1)
def letters() -> dict[str, float]:
    """Letter floors from the pipeline's setup_grade merged with
    briefing.yaml — the single source of truth (drift-guarded)."""
    cfg = _sg.load_setup_grade_config(briefing_config())
    return {k: float(v) for k, v in (cfg.get("letters") or {}).items()}


def _letter_slug(letter: str) -> str:
    return {
        "A": "a", "A-": "a_minus", "B": "b", "C": "c", "D": "d",
        "—": "blocked", "n/a": "na",
    }.get(letter, "other")


def _grade_of(base: Any) -> tuple[str | None, str | None, dict]:
    """(side, letter, raw) from a merged-idea or strategy-upgrade dict.

    Side: 'cc' for covered-call upgrade types, 'csp' for put-side idea
    kinds (or any graded dict that isn't a CC — every other graded surface
    is put-side today). Letter from the dict's own ``setup_grade`` field,
    else parsed from the LTO trigger-reason note. (None, None, {}) when
    ungraded — fail closed, no badge."""
    if not isinstance(base, dict):
        # Strategy upgrades reach the briefing route as Pydantic models
        # (models/briefing.py StrategyUpgrade, extra="allow") — the grade
        # rides in the extras. Without this dump, every graded CC-write
        # rendered ungraded on the real page (the exact 2026-08-13
        # NFLX/SOFI/SOXL/SOXX/VRT "graded D but shouting CSP zone" case).
        dump = getattr(base, "model_dump", None)
        if not callable(dump):
            return None, None, {}
        try:
            base = dump()
        except Exception:
            return None, None, {}
        if not isinstance(base, dict):
            return None, None, {}
    raw = base.get("raw") if isinstance(base.get("raw"), dict) else base
    upgrade_type = str(base.get("type") or raw.get("type") or "").lower()
    kind = str(base.get("kind") or raw.get("kind") or "").upper()

    letter = raw.get("setup_grade") or base.get("setup_grade")
    if not letter:
        for tr in raw.get("trigger_reasons") or []:
            m = _GRADE_NOTE_RE.search(str(tr))
            if m:
                letter = m.group(1)
                break
    if not letter:
        return None, None, raw

    if upgrade_type in _CC_TYPES:
        side = "cc"
    elif kind in _CSP_KINDS or kind.startswith("SKIPPED"):
        side = "csp"
    else:
        side = "csp"  # every other graded surface is put-side today
    return side, str(letter), raw


def grade_of(base: Any) -> tuple[str | None, str | None, dict]:
    """Public accessor for the card-hierarchy redesign (George 2026-08-13):
    (side, letter, raw) — side 'csp'/'cc', letter 'A'..'D'/'—'/'n/a', raw
    the dict the grade fields were read from. (None, None, {}) when
    ungraded (fail closed, rule #19)."""
    return _grade_of(base)


def grade_tokens(base: Any) -> str:
    """Space-separated tokens for the data-setup-grades attribute:
    '{side}_{letter-slug}' plus '{side}_good' when the letter is A/A-/B.
    Ungraded → 'none'."""
    side, letter, _raw = _grade_of(base)
    if not side or not letter:
        return "none"
    tokens = [f"{side}_{_letter_slug(letter)}"]
    if letter in GOOD_LETTERS:
        tokens.append(f"{side}_good")
    return " ".join(tokens)


def grade_badge(base: Any) -> dict[str, str] | None:
    """Humanized at-a-glance badge (rule #31 — never leak raw tokens):
    {label, tone, rep_tone, title}. Tooltip carries the measured drivers +
    the entry message. None when the card is ungraded."""
    side, letter, raw = _grade_of(base)
    if not side or not letter:
        return None
    side_label = "CSP" if side == "csp" else "CC"
    if letter in ("A", "A-"):
        tone, rep_tone = "green", "green"
    elif letter == "B":
        tone, rep_tone = "green", "green"
    elif letter == "C":
        tone, rep_tone = "neutral", "muted"
    elif letter == "D":
        tone, rep_tone = "orange", "amber"
    else:  # '—' hard block / n/a
        tone, rep_tone = "red", "red"
    drivers = [str(d) for d in (raw.get("setup_grade_drivers") or [])]
    message = str(raw.get("setup_grade_message") or "")
    title = " · ".join(drivers) if drivers else ""
    if message:
        title = f"{title} — {message}" if title else message
    if not title:
        title = ("Entry-timing setup grade (WHEN, not WHAT) — see the "
                 "briefing's Best Setups section.")
    label = f"🏁 {side_label} setup {letter}"
    # 💎 PRIME (George 2026-08-14: "we should clearly see all of the good
    # entries based on this algorithm") — rides the pipeline's
    # ``setup_grade_prime`` field on the same dicts; never derived here.
    if raw.get("setup_grade_prime"):
        label += " · 💎 PRIME"
    return {
        "label": label,
        "tone": tone,
        "rep_tone": rep_tone,
        "title": title,
    }


def grade_filter_chips() -> list[dict[str, str]]:
    """Chip definitions for the setup-grade filter bar (All first). The
    titles carry the config-derived B floor so the UI never hardcodes
    letter thresholds."""
    b_floor = letters().get("b", 65.0)
    return [
        {"grade": "all", "label": "All",
         "title": "Show every card (including ungraded)"},
        {"grade": "csp_good", "label": "🎯 CSP setup A/B",
         "title": (f"Put-side entry timing graded A/A-/B "
                   f"(score ≥ {b_floor:.0f}/100) — favored CSP setups")},
        {"grade": "cc_good", "label": "📞 CC setup A/B",
         "title": (f"Covered-call entry timing graded A/A-/B "
                   f"(score ≥ {b_floor:.0f}/100) — favored CC writes")},
    ]


def register_jinja_globals(env) -> None:
    env.globals["setup_grade_filter_chips"] = grade_filter_chips
    env.globals["setup_grade_tokens"] = grade_tokens
    env.globals["setup_grade_badge"] = grade_badge
