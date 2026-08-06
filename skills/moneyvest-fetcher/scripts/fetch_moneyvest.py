#!/usr/bin/env python3
"""Moneyvest fetcher — shopping list + sentiment index + M-Scores (task #46).

Scrapes the RENDERED DOM of moneyvest.com (React SPA whose data rides
Firebase/WebSockets — network-response scraping is a dead end) via
Playwright, parses with BeautifulSoup, and writes a 20h-TTL cache the
briefing pipeline consumes as Step 1.9.

Surfaces fetched:
  - /shopping-list   — the core dataset. Table sections (MAG7, "Long Term -
                       High Quality", ... + an ETFs tab). Columns: Stock ·
                       Price · Fair Value · Delta% · Light Buy · Heavy Buy ·
                       No Brainer Buy · No Brainer 2027 PE. The page's
                       "Last updated on <date>" line is captured.
  - /moneyvest-index — sentiment gauge value (e.g. 3.82) + label
                       (PANIC/FEAR/UNCERTAINTY/NEUTRAL/OPTIMISTIC/GREED/
                       EUPHORIA) for the S&P 500 and NASDAQ 100 tabs.
  - /stock/<T>/overview — per-stock "M-Score · 4.05/5.00" badge, fetched
                       ONLY for a bounded ticker set the caller passes
                       (held + candidate names), skipping gracefully
                       per-ticker.

Auth: Playwright storage-state persisted at
``~/.config/portfolio-briefing/moneyvest_state.json`` (0600). When the
state is missing/expired, a login flow runs using MONEYVEST_EMAIL /
MONEYVEST_PASSWORD from the environment (.env — loaded by the pipeline;
this module only ever calls os.getenv and NEVER logs credentials).

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
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - bs4 is a hard repo dep
    BeautifulSoup = None

BASE_URL = "https://moneyvest.com"
DEFAULT_STATE_PATH = Path.home() / ".config" / "portfolio-briefing" / "moneyvest_state.json"
DEFAULT_CACHE_PATH = (Path(__file__).resolve().parents[3]
                      / "state" / "cache" / "moneyvest.json")
CACHE_TTL_HOURS = 20.0  # daily refresh with slack for run-time drift

INDEX_LABELS = ("PANIC", "FEAR", "UNCERTAINTY", "NEUTRAL",
                "OPTIMISTIC", "GREED", "EUPHORIA")

# SPA + heavy ad scripts: networkidle never settles — wait on table rows.
NAV_TIMEOUT_MS = 60_000
SELECTOR_TIMEOUT_MS = 45_000


# --------------------------------------------------------------------------
# Parsing (pure functions — unit-tested on constructed DOM fixtures)
# --------------------------------------------------------------------------

_MONEY_RE = re.compile(r"(-?)\$?\s*([0-9][0-9,]*\.?[0-9]*)")
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_LAST_UPDATED_RE = re.compile(r"Last updated on\s+([^.<\n]{1,60})",
                              re.IGNORECASE)
_MSCORE_RE = re.compile(r"M[\s-]?Score\s*[·:\-]?\s*([0-9]\.[0-9]{1,2})\s*/\s*5",
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
    """First 1-5-char ALL-CAPS token in the Stock cell ('NVDA — Nvidia')."""
    for tok in _TICKER_RE.findall(cell_text or ""):
        return tok
    return None


def _map_columns(headers: list[str]) -> dict:
    """Fuzzy header → field mapping (site copy shifts; keys must not)."""
    mapping: dict[int, str] = {}
    for i, h in enumerate(headers):
        hl = (h or "").strip().lower()
        if not hl:
            continue
        if "stock" in hl or "ticker" in hl or "company" in hl:
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
        elif "pe" in hl or "p/e" in hl:
            mapping[i] = "pe_2027"
    return mapping


def parse_shopping_list(html: str, section_hint: str = "") -> tuple[list[dict], str | None]:
    """Parse the rendered /shopping-list DOM → (rows, list_last_updated).

    Tolerant of section headings living in h1-h4 tags OR full-width header
    rows above each table block. Rows without a recognizable ticker are
    skipped, never fabricated (hard rule #19).
    """
    if BeautifulSoup is None:
        return [], None
    soup = BeautifulSoup(html, "html.parser")
    rows_out: list[dict] = []

    m = _LAST_UPDATED_RE.search(soup.get_text(" ", strip=True))
    last_updated = m.group(1).strip().rstrip(".") if m else None

    for table in soup.find_all("table"):
        # Section: nearest previous heading tag.
        section = section_hint or "unknown"
        heading = table.find_previous(["h1", "h2", "h3", "h4"])
        if heading and heading.get_text(strip=True):
            section = heading.get_text(strip=True)

        header_cells = []
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
        if "ticker" not in colmap.values():
            continue  # not a shopping-list table

        body_rows = table.find_all("tr")
        for tr in body_rows:
            cells = tr.find_all("td")
            if not cells:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
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
            if rec.get("ticker"):
                for k in ("price", "fair_value", "light_buy",
                          "heavy_buy", "no_brainer", "pe_2027"):
                    rec.setdefault(k, None)
                rows_out.append(rec)
    return rows_out, last_updated


def parse_index_text(text: str) -> dict | None:
    """Rendered gauge text → {"value": 3.82, "label": "OPTIMISTIC"}."""
    if not text:
        return None
    upper = text.upper()
    label = None
    for candidate in INDEX_LABELS:
        if candidate in upper:
            label = candidate
            break
    m = re.search(r"\b([0-9]\.[0-9]{1,2})\b", text)
    value = float(m.group(1)) if m else None
    if value is None and label is None:
        return None
    return {"value": value, "label": label}


def parse_m_score(text: str) -> float | None:
    """'M-Score · 4.05/5.00' badge text → 4.05."""
    m = _MSCORE_RE.search(text or "")
    return float(m.group(1)) if m else None


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
            as_of = as_of.replace(tzinfo=None)
    except (ValueError, TypeError):
        return False
    now = now or datetime.utcnow()
    return (now - as_of) <= timedelta(hours=ttl_hours)


def staleness_hours(cache: dict | None, now: datetime | None = None) -> float | None:
    if not cache or not cache.get("as_of"):
        return None
    try:
        as_of = datetime.fromisoformat(str(cache["as_of"]).replace("Z", "+00:00"))
        if as_of.tzinfo:
            as_of = as_of.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
    now = now or datetime.utcnow()
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
# Playwright fetch (network side — NOT exercised by tests)
# --------------------------------------------------------------------------

def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_login(page, context, state_path: Path) -> bool:
    """If the shopping list redirected to a login wall, run the login flow.

    Credentials come from MONEYVEST_EMAIL / MONEYVEST_PASSWORD env vars
    (never logged). Returns True when authenticated content is reachable.
    """
    if "login" not in (page.url or "").lower() \
            and not page.locator("input[type=password]").count():
        return True
    email = os.getenv("MONEYVEST_EMAIL")
    password = os.getenv("MONEYVEST_PASSWORD")
    if not email or not password:
        print("[moneyvest] login required but MONEYVEST_EMAIL/"
              "MONEYVEST_PASSWORD not set — cache-only mode", file=sys.stderr)
        return False
    try:
        page.goto(f"{BASE_URL}/login", timeout=NAV_TIMEOUT_MS)
        page.fill("input[type=email]", email)
        page.fill("input[type=password]", password)
        page.click("button[type=submit]")
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        os.chmod(state_path, 0o600)
        return True
    except Exception as e:
        print(f"[moneyvest] login flow failed: {type(e).__name__}",
              file=sys.stderr)  # never echo credentials or full URLs
        return False


def fetch_live(tickers: list[str] | None = None,
               state_path: Path = DEFAULT_STATE_PATH,
               headless: bool = True) -> dict:
    """Fetch all three surfaces via Playwright. Raises on total failure —
    the caller (run) turns that into a stale-cache fallback."""
    from playwright.sync_api import sync_playwright

    payload: dict = {
        "as_of": datetime.utcnow().isoformat() + "Z",
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

        # 1) Shopping list — THE core dataset.
        page.goto(f"{BASE_URL}/shopping-list", timeout=NAV_TIMEOUT_MS)
        if not _ensure_login(page, context, state_path):
            browser.close()
            raise RuntimeError("moneyvest auth unavailable")
        page.goto(f"{BASE_URL}/shopping-list", timeout=NAV_TIMEOUT_MS)
        # SPA: wait for actual table rows, NOT networkidle (never settles).
        page.wait_for_selector("table tr td", timeout=SELECTOR_TIMEOUT_MS)
        rows, last_updated = parse_shopping_list(page.content())
        payload["shopping_list"] = rows
        payload["list_last_updated"] = last_updated
        # ETFs tab (best-effort — layout may fold it into the same DOM).
        try:
            etf_tab = page.get_by_role("tab", name=re.compile("ETF", re.I))
            if etf_tab.count():
                etf_tab.first.click()
                page.wait_for_timeout(2500)
                etf_rows, _ = parse_shopping_list(page.content(),
                                                  section_hint="ETFs")
                known = {r["ticker"] for r in rows}
                payload["shopping_list"].extend(
                    r for r in etf_rows if r["ticker"] not in known)
        except Exception:
            pass

        # 2) Sentiment index — S&P 500 + NASDAQ 100 tabs.
        try:
            page.goto(f"{BASE_URL}/moneyvest-index", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(4000)
            sp = parse_index_text(page.inner_text("body"))
            if sp:
                payload["index"]["sp500"] = sp
            ndx_tab = page.get_by_role(
                "tab", name=re.compile("NASDAQ", re.I))
            if ndx_tab.count():
                ndx_tab.first.click()
                page.wait_for_timeout(2500)
                ndx = parse_index_text(page.inner_text("body"))
                if ndx:
                    payload["index"]["ndx"] = ndx
        except Exception as e:
            print(f"[moneyvest] index fetch failed: {type(e).__name__}",
                  file=sys.stderr)

        # 3) Per-stock M-Scores — bounded set, skip gracefully per ticker.
        for t in sorted({str(t).upper() for t in (tickers or []) if t}):
            try:
                page.goto(f"{BASE_URL}/stock/{t}/overview",
                          timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(2500)
                score = parse_m_score(page.inner_text("body"))
                if score is not None:
                    payload["m_scores"][t] = score
            except Exception:
                continue

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
        payload = fetch_live(tickers=tickers, headless=headless)
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


def main() -> int:
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
                    help="run the browser headed (first-run login debug)")
    args = ap.parse_args()

    data = run(output=args.output, tickers=args.tickers,
               ttl_hours=args.ttl_hours, force=args.force or args.probe,
               headless=not args.headed)
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
