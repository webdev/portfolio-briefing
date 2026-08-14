"""Same-theme put-stacking guard — the SNDK/WDC gap (CLAUDE.md rule #49).

George (2026-08-14), after the briefing rendered "A- (80) WDC — SELL 1×
$450P … 🏁 Entry: A- — strong setup… Enter per plan" while he HELD the
SNDK $1230P (WDC = SanDisk's former parent, same Memory & Storage theme):
"make that change so that the briefing is not telling me to do it today.
It's misleading."

A new CSP whose ticker shares ANY scout theme with an underlying where a
short put is already held is the SAME correlated assignment risk wearing a
different ticker — a Memory & Storage flush assigns BOTH puts at once.
The entry algorithm (rule #48 STEP 2, right after the per-name rule-#40
overlap check) turns such an entry into a WAIT (config
``entry_algorithm.theme_stacking: {enabled: true, mode: wait}``) with the
measured reason naming the held contract and the shared theme.

Single source of truth for the ticker → theme mapping: the thematic
scout's ``theme_universes.yaml`` anchors (a ticker may sit in multiple
themes). Fail directions (rule #19):

  - a ticker in NO mapped theme → no check (fail-open);
  - the YAML unreadable / PyYAML missing → no check (fail-open);
  - SAME-ticker held puts are excluded here — same-name risk is governed
    by the rule-#40 5% overlap check and the stacking annotation, not by
    the theme guard;
  - CC writes are exempt (share-backed) — the exemption lives in the
    ``evaluate_entry`` wiring, not here (this module is pure).
"""

from __future__ import annotations

from pathlib import Path

# scripts/analysis/ → scripts/ → daily-portfolio-briefing/ → skills/
DEFAULT_THEME_FILE = (Path(__file__).resolve().parents[3]
                      / "thematic-scout" / "references"
                      / "theme_universes.yaml")

# Per-path parse cache (the YAML is static within a run).
_CACHE: dict[str, dict | None] = {}


def load_theme_map(path=None) -> dict[str, set] | None:
    """{TICKER: {theme display names}} from theme_universes.yaml anchors.

    A ticker may appear in multiple themes. Fail-open: unreadable file /
    missing PyYAML / empty themes → None (callers skip the check)."""
    p = Path(path) if path else DEFAULT_THEME_FILE
    key = str(p)
    if key in _CACHE:
        return _CACHE[key]
    result: dict[str, set] | None = None
    try:
        import yaml
        with open(p, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        out: dict[str, set] = {}
        for tkey, meta in (raw.get("themes") or {}).items():
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or tkey)
            for a in (meta.get("anchors") or []):
                tk = str(a).upper().strip()
                if tk:
                    out.setdefault(tk, set()).add(name)
        result = out or None
    except Exception:
        result = None       # fail-open (rule #19) — no fabricated mapping
    _CACHE[key] = result
    return result


def themes_for(ticker, theme_map: dict | None = None) -> set:
    """The set of theme display names ``ticker`` belongs to (empty when
    unmapped — the fail-open direction)."""
    tm = theme_map if theme_map is not None else load_theme_map()
    if not tm:
        return set()
    return set(tm.get(str(ticker or "").upper()) or set())


def check_theme_stacking(ticker, held_puts_by_ticker,
                         theme_map: dict | None = None) -> dict | None:
    """Cross-name same-theme check for one proposed NEW CSP.

    Args:
        ticker: the new CSP's underlying.
        held_puts_by_ticker: {TICKER: [strikes]} of currently HELD short
            puts (``setup_grade._held_short_put_strikes`` shape).
        theme_map: override mapping for tests; default loads the scout's
            theme_universes.yaml.

    Returns None when there is no overlap (or the ticker is unmapped /
    the map is unavailable — fail-open, rule #19). Otherwise::

        {"matches": [{"ticker", "strikes", "themes"}, ...],
         "detail": "⛔ same-theme put already held — SNDK $1230P "
                   "(Memory & Storage); stacking correlated assignment "
                   "risk"}

    Same-ticker held puts never match here (rule #40 territory)."""
    tk = str(ticker or "").upper()
    tm = theme_map if theme_map is not None else load_theme_map()
    if not tk or not tm:
        return None
    mine = set(tm.get(tk) or set())
    if not mine:
        return None         # not in any mapped theme → no check
    matches: list[dict] = []
    for held_tk in sorted(held_puts_by_ticker or {}):
        h = str(held_tk or "").upper()
        if not h or h == tk:
            continue        # same-name risk is rule #40's, not the theme's
        shared = mine & set(tm.get(h) or set())
        if not shared:
            continue
        strikes = []
        for s in (held_puts_by_ticker or {}).get(held_tk) or []:
            try:
                strikes.append(float(s))
            except (TypeError, ValueError):
                continue
        matches.append({"ticker": h, "strikes": sorted(strikes),
                        "themes": sorted(shared)})
    if not matches:
        return None
    frags = []
    for m in matches:
        held_s = "/".join(f"${x:g}P" for x in m["strikes"]) or "short put"
        frags.append(f"{m['ticker']} {held_s} ({', '.join(m['themes'])})")
    detail = (f"⛔ same-theme put already held — {' · '.join(frags)}; "
              f"stacking correlated assignment risk")
    return {"matches": matches, "detail": detail}
