"""Shared per-run verdict state for the entry pipeline (12-entry-pipeline-spec §4/§5/§7).

Single source of truth connecting the two entry renderers:

  - ``candidate_research.render_candidate_report`` computes each ticker's
    verdict class (CANDIDATE / HELD_RSI / WATCH / AVOID), prints a
    "Verdict changes since last run" audit section (§4 — no flip without a
    named trigger), then persists the run's verdicts to
    ``state/scout_verdicts.yaml``.
  - ``when_to_enter.render_when_to_enter_report`` reads the SAME-DATE entries
    back and downgrades any ENTRY NOW whose candidate verdict is not
    CANDIDATE (§7) — the two files can no longer contradict each other.

Also home to the RSI entry-band / override-label helpers (§5) shared by both
renderers, so the band and the override phrasing can't drift apart.

Fail-soft everywhere: a missing or corrupt state file, or missing PyYAML,
never breaks a render — the reports just lose the audit/consistency extras.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML always present in the pipeline
    yaml = None

# skills/daily-portfolio-briefing/state/scout_verdicts.yaml
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / "state" / "scout_verdicts.yaml"

# The RSI entry band (12-entry-pipeline-spec §5). A candidate whose RSI falls
# outside this band only qualifies via the override path (deep drawdown +
# third-party BUY) and must be labelled as an OVERRIDE, never as "favourable".
RSI_BAND_LOW = 35.0
RSI_BAND_HIGH = 50.0

# Drawdown threshold that legitimizes an out-of-band override (spec §5).
OVERRIDE_DRAWDOWN_PCT = 50.0

_BUY_RECS = {"BUY", "STRONG_BUY", "OUTPERFORM", "TOP_15"}


def has_buy_rec(r: dict) -> bool:
    """True when the scout result carries a third-party BUY-class rec."""
    return (str(r.get("third_party_rec") or "")).upper() in _BUY_RECS


def rsi_in_band(rsi) -> bool:
    """True when RSI sits inside the 35-50 entry band."""
    try:
        return rsi is not None and RSI_BAND_LOW <= float(rsi) <= RSI_BAND_HIGH
    except (TypeError, ValueError):
        return False


def override_label(rsi, drawdown=None, buy_rec: bool = False) -> str:
    """Explicit override tag for a name qualifying despite RSI outside 35-50,
    e.g. ``RSI 53 — OVERRIDE (drawdown 74% + BUY rec)`` (spec §5). Never
    presents the out-of-band RSI as favourable."""
    parts: list[str] = []
    try:
        if drawdown is not None and float(drawdown) >= OVERRIDE_DRAWDOWN_PCT:
            parts.append(f"drawdown {float(drawdown):.0f}%")
    except (TypeError, ValueError):
        pass
    if buy_rec:
        parts.append("BUY rec")
    cause = " + ".join(parts) if parts else (
        f"outside {RSI_BAND_LOW:.0f}-{RSI_BAND_HIGH:.0f} band")
    return f"RSI {float(rsi):.0f} — OVERRIDE ({cause})"


def key_inputs(r: dict) -> dict:
    """The inputs a verdict flip is explained from (spec §4)."""
    return {
        "drawdown": r.get("drawdown_pct"),
        "rsi": r.get("rsi_14"),
        "has_buy_rec": has_buy_rec(r),
    }


# ---------------------------------------------------------------------------
# State file IO — fail-soft, audit aid only
# ---------------------------------------------------------------------------

def load(path=None) -> dict:
    """Load the stored {TICKER: {verdict, date, key_inputs}} map, or {}."""
    p = Path(path) if path else DEFAULT_STATE_PATH
    if yaml is None or not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(verdicts: dict, path=None) -> None:
    """Persist this run's verdicts. Fail-soft — never raises into a render."""
    if yaml is None:
        return
    p = Path(path) if path else DEFAULT_STATE_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(verdicts, sort_keys=True,
                                    default_flow_style=False))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Flip audit (§4) + same-run consistency (§7)
# ---------------------------------------------------------------------------

def _flip_cause(prev_ki: dict, curr_ki: dict) -> str:
    """Derive the human cause of a verdict flip from the changed key inputs.
    Falls back to "inputs changed — review" when nothing derivable changed."""
    parts: list[str] = []
    pd, cd = prev_ki.get("drawdown"), curr_ki.get("drawdown")
    try:
        if cd is not None and (pd is None or abs(float(cd) - float(pd)) >= 1.0):
            parts.append(f"drawdown {float(cd):.0f}%")
    except (TypeError, ValueError):
        pass
    pb, cb = bool(prev_ki.get("has_buy_rec")), bool(curr_ki.get("has_buy_rec"))
    if cb and not pb:
        parts.append("third-party BUY now present")
    elif pb and not cb:
        parts.append("third-party BUY dropped")
    pr, cr = prev_ki.get("rsi"), curr_ki.get("rsi")
    try:
        if pr is not None and cr is not None and abs(float(cr) - float(pr)) >= 1.0:
            parts.append(f"RSI {float(pr):.0f} → {float(cr):.0f}")
        elif cr is not None and pr is None:
            parts.append(f"RSI now {float(cr):.0f}")
    except (TypeError, ValueError):
        pass
    return ", ".join(parts) if parts else "inputs changed — review"


def flip_lines(prev: dict, curr: dict) -> list[str]:
    """One audit line per ticker whose verdict CLASS changed vs the stored run
    (spec §4): ``ORCL: AVOID → CANDIDATE (drawdown 46%, third-party BUY now
    present)``. Tickers only present on one side produce no line (a brand-new
    name isn't a flip)."""
    out: list[str] = []
    for tk in sorted(set(prev) & set(curr)):
        pe = prev.get(tk) if isinstance(prev.get(tk), dict) else {}
        ce = curr.get(tk) if isinstance(curr.get(tk), dict) else {}
        pv, cv = pe.get("verdict"), ce.get("verdict")
        if not pv or not cv or pv == cv:
            continue
        cause = _flip_cause(pe.get("key_inputs") or {}, ce.get("key_inputs") or {})
        out.append(f"{tk}: {pv} → {cv} ({cause})")
    return out


def same_date_verdicts(stored: dict, run_date: str | None = None) -> dict:
    """{TICKER: VERDICT} for entries stamped ``run_date`` (default: today).
    Missing or stale-dated entries are dropped — a stale state file means the
    consistency contract silently disables itself (spec §7 fail-soft)."""
    run_date = run_date or date.today().isoformat()
    return {tk: e.get("verdict")
            for tk, e in (stored or {}).items()
            if isinstance(e, dict) and e.get("date") == run_date and e.get("verdict")}
