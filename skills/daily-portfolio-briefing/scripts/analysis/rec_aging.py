"""
Recommendation aging + fill reconciliation (Step 7.5 of the skill spec).

The May–June 2026 runs exposed the system's central weakness: recommendations
repeated daily without execution (SPY hedge 11+ consecutive briefings,
GOOG/NVDA trim ~30 days) while the briefing inferred "resolved" from items
merely dropping off the list. Two mechanisms fix this:

A. **Fill reconciliation** — compare yesterday's action list against actual
   broker state (position diffs between daily snapshots; etrade-mcp has no
   list_transactions, so position diffs + open_orders.json are the evidence).
   Each prior action gets a definitive status:
     EXECUTED   — position gone/reduced consistent with the action (for ROLL:
                  old contract gone AND a same-underlying later-dated short
                  appeared). NOTE: "position gone for another reason" (OBE) is
                  indistinguishable from execution on position diffs alone, so
                  OBE folds into EXECUTED with a note.
     PARTIAL    — position reduced but not fully consistent with the action
     IGNORED    — position unchanged (no fill detected)
     UNVERIFIED — snapshot data missing / ambiguous. NEVER guess EXECUTED.

B. **Recommendation aging** — every action item carries
   {first_flagged, days_flagged}. Escalation ladder:
     day 1–2  normal rendering
     day 3+   "⏳ IGNORED N DAYS" tag, treated as Tier-1/urgent
     day 5+   binary prompt: execute today or file a directive
     day 6+   item surfaces in a "## ⛔ Stalled Items" panel at the VERY TOP
              of the briefing, above the header.

State lives in ``state/rec_aging.yaml`` keyed by a stable action key
``{KIND}:{ticker_or_contract}`` (e.g. ``CLOSE:MU_PUT_700_20261218``,
``TRIM:NVDA``). Ignoring a recommendation must be an explicit, recorded
decision — never a free, invisible default.

This module is deterministic; all I/O is confined to the small load/save
helpers and ``build_aging_context`` (which run_briefing.py calls).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from analysis.briefing_diff import _signature_for_action

# Escalation thresholds (days of consecutive IGNORED flagging)
TIER1_DAYS = 3      # ⏳ tag + urgent priority
PROMPT_DAYS = 5     # binary "execute or file a directive" prompt
STALLED_DAYS = 6    # moves to the ⛔ Stalled Items panel at the top

# Headline cap for the action list — everything past the top 5 goes to the
# "Appendix: Full Action Queue" subsection (June 2026: 12+ items/day against
# ~1-2 trades/day of operator bandwidth causes paralysis).
HEADLINE_CAP = 5

_CONTRACT_RE = re.compile(r"^([A-Z.]{1,6})_(PUT|CALL)_(\d+(?:\.\d+)?)(?:_(\d{6,8}))?$")

# Kind groupings for reconciliation semantics
_ROLL_KINDS_HINT = "ROLL"  # any kind containing ROLL → roll semantics
_CLOSE_KINDS = {"CLOSE", "CLOSE_FOR_PROFIT", "URGENT", "LET_EXPIRE",
                "TAKE_ASSIGNMENT", "CLOSE_WINNER"}
_EXIT_KINDS = {"EXIT", "SELL"}
_TRIM_KINDS = {"TRIM", "REVIEW_CORE"}
_NEW_SHORT_PUT_KINDS = {"NEW_CSP", "PULLBACK_CSP", "NEW_WEEKLY"}
_HEDGE_KINDS = {"HEDGE"}


# ---------------------------------------------------------------------------
# Keys + action extraction
# ---------------------------------------------------------------------------

def normalize_kind(kind: str) -> str:
    """Normalize an action kind: uppercase, strip parentheticals, _ for spaces.

    "DEFENSIVE ROLL (core override)" → "DEFENSIVE_ROLL"
    """
    k = re.sub(r"\(.*?\)", "", str(kind or "")).strip().upper()
    k = re.sub(r"[^A-Z0-9]+", "_", k).strip("_")
    return k


def normalize_ident(ident: str) -> str:
    """Normalize a ticker or contract symbol (uppercase, trimmed)."""
    return str(ident or "").strip().upper()


def action_key(kind: str, ident: str) -> str:
    """Stable action key: ``{KIND}:{ticker_or_contract}``."""
    return f"{normalize_kind(kind)}:{normalize_ident(ident)}"


def key_from_signature(sig: str) -> str:
    """Convert a briefing_diff signature ``KIND|IDENT`` to an action key."""
    kind, _, ident = str(sig or "").partition("|")
    return action_key(kind, ident)


def action_from_line(line: str) -> Optional[dict]:
    """Parse a numbered action line into {key, kind, ident, summary} or None."""
    sig = _signature_for_action(line)
    if "|" not in sig:
        return None
    kind, _, ident = sig.partition("|")
    summary = re.sub(r"^\s*\d+\.\s*", "", line.split("\n")[0]).strip()
    return {
        "key": action_key(kind, ident),
        "kind": normalize_kind(kind),
        "ident": normalize_ident(ident),
        "summary": summary,
    }


def extract_actions_from_markdown(briefing_md: str) -> list[dict]:
    """Extract structured actions from a briefing markdown's Action List section.

    Includes the "Appendix: Full Action Queue" subsection (it lives inside the
    same ``## Today's Action List`` section).
    """
    actions: list[dict] = []
    if not briefing_md or "## Today's Action List" not in briefing_md:
        return actions
    section = briefing_md.split("## Today's Action List", 1)[1].split("\n## ", 1)[0]
    seen: set[str] = set()
    for line in section.split("\n"):
        if not re.match(r"^\s*\d+\.\s+\*\*", line):
            continue
        a = action_from_line(line)
        if a and a["key"] not in seen:
            seen.add(a["key"])
            actions.append(a)
    return actions


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state(state_path: Path) -> dict:
    """Load the aging state map (key → {first_flagged, days_flagged, last_status})."""
    state_path = Path(state_path)
    if not state_path.exists():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(state_path.read_text()) or {}
    except Exception:
        return {}
    actions = raw.get("actions") if isinstance(raw, dict) else None
    return actions if isinstance(actions, dict) else {}


def save_state(state_path: Path, state: dict, updated: str | None = None) -> None:
    """Persist the aging state map to YAML (creates parent dirs)."""
    import yaml
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": updated or datetime.now().strftime("%Y-%m-%d"),
        "actions": state or {},
    }
    state_path.write_text(yaml.safe_dump(payload, sort_keys=True, allow_unicode=True))


# ---------------------------------------------------------------------------
# Fill reconciliation
# ---------------------------------------------------------------------------

def _positions_by_symbol(positions) -> dict:
    out: dict = {}
    for p in (positions or []):
        sym = normalize_ident(p.get("symbol") or "")
        if sym:
            out[sym] = p
    return out


def _parse_contract(ident: str) -> Optional[dict]:
    m = _CONTRACT_RE.match(ident or "")
    if not m:
        return None
    return {
        "underlying": m.group(1),
        "type": m.group(2),
        "strike": float(m.group(3)),
        "exp": m.group(4) or "",
    }


def _find_contract_position(ident: str, by_symbol: dict) -> Optional[dict]:
    """Find a position matching a contract ident; tolerate a missing expiry part."""
    if ident in by_symbol:
        return by_symbol[ident]
    # Ident without expiry (e.g. GOOG_CALL_450) — prefix match
    if _CONTRACT_RE.match(ident) is None:
        # try prefix anyway (GOOG_CALL_450 fails full regex only if malformed)
        pass
    for sym, p in by_symbol.items():
        if sym == ident or sym.startswith(ident + "_"):
            return p
    return None


def _exp_key(exp: str) -> str:
    """Sortable expiration key from YYYYMMDD or YYYY-MM-DD strings."""
    return re.sub(r"\D", "", str(exp or ""))


def _roll_replacement_present(contract: dict, prev_by_sym: dict, today_by_sym: dict) -> bool:
    """A new same-underlying, same-type SHORT position with a later expiration
    appeared today (the rolled-to leg). Pre-existing later-dated shorts do NOT
    count — the replacement must be NEW since yesterday."""
    old_exp = _exp_key(contract.get("exp"))
    for sym, p in today_by_sym.items():
        if sym in prev_by_sym:
            continue  # not new
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if normalize_ident(p.get("underlying") or "") != contract["underlying"]:
            continue
        if (p.get("type") or "").upper() != contract["type"]:
            continue
        if float(p.get("qty", 0) or 0) >= 0:
            continue  # must be short
        new_exp = _exp_key(p.get("expiration") or sym.rsplit("_", 1)[-1])
        if old_exp and new_exp and new_exp <= old_exp:
            continue
        return True
    return False


def _new_option_on_underlying(ticker: str, prev_by_sym: dict, today_by_sym: dict,
                              opt_type: str, short: bool) -> bool:
    """Did a NEW option position of the given type/direction appear on ticker?
    Also true when an existing one's |qty| increased."""
    for sym, p in today_by_sym.items():
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if normalize_ident(p.get("underlying") or "") != ticker:
            continue
        if (p.get("type") or "").upper() != opt_type:
            continue
        qty = float(p.get("qty", 0) or 0)
        if short and qty >= 0:
            continue
        if not short and qty <= 0:
            continue
        prev = prev_by_sym.get(sym)
        if prev is None:
            return True
        if abs(qty) > abs(float(prev.get("qty", 0) or 0)):
            return True
    return False


def _qty(p: Optional[dict]) -> float:
    return float((p or {}).get("qty", 0) or 0)


def reconcile(prev_actions, prev_positions, today_positions, open_orders=None) -> dict:
    """Reconcile yesterday's action list against actual position diffs.

    Args:
        prev_actions: list of action dicts ({key, kind, ident, ...}) or
            signature strings ("KIND|IDENT" / "KIND:IDENT").
        prev_positions / today_positions: positions.json-shaped lists. ``None``
            means the snapshot is missing → every status is UNVERIFIED.
        open_orders: open_orders.json-shaped list (accepted for completeness;
            an open unfilled order does not change a fill status).

    Returns:
        dict mapping action key → status string in
        {EXECUTED, PARTIAL, IGNORED, UNVERIFIED}.

    Conservative by design: when the evidence is ambiguous the status is
    UNVERIFIED — never a guessed EXECUTED. Position-gone on a CLOSE-like
    action is reported EXECUTED even though "overtaken by events" (OBE) is
    indistinguishable on position diffs; renderers note this caveat.
    """
    statuses: dict = {}
    actions: list[dict] = []
    for a in (prev_actions or []):
        if isinstance(a, str):
            sig = a.replace(":", "|", 1) if ("|" not in a and ":" in a) else a
            kind, _, ident = sig.partition("|")
            actions.append({"key": action_key(kind, ident),
                            "kind": normalize_kind(kind),
                            "ident": normalize_ident(ident)})
        elif isinstance(a, dict):
            kind = a.get("kind") or ""
            ident = a.get("ident") or ""
            key = a.get("key") or action_key(kind, ident)
            if not (kind and ident) and ":" in key:
                kind, _, ident = key.partition(":")
            actions.append({"key": key, "kind": normalize_kind(kind),
                            "ident": normalize_ident(ident)})

    data_missing = prev_positions is None or today_positions is None
    prev_by_sym = _positions_by_symbol(prev_positions)
    today_by_sym = _positions_by_symbol(today_positions)

    for a in actions:
        key, kind, ident = a["key"], a["kind"], a["ident"]
        if data_missing:
            statuses[key] = "UNVERIFIED"
            continue

        contract = _parse_contract(ident)
        is_roll = _ROLL_KINDS_HINT in kind

        if contract or (ident in prev_by_sym and
                        (prev_by_sym[ident].get("assetType") or "").upper() == "OPTION"):
            # ---- option-contract semantics ----
            prev_p = _find_contract_position(ident, prev_by_sym)
            today_p = _find_contract_position(ident, today_by_sym)
            if contract is None and prev_p is not None:
                contract = {
                    "underlying": normalize_ident(prev_p.get("underlying") or ""),
                    "type": (prev_p.get("type") or "").upper(),
                    "strike": float(prev_p.get("strike", 0) or 0),
                    "exp": prev_p.get("expiration") or "",
                }
            if prev_p is None:
                # We never saw the position the action referred to — ambiguous.
                statuses[key] = "UNVERIFIED"
                continue
            if today_p is None:
                # Old contract is gone.
                if is_roll:
                    if contract and _roll_replacement_present(contract, prev_by_sym, today_by_sym):
                        statuses[key] = "EXECUTED"  # genuine roll: old gone + new later-dated short
                    else:
                        # Gone without a replacement leg — could be a close or
                        # assignment, NOT a verified roll. Don't guess.
                        statuses[key] = "UNVERIFIED"
                else:
                    # CLOSE/EXPIRE/ASSIGN semantics: gone ≈ executed. OBE is
                    # indistinguishable on position diffs — folded in with note.
                    statuses[key] = "EXECUTED"
                continue
            if abs(_qty(today_p)) < abs(_qty(prev_p)):
                statuses[key] = "PARTIAL"
            else:
                statuses[key] = "IGNORED"
            continue

        # ---- ticker-level semantics ----
        ticker = ident
        prev_p = prev_by_sym.get(ticker)
        today_p = today_by_sym.get(ticker)

        if kind in _TRIM_KINDS or kind in _EXIT_KINDS:
            if prev_p is None:
                statuses[key] = "UNVERIFIED"
            elif today_p is None or _qty(today_p) == 0:
                statuses[key] = "EXECUTED"  # position gone (or OBE — see note)
            elif _qty(today_p) < _qty(prev_p):
                # Reduced: a TRIM is satisfied by a reduction; an EXIT is only
                # partially done.
                statuses[key] = "EXECUTED" if kind in _TRIM_KINDS else "PARTIAL"
            else:
                statuses[key] = "IGNORED"
            continue

        if kind in _HEDGE_KINDS:
            # Hedge = buy protective (long) puts on the ident (e.g. SPY).
            if _new_option_on_underlying(ticker, prev_by_sym, today_by_sym,
                                         "PUT", short=False):
                statuses[key] = "EXECUTED"
            else:
                statuses[key] = "IGNORED"
            continue

        if kind in _NEW_SHORT_PUT_KINDS:
            if _new_option_on_underlying(ticker, prev_by_sym, today_by_sym,
                                         "PUT", short=True):
                statuses[key] = "EXECUTED"
            else:
                statuses[key] = "IGNORED"
            continue

        # Unknown kind on a held ticker: generic position-diff read.
        if prev_p is not None:
            if today_p is None:
                statuses[key] = "EXECUTED"
            elif _qty(today_p) != _qty(prev_p):
                statuses[key] = "PARTIAL"
            else:
                statuses[key] = "IGNORED"
        else:
            statuses[key] = "UNVERIFIED"

    return statuses


# ---------------------------------------------------------------------------
# Aging
# ---------------------------------------------------------------------------

def age_actions(today_actions: list, state: dict, reconciliation: dict,
                today_iso: str | None = None) -> tuple[dict, dict]:
    """Apply the aging ladder to today's actions.

    Args:
        today_actions: list of action dicts ({key, kind, ident, summary}).
        state: prior state map (key → {first_flagged, days_flagged, last_status}).
        reconciliation: output of :func:`reconcile` for YESTERDAY's actions.
        today_iso: today's date (YYYY-MM-DD).

    Returns:
        (updated_state, aged) where:
        - updated_state is the new state map (pruned to today's actions —
          consecutiveness is the whole point, so an item that drops off the
          list restarts its clock if it returns);
        - aged maps key → {kind, ident, summary, first_flagged, days_flagged,
          recon_status, tier1, prompt, stalled}.

    Rules: IGNORED repeat → days_flagged += 1; EXECUTED/PARTIAL → reset
    (fresh first_flagged); UNVERIFIED → carry forward unchanged (never
    escalate on missing data, never silently reset either); new item →
    first_flagged = today, days_flagged = 1.
    """
    today_iso = today_iso or datetime.now().strftime("%Y-%m-%d")
    state = dict(state or {})
    reconciliation = reconciliation or {}
    updated_state: dict = {}
    aged: dict = {}

    for a in (today_actions or []):
        key = a.get("key") or action_key(a.get("kind", ""), a.get("ident", ""))
        prior = state.get(key) if isinstance(state.get(key), dict) else None
        recon_status = reconciliation.get(key)

        if prior is None:
            first_flagged = today_iso
            days_flagged = 1
        elif recon_status in ("EXECUTED", "PARTIAL"):
            # The prior flag was acted on — this is a fresh recommendation.
            first_flagged = today_iso
            days_flagged = 1
        elif recon_status == "IGNORED":
            first_flagged = prior.get("first_flagged") or today_iso
            prev_days = int(prior.get("days_flagged", 0) or 0)
            if prior.get("last_aged") == today_iso:
                # Same-day re-run: the clock already ticked today. Re-running
                # the briefing must not double-count an ignored day.
                days_flagged = max(1, prev_days)
            else:
                days_flagged = prev_days + 1
        else:
            # UNVERIFIED / missing recon: carry forward, don't escalate.
            first_flagged = prior.get("first_flagged") or today_iso
            days_flagged = max(1, int(prior.get("days_flagged", 0) or 0))

        status_label = recon_status or ("NEW" if prior is None else "UNVERIFIED")
        entry = {
            "first_flagged": first_flagged,
            "days_flagged": days_flagged,
            "last_status": status_label,
            "last_aged": today_iso,
            "summary": (a.get("summary") or "")[:200],
        }
        updated_state[key] = entry
        aged[key] = {
            "kind": a.get("kind", ""),
            "ident": a.get("ident", ""),
            "summary": a.get("summary", ""),
            "first_flagged": first_flagged,
            "days_flagged": days_flagged,
            "recon_status": status_label,
            "tier1": days_flagged >= TIER1_DAYS,
            "prompt": days_flagged >= PROMPT_DAYS,
            "stalled": days_flagged >= STALLED_DAYS,
        }

    return updated_state, aged


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_stalled_panel(aged: dict, suppressed=None) -> list[str]:
    """Render the "## ⛔ Stalled Items" section for items ≥ STALLED_DAYS days.

    Returns [] when nothing is stalled. By design this panel goes at the VERY
    TOP of the briefing, above the header — stalled items are the first thing
    the operator sees.

    ``suppressed`` (bug #25): optional set of action keys held by a standing
    directive (``aging_info["directive_suppressed"]``). Those items are
    SKIPPED — the user is following a documented directive, not ignoring the
    recommendation, so promoting them to the stalled panel is wrong.
    """
    suppressed = set(suppressed or ())
    stalled = [(k, v) for k, v in (aged or {}).items()
               if int(v.get("days_flagged", 0) or 0) >= STALLED_DAYS
               and k not in suppressed]
    if not stalled:
        return []
    stalled.sort(key=lambda kv: -int(kv[1].get("days_flagged", 0) or 0))
    lines = [
        "## ⛔ Stalled Items",
        "",
        f"_{len(stalled)} recommendation(s) have been ignored for "
        f"{STALLED_DAYS}+ consecutive sessions. Execute today, or file a "
        f"directive (DEFER / OVERRIDE with reason) — these will not silently "
        f"repeat._",
        "",
    ]
    for key, v in stalled:
        kind = v.get("kind") or key.split(":", 1)[0]
        ident = v.get("ident") or key.split(":", 1)[-1]
        summary = v.get("summary") or ""
        lines.append(
            f"- **{kind} {ident}** — ⏳ IGNORED {int(v['days_flagged'])} DAYS "
            f"(since {v.get('first_flagged', '?')})"
            + (f" — {summary[:160]}" if summary else "")
        )
    lines.append("")
    return lines


# ── Action-list priority weighting ─────────────────────────────────────────
# Priority = obligation_at_risk × urgency_class; staleness is a TIEBREAKER,
# not the primary key. (2026-07-29 bug: IREN ROLL_OUT — $4.7K collateral,
# ⏳ 6 days — ranked #1 all week purely on staleness while META's $115K
# loss-stopped, earnings-day CLOSEs sat at #4-5.)

_URGENT_MARKER_RE = re.compile(
    r"🚨|URGENT|LOSS[ _-]?STOP|CLOSE NOW", re.IGNORECASE)
_EARNINGS_DAYS_RE = re.compile(
    r"earnings[^\n]{0,60}?(\d+)\s*d\b", re.IGNORECASE)
_DOLLAR_AMT_RE = re.compile(r"\$\s*-?([\d,]+(?:\.\d+)?)")


def _urgency_class(block_text: str) -> int:
    """3 = loss-stop / URGENT / earnings ≤2d; 2 = earnings ≤7d; 1 = default
    (including merely-stalled items — staleness is not urgency)."""
    if _URGENT_MARKER_RE.search(block_text):
        return 3
    m = _EARNINGS_DAYS_RE.search(block_text)
    if m:
        try:
            d = int(m.group(1))
        except ValueError:
            return 1
        if d <= 2:
            return 3
        if d <= 7:
            return 2
    return 1


def _obligation_at_risk(block_text: str, ident: str) -> float:
    """Dollar scale of the position the action concerns. Contract idents
    give strike × 100 (per-contract obligation — the reliable, deterministic
    read); otherwise the largest dollar figure in the block; fallback $1K so
    urgency alone can still differentiate."""
    c = _parse_contract(ident or "")
    if c and c.get("strike"):
        return float(c["strike"]) * 100.0
    amts = []
    for a in _DOLLAR_AMT_RE.findall(block_text):
        try:
            amts.append(float(a.replace(",", "")))
        except ValueError:
            continue
    return max(amts) if amts else 1000.0


def _split_action_blocks(items: list[str]) -> tuple[list[list[str]], list[str]]:
    """Group rendered action-list item lines into numbered blocks + trailing
    footer lines (transparency notes etc.)."""
    blocks: list[list[str]] = []
    footer: list[str] = []
    current: Optional[list[str]] = None
    for line in items:
        if re.match(r"^\s*\d+\.\s", line or ""):
            current = [line]
            blocks.append(current)
        elif current is not None and (line or "").startswith("  "):
            current.append(line)
        else:
            current = None
            footer.append(line)
    return blocks, footer


def _renumber(block: list[str], n: int) -> list[str]:
    head = re.sub(r"^(\s*)\d+\.", rf"\g<1>{n}.", block[0], count=1)
    return [head] + block[1:]


def apply_aging_to_action_items(items: list[str], aging_info: dict) -> list[str]:
    """Annotate + reorder + cap the rendered action-list items.

    - runs :func:`age_actions` over the parsed items (using the state and
      reconciliation in ``aging_info``), stashing the results back into
      ``aging_info`` under "aged", "updated_state" and "actions_export";
    - tags day-3+ items with "⏳ IGNORED N DAYS" (Tier-1: they sort first);
    - adds the day-5 binary prompt sub-line;
    - enforces the HEADLINE_CAP: top 5 blocks stay in the headline (aged
      Tier-1 items count first, otherwise existing priority order is
      preserved); the rest move under "### Appendix: Full Action Queue".

    Mutates ``aging_info`` in place; returns the new item lines.
    """
    today_iso = aging_info.get("today") or datetime.now().strftime("%Y-%m-%d")
    blocks, footer = _split_action_blocks(items or [])

    # Parse today's actions from the headline blocks
    today_actions: list[dict] = []
    block_keys: list[Optional[str]] = []
    seen: set[str] = set()
    for b in blocks:
        a = action_from_line(b[0])
        if a is None:
            block_keys.append(None)
            continue
        block_keys.append(a["key"])
        if a["key"] not in seen:
            seen.add(a["key"])
            today_actions.append(a)

    # Fix 4 (2026-08-04): synthetic actions — items vacated from the numbered
    # list (hedge-nag) whose aging clock must keep ticking and whose
    # ⛔ Stalled Items entry must remain. They join today's actions for
    # aging/state/JSON-export purposes but have no rendered block.
    for sa in (aging_info.get("synthetic_actions") or []):
        if not isinstance(sa, dict):
            continue
        key = sa.get("key") or action_key(sa.get("kind", ""), sa.get("ident", ""))
        if key and key not in seen:
            seen.add(key)
            today_actions.append({
                "key": key,
                "kind": normalize_kind(sa.get("kind", "")),
                "ident": normalize_ident(sa.get("ident", "")),
                "summary": sa.get("summary", ""),
            })

    updated_state, aged = age_actions(
        today_actions, aging_info.get("state") or {},
        aging_info.get("reconciliation") or {}, today_iso,
    )
    aging_info["aged"] = aged
    aging_info["updated_state"] = updated_state
    aging_info["actions_export"] = [
        {
            "key": k,
            "kind": v.get("kind"),
            "ident": v.get("ident"),
            "summary": v.get("summary", "")[:200],
            "first_flagged": v.get("first_flagged"),
            "days_flagged": v.get("days_flagged"),
            "recon_status": v.get("recon_status"),
        }
        for k, v in aged.items()
    ]

    # Annotate blocks + compute priority = obligation_at_risk × urgency_class
    annotated: list[tuple[float, int, list[str]]] = []  # (priority, days, block)
    for b, key in zip(blocks, block_keys):
        info = aged.get(key) if key else None
        days = int(info.get("days_flagged", 0) or 0) if info else 0
        block = list(b)
        if info:
            if days >= TIER1_DAYS:
                block[0] = block[0].rstrip() + f"  ⏳ IGNORED {days} DAYS"
            if days >= PROMPT_DAYS:
                block.append(
                    "   - **⛔ DECISION REQUIRED:** Execute today, or file a "
                    "directive (DEFER/OVERRIDE with reason) — this item will not "
                    "silently repeat."
                )
        block_text = "\n".join(b)  # un-annotated text (⏳ tag must not affect scoring)
        ident = (key or "").partition(":")[2]
        priority = _obligation_at_risk(block_text, ident) * _urgency_class(block_text)
        annotated.append((priority, days, block))

    # Stable reorder: highest (obligation × urgency) first; staleness
    # (days_flagged) is the tiebreaker, then the renderer's original order
    # (which already encodes category priority). A stalled $4.7K roll must
    # never outrank a loss-stopped $115K close just because it's older.
    ordered = [blk for _p, _d, blk in
               sorted(annotated, key=lambda t: (-t[0], -t[1]))]

    out: list[str] = []
    for i, blk in enumerate(ordered[:HEADLINE_CAP], start=1):
        out.extend(_renumber(blk, i))
    overflow = ordered[HEADLINE_CAP:]
    if overflow:
        out.append("")
        out.append("### Appendix: Full Action Queue")
        out.append("")
        out.append(
            f"_{len(overflow)} additional item(s) beyond the headline cap of "
            f"{HEADLINE_CAP} — lower priority, same discipline applies._"
        )
        for j, blk in enumerate(overflow, start=HEADLINE_CAP + 1):
            out.extend(_renumber(blk, j))
    out.extend(footer)
    return out


# ---------------------------------------------------------------------------
# Orchestrator-facing context builder (the only function with broad I/O)
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def load_previous_actions(today_iso: str, reports_dir: Path,
                          snapshot_root: Path) -> tuple[list[dict], Optional[str]]:
    """Find the most recent prior briefing (≤7 days back) and return its
    structured actions plus its date.

    Prefers the machine-readable JSON sidecar's "actions" array (written by
    this feature); falls back to extracting from the markdown via the same
    parser the diff panel uses.
    """
    try:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return [], None
    for offset in range(1, 8):
        d = (today - timedelta(days=offset)).isoformat()
        jpath = Path(reports_dir) / f"briefing_{d}.json"
        data = _load_json(jpath) if jpath.exists() else None
        if isinstance(data, dict) and isinstance(data.get("actions"), list):
            acts = []
            for a in data["actions"]:
                if isinstance(a, dict) and (a.get("key") or (a.get("kind") and a.get("ident"))):
                    acts.append({
                        "key": a.get("key") or action_key(a["kind"], a["ident"]),
                        "kind": normalize_kind(a.get("kind") or (a.get("key") or "").split(":")[0]),
                        "ident": normalize_ident(a.get("ident") or (a.get("key") or ":").split(":", 1)[1]),
                        "summary": a.get("summary", ""),
                    })
            return acts, d
        # Markdown fallbacks
        for mpath in (Path(reports_dir) / f"briefing_{d}.md",
                      Path(snapshot_root) / d / "briefing.md"):
            if mpath.exists():
                try:
                    return extract_actions_from_markdown(mpath.read_text()), d
                except Exception:
                    continue
    return [], None


def build_aging_context(today_iso: str, snapshot_root: Path, state_path: Path,
                        today_positions, open_orders=None,
                        reports_dir: Path = Path("reports/daily")) -> dict:
    """Build the aging context dict run_briefing passes into aggregate_briefing.

    Always returns a usable context — on first run (no prior briefing or no
    prior positions snapshot) the reconciliation is empty/UNVERIFIED and every
    action starts a fresh clock.
    """
    snapshot_root = Path(snapshot_root)
    prev_actions, prev_date = load_previous_actions(today_iso, Path(reports_dir), snapshot_root)
    prev_positions = None
    if prev_date:
        prev_positions = _load_json(snapshot_root / prev_date / "positions.json")
    if open_orders is None:
        open_orders = _load_json(snapshot_root / today_iso / "open_orders.json")
    reconciliation = reconcile(prev_actions, prev_positions, today_positions,
                               open_orders) if prev_actions else {}
    return {
        "today": today_iso,
        "prev_date": prev_date,
        "state": load_state(state_path),
        "state_path": str(state_path),
        "reconciliation": reconciliation,
        # Task #40 fix 9: the previous day's positions list rides along so
        # the Since-Yesterday panel can detect user-executed rolls.
        "prev_positions": prev_positions,
    }
