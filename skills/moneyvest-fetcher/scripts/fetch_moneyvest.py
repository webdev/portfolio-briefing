#!/usr/bin/env python3
"""Moneyvest fetcher — shopping list + sentiment index + M-Scores (task #46).

Scrapes the RENDERED DOM of moneyvest.com (React SPA whose data rides
Firebase/WebSockets — network-response scraping is a dead end) via
Playwright, parses with BeautifulSoup, and writes a 20h-TTL cache the
briefing pipeline consumes as Step 1.9.

Surfaces fetched:
  - /shopping-list   — the core dataset. MULTIPLE section tables (MAG7,
                       "Long Term - High Quality", ...) whose titles render
                       as plain divs, NOT h-tags, and which lazy-load below
                       the fold — the page is scrolled to the bottom in
                       steps before parsing. Columns: Stock · Price ·
                       Fair Value · Delta% · Light Buy · Heavy Buy ·
                       No Brainer Buy · No Brainer 2027 PE. The standalone
                       "Last updated on <date>" line is captured (date
                       only). The ETFs tab is fetched too (URL variants
                       first, then a tab click); rows carry
                       asset_type: stock|etf. The DOM may render as a real
                       <table> OR a div-grid — both are parsed generically
                       off the column-header row.
  - /moneyvest-index — sentiment gauge value (e.g. 3.82) + the ACTIVE
                       label (PANIC/FEAR/UNCERTAINTY/NEUTRAL/OPTIMISTIC/
                       GREED/EUPHORIA) for the S&P 500 and NASDAQ 100 tabs.
                       The active badge is the bare label WITHOUT an
                       adjacent numeric range — the legend lists all seven
                       labels alongside ranges like "0.00 - 2.00" (the
                       2026-08-06 probe grabbed PANIC from the legend while
                       the gauge read OPTIMISTIC). Legend bands are scraped
                       and cross-checked against the value; a disagreement
                       surfaces a band_warning, never a fabricated label.
  - /stock/<T>/overview — per-stock "M-Score · 4.05/5.00" badge, fetched
                       ONLY for a bounded ticker set the caller passes
                       (held + candidate names), skipping gracefully
                       per-ticker.

Auth (hardened 2026-08-06 after a real probe failure — the 45s
wait_for_selector("table tr td") timeout was actually a LOGIN WALL):
  1. Load /shopping-list, then detect login state FIRST from the rendered
     header text — the logged-IN page shows "Sign Out" (+ user name); the
     logged-OUT page shows "Sign In"/"Sign Up" or redirects to a landing
     route.
  2. Logged out + MONEYVEST_EMAIL/MONEYVEST_PASSWORD present → scripted
     login, verify, retry.
  3. Logged out + creds absent + --headed → wait for MANUAL login in the
     visible browser, then persist storage state so creds stay optional.
  4. Logged out + creds absent + headless → fail with an explicit
     actionable message (never a bare selector timeout).
Storage state persists at ``~/.config/portfolio-briefing/
moneyvest_state.json`` (0600). Credentials are read via os.getenv only
and NEVER logged.

Debug artifacts: ANY fetch failure dumps page.html + screenshot.png +
console.log (probe-stage log + browser console) under
``state/moneyvest_debug/<timestamp>/`` (last 3 dumps kept) and prints the
path so the dump can be sent to Claude to fix selectors. ``--dump`` saves
the same bundle on SUCCESS as well, for parser iteration on the real DOM.

Fail-open contract: every error path returns the prior cache (labeled
``provenance: "stale_cache"``) or an empty payload — the briefing never
blocks on Moneyvest. When Playwright is not installed, the fetcher
degrades to cache-only mode with a one-line stderr notice.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - bs4 is a hard repo dep
    BeautifulSoup = None

BASE_URL = "https://moneyvest.com"
DEFAULT_STATE_PATH = Path.home() / ".config" / "portfolio-briefing" / "moneyvest_state.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_PATH = _REPO_ROOT / "state" / "cache" / "moneyvest.json"
DEFAULT_DEBUG_DIR = _REPO_ROOT / "state" / "moneyvest_debug"
CACHE_TTL_HOURS = 20.0  # daily refresh with slack for run-time drift
DEBUG_DUMPS_KEPT = 3

INDEX_LABELS = ("PANIC", "FEAR", "UNCERTAINTY", "NEUTRAL",
                "OPTIMISTIC", "GREED", "EUPHORIA")

# Text landmarks known from live inspection of /shopping-list — column
# headers of the buy-ladder. Waiting on TEXT, not <table>, survives a
# table→div-grid re-render (the 2026-08-06 probe failure mode #2).
SHOPPING_LANDMARKS = ("Fair Value", "Light Buy", "Heavy Buy", "No Brainer")

NOT_LOGGED_IN_MSG = (
    "not logged in and MONEYVEST_EMAIL/MONEYVEST_PASSWORD not set in .env — "
    "add credentials or run --headed to log in manually once (session persists)"
)

# SPA + heavy ad scripts: networkidle never settles — poll rendered text.
NAV_TIMEOUT_MS = 60_000
SELECTOR_TIMEOUT_MS = 45_000
MANUAL_LOGIN_TIMEOUT_S = 240
# Post-submit dialog-close poll: up to 20s in 1s steps. The old fixed 4s
# check declared a false rejection while the button read "Signing In...".
LOGIN_SUBMIT_TIMEOUT_S = 20


def _utcnow() -> datetime:
    """Timezone-aware now, returned NAIVE-UTC (internal convention —
    replaces the deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Parsing (pure functions — unit-tested on constructed DOM fixtures)
# --------------------------------------------------------------------------

_MONEY_RE = re.compile(r"(-?)\$?\s*([0-9][0-9,]*\.?[0-9]*)")
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_LAST_UPDATED_RE = re.compile(
    r"Last updated on\s+([A-Za-z]+ \d{1,2}, \d{4})", re.IGNORECASE)
_MSCORE_RE = re.compile(r"M[\s-]?Score\s*[·:\-]?\s*([0-9]\.[0-9]{1,2})\s*/\s*5",
                        re.IGNORECASE)
_SIGN_OUT_RE = re.compile(r"\bSign\s*Out\b", re.IGNORECASE)
_SIGN_IN_UP_RE = re.compile(r"\bSign\s*(?:In|Up)\b", re.IGNORECASE)
_USER_NAME_RE = re.compile(
    r"([A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+)?)\s*[\n|·•]?\s*Sign\s*Out",
    re.IGNORECASE)


def parse_money(text: str | None) -> float | None:
    """'$196.00' / '1,234.5' → float; None-safe; n/a-safe."""
    if not text:
        return None
    m = _MONEY_RE.search(str(text))
    if not m:
        return None
    try:
        return float(m.group(1) + m.group(2).replace(",", ""))
    except ValueError:
        return None


def _extract_ticker(cell_text: str) -> str | None:
    """LAST 1-5-char ALL-CAPS token in the Stock/ETF cell. The live cells
    render name-then-ticker ('State Street SPDR S&P 500 ETF\\nSPY',
    'Apple Inc.\\nAAPL') — taking the FIRST token returned SPDR/S/ETF for
    the index funds (2026-08-06 live parse). The final token is the ticker
    on every observed row, stock and ETF alike."""
    toks = _TICKER_RE.findall(cell_text or "")
    return toks[-1] if toks else None


def _map_columns(headers: list[str]) -> dict:
    """Fuzzy header → field mapping (site copy shifts; keys must not)."""
    mapping: dict[int, str] = {}
    for i, h in enumerate(headers):
        hl = (h or "").strip().lower()
        if not hl:
            continue
        if "stock" in hl or "ticker" in hl or "company" in hl or hl == "etf":
            # The ETFs tab (verified live 2026-08-06) heads its ticker
            # column "ETF" and carries a different schema: ETF · Price ·
            # Light Buy · Delta (%) · Heavy Buy · AUM · TER · Holdings ·
            # T10 Conc. · DY (TTM) — no Fair Value / No Brainer ladder.
            mapping[i] = "ticker"
        elif "fair" in hl:
            mapping[i] = "fair_value"
        elif "light" in hl:
            mapping[i] = "light_buy"
        elif "heavy" in hl:
            mapping[i] = "heavy_buy"
        elif "brainer" in hl and ("pe" in hl or "p/e" in hl or "2027" in hl):
            mapping[i] = "pe_2027"
        elif "brainer" in hl:
            mapping[i] = "no_brainer"
        elif hl.startswith("price") or hl == "last":
            mapping[i] = "price"
        elif "delta" in hl:
            mapping[i] = "delta_pct"
        elif "aum" in hl:
            mapping[i] = "aum"
        elif "ter" in hl:
            mapping[i] = "ter"
        elif "holding" in hl:
            mapping[i] = "holdings"
        elif "conc" in hl:
            mapping[i] = "t10_concentration"
        elif hl.startswith("dy") or "yield" in hl:
            mapping[i] = "dividend_yield"
        elif "pe" in hl or "p/e" in hl:
            mapping[i] = "pe_2027"
    return mapping


def _child_cell_texts(el) -> list[str]:
    """Row-like element → its cell texts. <tr> uses td/th; anything else
    (div-grid rows) uses direct element children."""
    if el.name == "tr":
        cells = el.find_all(["td", "th"], recursive=False)
    else:
        cells = el.find_all(True, recursive=False)
    return [c.get_text(" ", strip=True) for c in cells]


def _is_header_texts(texts: list[str]) -> bool:
    """True when a cell-text list IS the shopping-list column-header row:
    ≥3 distinct mapped fields incl. fair_value + a buy-ladder column, each
    matched cell short (a container whose first child concatenates the
    whole header row must NOT qualify)."""
    if len(texts) < 3:
        return False
    colmap = _map_columns(texts)
    fields = set(colmap.values())
    if "fair_value" not in fields or len(fields) < 3:
        return False
    if not ({"light_buy", "heavy_buy", "no_brainer"} & fields):
        return False
    return all(len(texts[i]) < 30 for i in colmap)


def list_headers(html: str) -> list[str]:
    """First recognizable column-header row's cell texts (probe stage log:
    'found headers: [...]'). [] when none found."""
    if BeautifulSoup is None or not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(True):
        texts = _child_cell_texts(el)
        if _is_header_texts(texts):
            return texts
    return []


def _build_rec(texts: list[str], colmap: dict, section: str) -> dict | None:
    """Cell texts + column map → row record, or None when no ticker
    (rows without a recognizable ticker are skipped, never fabricated —
    hard rule #19)."""
    rec: dict = {"section": section}
    for i, txt in enumerate(texts):
        field = colmap.get(i)
        if not field:
            continue
        if field == "ticker":
            rec["ticker"] = _extract_ticker(txt)
        elif field == "delta_pct":
            rec["delta_pct"] = parse_money(txt.replace("%", ""))
        elif field == "pe_2027":
            rec["pe_2027"] = parse_money(txt)
        else:
            rec[field] = parse_money(txt)
    if not rec.get("ticker"):
        return None
    for k in ("price", "fair_value", "light_buy",
              "heavy_buy", "no_brainer", "pe_2027"):
        rec.setdefault(k, None)
    return rec


# Section-name candidates must be short titles, not header cells, dates,
# prices, or the "Last updated ..." line.
_HEADER_CELL_WORDS = {
    "stock", "ticker", "company", "price", "last",
    "fair value", "delta", "delta %", "delta (%)",
    "light buy", "heavy buy", "no brainer buy", "no brainer 2027 pe",
}
_SECTION_SKIP_RE = re.compile(
    r"^(?:last updated|updated)\b|[$%]|^[\d.,\s\-+/()]+$", re.IGNORECASE)
_DATEISH_RE = re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$")


def _looks_like_section_name(text: str | None) -> bool:
    """True for short title-like text ('MAG7', 'Long Term - High Quality');
    False for header cells, prices, dates, and boilerplate."""
    t = (text or "").strip()
    if not t or len(t) > 48:
        return False
    if not re.search(r"[A-Za-z]", t):
        return False
    if t.lower() in _HEADER_CELL_WORDS:
        return False
    if _SECTION_SKIP_RE.search(t) or _DATEISH_RE.match(t):
        return False
    return True


def _section_for(el, section_hint: str) -> str:
    """Section name for a table/grid: the nearest PRECEDING text node that
    looks like a section title. The real page renders 'MAG7' etc. as plain
    divs, NOT h-tags — the 2026-08-06 probe's heading-only search yielded
    sections: ['unknown']. Text inside other tables (a prior section's
    cells) is skipped; heading tags remain the fallback."""
    for node in el.find_all_previous(string=True):
        if node.find_parent("table") is not None:
            continue
        if _looks_like_section_name(str(node)):
            return str(node).strip()
    heading = el.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading and heading.get_text(strip=True):
        return heading.get_text(strip=True)
    return section_hint or "unknown"


def _parse_tables(soup, section_hint: str) -> list[dict]:
    """Walk ALL tables in document order, carrying the last header mapping
    forward. The live page (verified against the real authenticated DOM,
    2026-08-06) renders the FIRST section (MAG7) as one table with an
    embedded header row, but LATER sections as a title div + a 1-row
    HEADER-ONLY table + a separate HEADERLESS body table ("Long Term -
    High Quality": 33 body rows). A headerless table inherits the most
    recent column map and section — that's the fix for 'parsed 7 rows
    (sections: [MAG7])' when the page holds 40+."""
    rows_out: list[dict] = []
    last_colmap: dict = {}
    last_section: str = section_hint or "unknown"
    for table in soup.find_all("table"):
        header_cells: list[str] = []
        thead = table.find("thead")
        if thead:
            header_cells = [th.get_text(" ", strip=True)
                            for th in thead.find_all(["th", "td"])]
        else:
            first = table.find("tr")
            if first:
                header_cells = [c.get_text(" ", strip=True)
                                for c in first.find_all(["th", "td"])]
        colmap = _map_columns(header_cells)
        if "ticker" in colmap.values():
            # Header-bearing table: (re)anchor section + column map here.
            last_colmap = colmap
            last_section = _section_for(table, section_hint)
        elif last_colmap:
            # Headerless BODY table — inherit the previous header table's
            # mapping and section.
            colmap = last_colmap
        else:
            continue  # no mapping yet — not shopping-list content
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            rec = _build_rec(texts, colmap, last_section)
            if rec:
                rows_out.append(rec)
    return rows_out


def _rows_after(header_el, colmap: dict, section: str) -> list[dict]:
    """Iterate the row-like siblings AFTER a div-grid header row. A
    single-cell sibling with short alpha text (e.g. 'MAG7') updates the
    current section; another header row ends the block."""
    recs: list[dict] = []
    for sib in header_el.find_next_siblings(True):
        texts = _child_cell_texts(sib)
        if _is_header_texts(texts):
            break
        if not texts:
            continue
        if len(texts) == 1:
            if _looks_like_section_name(texts[0]):
                section = texts[0].strip()  # interleaved section-header row
            continue
        rec = _build_rec(texts, colmap, section)
        if rec:
            recs.append(rec)
    return recs


def _parse_div_grids(soup, section_hint: str) -> list[dict]:
    """Generic non-<table> path: locate the column-header row by its TEXT
    landmarks, then walk row-like siblings (or the header wrapper's
    siblings) extracting ticker + numeric cells in column order."""
    rows_out: list[dict] = []
    for el in soup.find_all(True):
        if el.name in ("table", "thead", "tbody", "tr", "td", "th"):
            continue
        if el.find_parent("table") is not None:
            continue
        texts = _child_cell_texts(el)
        if not _is_header_texts(texts):
            continue
        colmap = _map_columns(texts)
        section = _section_for(el, section_hint)
        recs = _rows_after(el, colmap, section)
        if not recs and el.parent is not None:
            # Header lives in its own wrapper; rows are siblings of it.
            recs = _rows_after(el.parent, colmap, section)
        rows_out.extend(recs)
    return rows_out


def parse_shopping_list(html: str, section_hint: str = "",
                        asset_type: str = "stock") -> tuple[list[dict], str | None]:
    """Parse the rendered /shopping-list DOM → (rows, list_last_updated).

    Handles BOTH real <table> markup and div-grid layouts: the column-header
    row ("Fair Value" / "Light Buy" / "Heavy Buy" / "No Brainer Buy") is the
    anchor. Section names come from the nearest preceding title-like text
    node (plain divs on the real page), heading tags, or interleaved
    single-cell section rows. ALL section tables are parsed. Every row is
    labeled with ``asset_type`` ("stock" default; "etf" for the ETFs tab).
    Rows without a recognizable ticker are skipped, never fabricated (hard
    rule #19). ``list_last_updated`` is the bare date ("Jun 8, 2026") — the
    old open-ended capture over-read run-on SPA text.
    """
    if BeautifulSoup is None:
        return [], None
    soup = BeautifulSoup(html, "html.parser")

    m = _LAST_UPDATED_RE.search(soup.get_text(" ", strip=True))
    last_updated = m.group(1).strip() if m else None

    rows_out = _parse_tables(soup, section_hint)
    rows_out.extend(_parse_div_grids(soup, section_hint))

    # De-dup by ticker (first occurrence wins) in case both paths fire.
    seen: set = set()
    unique: list[dict] = []
    for r in rows_out:
        key = (r.get("ticker"), r.get("section"))
        if key in seen:
            continue
        seen.add(key)
        r["asset_type"] = asset_type
        unique.append(r)
    return unique, last_updated


# Legend entries render as "LABEL 0.00 - 2.00"; the ACTIVE badge is a bare
# label word with NO adjacent numeric range.
_INDEX_RANGE_RE = re.compile(r"[0-9]\.[0-9]{1,2}\s*[-–—]\s*[0-9]\.[0-9]{1,2}")
_RANGE_AFTER_RE = re.compile(
    r"^[\s·:,()\-–—]*[0-9]\.[0-9]{1,2}\s*[-–—]\s*[0-9]\.[0-9]{1,2}")
_RANGE_BEFORE_RE = re.compile(
    r"[0-9]\.[0-9]{1,2}\s*[-–—]\s*[0-9]\.[0-9]{1,2}[\s·:,()\-–—]*$")


def parse_index_bands(text: str) -> dict:
    """Label → (lo, hi) scraped from the legend list, where every label
    renders WITH its numeric range (e.g. 'OPTIMISTIC 3.50 - 4.00'). Bands
    are data-driven, never hardcoded (hard rule #19)."""
    bands: dict = {}
    upper = (text or "").upper()
    for label in INDEX_LABELS:
        m = re.search(
            rf"\b{label}\b[\s·:,()]*([0-9]\.[0-9]{{1,2}})\s*[-–—]\s*"
            rf"([0-9]\.[0-9]{{1,2}})", upper)
        if m:
            bands[label] = (float(m.group(1)), float(m.group(2)))
    return bands


def _active_index_label(text: str) -> str | None:
    """The ACTIVE gauge label: a bare label occurrence with NO immediately
    adjacent numeric range. The legend lists all seven labels alongside
    ranges — the 2026-08-06 probe grabbed PANIC from the legend while the
    gauge badge read OPTIMISTIC."""
    upper = (text or "").upper()
    for label in INDEX_LABELS:
        for m in re.finditer(rf"\b{label}\b", upper):
            if _RANGE_AFTER_RE.match(upper[m.end():m.end() + 28]):
                continue
            if _RANGE_BEFORE_RE.search(upper[max(0, m.start() - 28):m.start()]):
                continue
            return label
    return None


def derive_index_label(value: float | None, bands: dict) -> str | None:
    """Map the gauge value onto the scraped legend bands (first match in
    label order wins on shared boundaries). None-safe, never fabricated."""
    if value is None:
        return None
    for label, (lo, hi) in bands.items():
        if lo <= value <= hi:
            return label
    return None


def parse_index_text(text: str) -> dict | None:
    """Rendered gauge text → {"value": 3.82, "label": "OPTIMISTIC"}.

    Value: first decimal that is NOT part of a legend range. Label: the
    ACTIVE badge (bare, range-free occurrence), falling back to the label
    derived from value + scraped legend bands. When both exist and disagree,
    the scraped badge wins and a "band_warning" key is added (WARN, per the
    published-bands cross-check)."""
    if not text:
        return None
    range_spans = [m.span() for m in _INDEX_RANGE_RE.finditer(text)]
    value = None
    for m in re.finditer(r"\b([0-9]\.[0-9]{1,2})\b", text):
        if any(a <= m.start() and m.end() <= b for a, b in range_spans):
            continue
        value = float(m.group(1))
        break
    bands = parse_index_bands(text)
    scraped = _active_index_label(text)
    derived = derive_index_label(value, bands)
    # DERIVED (value × scraped legend bands) wins: the live page proved the
    # bare-badge heuristic unreliable (legend labels and their ranges sit in
    # separate DOM nodes, so "PANIC" appears range-free in text order and was
    # scraped as active while the gauge read OPTIMISTIC at 3.82). Value +
    # bands are both scraped data — deriving from them fabricates nothing —
    # and the badge remains a cross-check that surfaces a warning on
    # disagreement.
    label = derived or scraped
    if value is None and label is None:
        return None
    out = {"value": value, "label": label}
    if scraped and derived and scraped != derived:
        out["band_warning"] = (
            f"badge scrape said {scraped}; using {derived} from legend bands "
            f"for value {value} (badge heuristic unreliable on this DOM)")
    return out


def parse_m_score(text: str) -> float | None:
    """'M-Score · 4.05/5.00' badge text → 4.05."""
    m = _MSCORE_RE.search(text or "")
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------
# Login-state detection (pure functions — unit-tested)
# --------------------------------------------------------------------------

def detect_login_state(body_text: str | None, url: str | None = None) -> str:
    """'logged_in' / 'logged_out' / 'unknown' from rendered header text.

    The logged-IN page shows "Sign Out" (+ the user's name) in the header;
    the logged-OUT page shows "Sign In"/"Sign Up", or redirects off
    /shopping-list to a landing/login route.
    """
    t = body_text or ""
    if _SIGN_OUT_RE.search(t):
        return "logged_in"
    if _SIGN_IN_UP_RE.search(t):
        return "logged_out"
    u = (url or "").lower()
    if u and ("login" in u or "shopping-list" not in u):
        return "logged_out"
    return "unknown"


def detect_user_name(body_text: str | None) -> str | None:
    """Best-effort user display name adjacent to 'Sign Out'; None-safe.
    Never fabricated — None when the pattern doesn't match."""
    m = _USER_NAME_RE.search(body_text or "")
    if not m:
        return None
    name = m.group(1).strip()
    return name or None


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def load_cache(path: Path | str) -> dict | None:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_is_fresh(cache: dict | None, ttl_hours: float = CACHE_TTL_HOURS,
                   now: datetime | None = None) -> bool:
    if not cache or not cache.get("as_of"):
        return False
    try:
        as_of = datetime.fromisoformat(str(cache["as_of"]).replace("Z", "+00:00"))
        if as_of.tzinfo:
            as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return False
    now = now or _utcnow()
    return (now - as_of) <= timedelta(hours=ttl_hours)


def staleness_hours(cache: dict | None, now: datetime | None = None) -> float | None:
    if not cache or not cache.get("as_of"):
        return None
    try:
        as_of = datetime.fromisoformat(str(cache["as_of"]).replace("Z", "+00:00"))
        if as_of.tzinfo:
            as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
    now = now or _utcnow()
    return round((now - as_of).total_seconds() / 3600.0, 1)


def write_cache(path: Path | str, payload: dict) -> None:
    """Atomic write (tempfile + os.replace) — never a torn cache."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Debug artifacts — dumped on ANY fetch failure
# --------------------------------------------------------------------------

def dump_debug(page, stage_log: list[str], error: str | None = None,
               debug_dir: Path | str = DEFAULT_DEBUG_DIR,
               keep: int = DEBUG_DUMPS_KEPT) -> Path | None:
    """Save page.html (rendered DOM), screenshot.png, and console.log of
    the probe steps under <debug_dir>/<timestamp>/; keep only the last
    ``keep`` dumps. Best-effort — never raises."""
    try:
        root = Path(debug_dir)
        d = root / _utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:
            (d / "page.html").write_text(
                f"<!-- page.content() failed: {type(e).__name__} -->",
                encoding="utf-8")
        try:
            page.screenshot(path=str(d / "screenshot.png"), full_page=True)
        except Exception:
            pass
        lines = list(stage_log or [])
        if error:
            lines.append(f"ERROR: {error}")
        (d / "console.log").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
        subdirs = sorted(p for p in root.iterdir() if p.is_dir())
        for old in subdirs[:-keep] if keep > 0 else subdirs:
            shutil.rmtree(old, ignore_errors=True)
        return d
    except Exception:
        return None


# --------------------------------------------------------------------------
# Playwright fetch (network side — NOT exercised by tests)
# --------------------------------------------------------------------------

def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _body_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _wait_for_any_text(page, needles: tuple[str, ...],
                       timeout_ms: int, poll_ms: int = 1000) -> str:
    """Poll the rendered DOM until any landmark string appears. Returns the
    landmark found; raises TimeoutError listing what never appeared."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        try:
            low = page.content().lower()
        except Exception:
            low = ""
        for n in needles:
            if n.lower() in low:
                return n
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"none of the landmarks {list(needles)} appeared within "
                f"{timeout_ms}ms")
        page.wait_for_timeout(poll_ms)


def _save_state(context, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_path))
    os.chmod(state_path, 0o600)


def _login_flow(page, headless: bool, stages: list[str]) -> None:
    """Run when /shopping-list rendered logged OUT. Scripted login with env
    creds; headed manual login otherwise; explicit failure when neither is
    possible. Credentials are never logged."""
    email = os.getenv("MONEYVEST_EMAIL")
    password = os.getenv("MONEYVEST_PASSWORD")
    if email and password:
        try:
            _scripted_login(page, email, password, stages)
            return
        except Exception as e:
            if headless:
                raise
            # Headed: fall through to manual login rather than dying — the
            # user is watching the window and can finish it by hand.
            stages.append(f"scripted login failed ({e}) — falling back to "
                          f"manual login (headed)")
            print(f"[moneyvest] scripted login failed: {e}\n"
                  f"[moneyvest] please log in manually in the browser window",
                  file=sys.stderr)
    if not headless:
        stages.append("login state: LOGGED OUT — waiting for manual login "
                      "(headed)")
        print(f"[moneyvest] complete the login in the browser window "
              f"(waiting up to {MANUAL_LOGIN_TIMEOUT_S}s; session will be "
              f"saved for future headless runs)...", file=sys.stderr)
        deadline = time.monotonic() + MANUAL_LOGIN_TIMEOUT_S
        while time.monotonic() < deadline:
            page.wait_for_timeout(3000)
            if detect_login_state(_body_text(page), page.url) == "logged_in":
                return
        raise RuntimeError(
            f"manual login not completed within {MANUAL_LOGIN_TIMEOUT_S}s")
    stages.append("login state: LOGGED OUT — no credentials available")
    print(f"[moneyvest] {NOT_LOGGED_IN_MSG}", file=sys.stderr)
    raise RuntimeError(NOT_LOGGED_IN_MSG)


def _scripted_login(page, email: str, password: str,
                    stages: list[str]) -> None:
    if True:  # noqa: SIM108 — kept for indentation stability of the block below
        # The site has NO /login route (404s into the SPA shell). The Sign-In
        # UI is a Radix modal that auto-opens on any page when the session is
        # expired — and it's already open on the /shopping-list we just
        # rendered. Fill IN the dialog; the submit button is disabled until
        # React registers both fields, so type() (real key events) beats
        # fill(), and we assert enablement before clicking (debug dump
        # 20260806T204422: fills completed but a fresh empty modal came back).
        stages.append("login state: LOGGED OUT — running scripted login (modal)")
        dialog = page.locator("[role=dialog]")
        if not dialog.count():
            # Modal not auto-open — open it via the header Sign In button.
            page.get_by_role("button", name="Sign In").first.click()
            page.wait_for_selector("[role=dialog] input#password",
                                   timeout=NAV_TIMEOUT_MS)
        dialog = page.locator("[role=dialog]")
        dialog.locator("input#email").click()
        dialog.locator("input#email").type(email, delay=25)
        dialog.locator("input#password").click()
        dialog.locator("input#password").type(password, delay=25)
        submit = dialog.locator("button[type=submit]")
        try:
            # Playwright auto-waits for enabled; a persistent disabled state
            # means React never registered the input — surface that precisely.
            submit.click(timeout=10_000)
        except Exception as e:
            raise RuntimeError(
                "Sign In button never enabled after typing credentials — the "
                "form did not register input (React state). Run --headed and "
                "log in manually once; the session will persist."
            ) from e
        # Poll for the dialog to close (success) — up to
        # LOGIN_SUBMIT_TIMEOUT_S, checking every 1s. "Signing In..." visible
        # means the request is still in flight; keep waiting. (The old fixed
        # 4s wait declared a false rejection mid-flight.)
        deadline = time.monotonic() + LOGIN_SUBMIT_TIMEOUT_S
        while (page.locator("[role=dialog] button[type=submit]").count()
               and time.monotonic() < deadline):
            page.wait_for_timeout(1000)
        if page.locator("[role=dialog] button[type=submit]").count():
            # Modal still open after the full poll. Capture its visible text
            # so the user sees the site's own words (wrong password vs
            # Patreon-linked account vs slow server). Never log the
            # credentials themselves.
            try:
                modal_text = page.locator("[role=dialog]").inner_text()[:400]
            except Exception:
                modal_text = "(modal text unavailable)"
            if "signing in" in modal_text.lower():
                raise RuntimeError(
                    f"login still in flight after {LOGIN_SUBMIT_TIMEOUT_S}s "
                    "(button reads 'Signing In...') — slow network or server; "
                    "retry, or run --headed to watch it complete."
                )
            raise RuntimeError(
                "login rejected by the site — modal still open. Site says: "
                f"{modal_text!r}. If your Moneyvest account is Patreon-linked "
                "or the password has special characters, quote the value in "
                ".env or run --headed and log in manually once (session "
                "persists)."
            )
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(2000)


def _scroll_page(page, max_steps: int = 30, settle_ms: int = 350) -> None:
    """Scroll to the page bottom in viewport steps so lazy-loaded section
    tables below the fold render (the 2026-08-06 probe caught only MAG7's
    7 rows — 'Long Term - High Quality' etc. were never in the DOM).
    Best-effort — never raises."""
    try:
        prev_height = -1
        for _ in range(max_steps):
            page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight))")
            page.wait_for_timeout(settle_ms)
            height = page.evaluate("document.body.scrollHeight")
            at_bottom = page.evaluate(
                "window.scrollY + window.innerHeight >= "
                "document.body.scrollHeight - 4")
            if at_bottom and height == prev_height:
                break
            prev_height = height
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


def _fetch_etf_tab(page, stages: list[str], stock_tickers: set) -> list[dict]:
    """Fetch the ETFs tab of /shopping-list (best-effort — never raises).

    Order: URL variants (?tab=etfs, /shopping-list/etfs) — cheap when the
    SPA routes them; otherwise click the 'ETFs' tab text in our automated
    browser. "New content" = parsed rows whose tickers are NOT already in
    the stock set — a variant the SPA ignores just re-renders the stock
    view and yields nothing new. Rows come back labeled asset_type='etf'.
    """
    def _grab() -> list[dict]:
        page.wait_for_timeout(2500)
        _scroll_page(page)
        rows, _ = parse_shopping_list(page.content(), section_hint="ETFs",
                                      asset_type="etf")
        return [r for r in rows if r["ticker"] not in stock_tickers]

    for url in (f"{BASE_URL}/shopping-list?tab=etfs",
                f"{BASE_URL}/shopping-list/etfs"):
        try:
            page.goto(url, timeout=NAV_TIMEOUT_MS)
            new = _grab()
            if new:
                stages.append(f"ETFs via {url.removeprefix(BASE_URL)}: "
                              f"{len(new)} rows")
                return new
        except Exception:
            continue
    try:
        page.goto(f"{BASE_URL}/shopping-list", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(2000)
        tab = page.get_by_role("tab", name=re.compile(r"ETFs?", re.I))
        if not tab.count():
            tab = page.get_by_text(re.compile(r"^\s*ETFs?\s*$", re.I))
        if tab.count():
            tab.first.click()
            new = _grab()
            if new:
                stages.append(f"ETFs via tab click: {len(new)} rows")
                return new
        stages.append("ETFs: tab not found / no new rows")
    except Exception as e:
        stages.append(f"ETFs: unavailable ({type(e).__name__})")
    return []


def fetch_live(tickers: list[str] | None = None,
               state_path: Path = DEFAULT_STATE_PATH,
               headless: bool = True,
               debug_dir: Path | str = DEFAULT_DEBUG_DIR,
               dump: bool = False) -> dict:
    """Fetch all three surfaces via Playwright. Raises on total failure —
    the caller (run) turns that into a stale-cache fallback. Any failure
    first dumps page.html/screenshot/console.log under debug_dir."""
    from playwright.sync_api import sync_playwright

    stages: list[str] = []
    payload: dict = {
        "as_of": _utcnow().isoformat(timespec="seconds") + "Z",
        "shopping_list": [], "index": {}, "m_scores": {},
        "list_last_updated": None, "provenance": "live",
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx_kwargs = {}
        if state_path.exists():
            ctx_kwargs["storage_state"] = str(state_path)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.on("console",
                lambda m: stages.append(f"[browser:{m.type}] {m.text}"))
        try:
            # 1) Shopping list — login-state detection FIRST, then landmarks.
            page.goto(f"{BASE_URL}/shopping-list", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(2500)  # SPA header hydration
            stages.append("loaded /shopping-list")
            state = detect_login_state(_body_text(page), page.url)
            if state != "logged_in":
                _login_flow(page, headless, stages)
                page.goto(f"{BASE_URL}/shopping-list", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(2500)
                state = detect_login_state(_body_text(page), page.url)
                if state != "logged_in":
                    raise RuntimeError(
                        "login verification failed — /shopping-list still "
                        "renders logged out after the login flow")
            name = detect_user_name(_body_text(page))
            stages.append(f"login state: LOGGED IN{' as ' + name if name else ''}")
            _save_state(context, state_path)  # refresh session for next run

            # Wait on TEXT landmarks, never on <table> markup.
            landmark = _wait_for_any_text(page, SHOPPING_LANDMARKS,
                                          SELECTOR_TIMEOUT_MS)
            stages.append(f"landmark hit: {landmark!r}")
            # Lazy-load: sections below the fold only render on scroll.
            _scroll_page(page)
            html = page.content()
            headers = list_headers(html)
            stages.append(f"found headers: {headers}")
            rows, last_updated = parse_shopping_list(html)
            payload["shopping_list"] = rows
            payload["list_last_updated"] = last_updated
            etf_rows = _fetch_etf_tab(page, stages,
                                      {r["ticker"] for r in rows})
            payload["shopping_list"].extend(etf_rows)
            sections = sorted({r.get("section") or "unknown"
                               for r in payload["shopping_list"]})
            stages.append(f"parsed {len(rows)} stock + {len(etf_rows)} ETF "
                          f"rows (sections: {sections})")
            if not payload["shopping_list"]:
                raise RuntimeError(
                    f"0 rows parsed after landmark {landmark!r} hit — "
                    f"parser/selector drift (headers seen: {headers})")

            # 2) Sentiment index — S&P 500 + NASDAQ 100 tabs.
            try:
                page.goto(f"{BASE_URL}/moneyvest-index",
                          timeout=NAV_TIMEOUT_MS)
                _wait_for_any_text(page, INDEX_LABELS, 20_000)
                sp = parse_index_text(_body_text(page))
                if sp:
                    payload["index"]["sp500"] = sp
                ndx_tab = page.get_by_role(
                    "tab", name=re.compile("NASDAQ", re.I))
                if not ndx_tab.count():
                    ndx_tab = page.get_by_text(re.compile("NASDAQ", re.I))
                if ndx_tab.count():
                    ndx_tab.first.click()
                    page.wait_for_timeout(2500)
                    ndx = parse_index_text(_body_text(page))
                    if ndx:
                        payload["index"]["ndx"] = ndx
                sp = payload["index"].get("sp500") or {}
                stages.append(f"index: {sp.get('value')} {sp.get('label')}")
                for key, gauge in payload["index"].items():
                    if gauge.get("band_warning"):
                        stages.append(f"index WARN ({key}): "
                                      f"{gauge['band_warning']}")
                        print(f"[moneyvest] WARN index {key}: "
                              f"{gauge['band_warning']}", file=sys.stderr)
            except Exception as e:
                stages.append(f"index: unavailable ({type(e).__name__})")
                print(f"[moneyvest] index fetch failed: {type(e).__name__}",
                      file=sys.stderr)

            # 3) Per-stock M-Scores — bounded set, skip gracefully per ticker.
            for t in sorted({str(t).upper() for t in (tickers or []) if t}):
                try:
                    page.goto(f"{BASE_URL}/stock/{t}/overview",
                              timeout=NAV_TIMEOUT_MS)
                    _wait_for_any_text(page, ("M-Score", "M Score"), 10_000)
                    score = parse_m_score(_body_text(page))
                    if score is not None:
                        payload["m_scores"][t] = score
                except Exception:
                    continue
            note = "" if tickers else " (pass --tickers to fetch M-Scores)"
            stages.append(f"M-Scores: {len(payload['m_scores'])} fetched{note}")

            print("[moneyvest] " + " → ".join(
                s for s in stages if not s.startswith("[browser:")))
            if dump:
                d = dump_debug(page, stages, error=None, debug_dir=debug_dir)
                if d:
                    print(f"[moneyvest] debug dump (success): {d}")
        except Exception as e:
            d = dump_debug(page, stages, error=repr(e), debug_dir=debug_dir)
            if d:
                print(f"[moneyvest] debug dump: {d} — send this to Claude "
                      f"to fix selectors", file=sys.stderr)
            browser.close()
            raise
        browser.close()
    return payload


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(output: Path | str = DEFAULT_CACHE_PATH,
        tickers: list[str] | None = None,
        ttl_hours: float = CACHE_TTL_HOURS,
        force: bool = False,
        headless: bool = True,
        dump: bool = False,
        now: datetime | None = None) -> dict:
    """Fetch-or-cache entry point. NEVER raises — fail-open contract:
    any error keeps the prior cache with a staleness label."""
    output = Path(output)
    prior = load_cache(output)

    if not force and cache_is_fresh(prior, ttl_hours, now=now):
        return prior

    if not playwright_available():
        print("[moneyvest] playwright not installed — degraded cache-only "
              "mode (uv add playwright && uv run playwright install "
              "chromium)", file=sys.stderr)
        return _stale(prior, now)

    try:
        payload = fetch_live(tickers=tickers, headless=headless, dump=dump)
        if not payload.get("shopping_list"):
            # An empty scrape is a parse/auth failure, not a valid dataset.
            print("[moneyvest] fetch returned no shopping-list rows — "
                  "keeping prior cache", file=sys.stderr)
            return _stale(prior, now)
        write_cache(output, payload)
        return payload
    except Exception as e:
        print(f"[moneyvest] fetch failed ({type(e).__name__}: {e}) — "
              f"keeping prior cache", file=sys.stderr)
        return _stale(prior, now)


def _stale(prior: dict | None, now: datetime | None = None) -> dict:
    if not prior:
        return {"as_of": None, "shopping_list": [], "index": {},
                "m_scores": {}, "list_last_updated": None,
                "provenance": "unavailable"}
    out = dict(prior)
    out["provenance"] = "stale_cache"
    out["stale_hours"] = staleness_hours(prior, now=now)
    return out


def _load_env() -> None:
    """Load the repo-root .env so MONEYVEST_EMAIL/MONEYVEST_PASSWORD are
    visible via os.getenv (the fetcher may run standalone, outside the
    pipeline's own dotenv loading). Fail-open: missing python-dotenv or
    missing .env just means shell-exported vars are the only source."""
    try:
        from dotenv import load_dotenv
        repo_root = Path(__file__).resolve().parents[3]
        load_dotenv(repo_root / ".env")
    except Exception:
        pass


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="Moneyvest fetcher")
    ap.add_argument("--output", default=str(DEFAULT_CACHE_PATH))
    ap.add_argument("--tickers", nargs="*", default=[],
                    help="bounded set for per-stock M-Scores")
    ap.add_argument("--ttl-hours", type=float, default=CACHE_TTL_HOURS)
    ap.add_argument("--force", action="store_true",
                    help="ignore cache freshness")
    ap.add_argument("--probe", action="store_true",
                    help="force a live fetch and print a summary")
    ap.add_argument("--headed", action="store_true",
                    help="run the browser headed (first-run manual login: "
                         "log in once in the window, session persists)")
    ap.add_argument("--dump", action="store_true",
                    help="save the page.html/screenshot debug bundle on "
                         "SUCCESS too (parser iteration on the real DOM)")
    args = ap.parse_args()

    data = run(output=args.output, tickers=args.tickers,
               ttl_hours=args.ttl_hours, force=args.force or args.probe,
               headless=not args.headed, dump=args.dump)
    n = len(data.get("shopping_list") or [])
    idx = data.get("index") or {}
    sp = idx.get("sp500") or {}
    print(f"moneyvest: {n} shopping-list rows · "
          f"index S&P {sp.get('value')} {sp.get('label')} · "
          f"{len(data.get('m_scores') or {})} M-Scores · "
          f"list updated: {data.get('list_last_updated')} · "
          f"provenance: {data.get('provenance')}")
    if args.probe and data.get("provenance") != "live":
        print("PROBE FAILED — no live data this run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
