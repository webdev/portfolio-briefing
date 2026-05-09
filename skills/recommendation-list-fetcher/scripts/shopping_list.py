"""Shopping list data layer — fetch, cache, parse, resolve tickers.

Ported from wheelhouz/src/data/shopping_list.py with adjustments for standalone skill.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypedDict

import structlog
import yaml

log = structlog.get_logger()


class ConfigDict(TypedDict, total=False):
    """Type hint for config structure."""
    source: dict[str, Any]
    column_mapping: dict[str, str]
    ticker_resolution: dict[str, Any]
    normalization: dict[str, Any]
    freshness: dict[str, int]
    caching: dict[str, Any]


def load_config(config_path: Path) -> ConfigDict:
    """Load YAML config file."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    return cfg or {}


def validate_config(config_path: Path) -> ConfigDict:
    """Load and validate config file."""
    cfg = load_config(config_path)

    # Check required fields
    required = ["source", "column_mapping", "ticker_resolution", "normalization"]
    for field in required:
        if field not in cfg:
            raise ValueError(f"Config missing required field: {field}")

    # Validate source
    if "url" not in cfg["source"]:
        raise ValueError("Config source missing required field: url")

    return cfg


def test_sheet_access(url: str) -> tuple[bool, str]:
    """Test if sheet is accessible via gviz endpoint.

    Returns (success, message).
    """
    import httpx

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()

            # Check if response is CSV vs HTML (error)
            if resp.text.startswith("<"):
                return False, "Sheet returned HTML (likely not publicly shared)"

            # Try to parse first line
            reader = csv.reader(io.StringIO(resp.text))
            rows = list(reader)

            if not rows:
                return False, "Sheet is empty"

            print(f"✓ Sheet accessible, {len(rows)} rows total")
            if len(rows) > 1:
                print(f"  Header: {rows[0][:5]}...")
                print(f"  First data row: {rows[1][:5]}...")

            return True, f"✓ Sheet accessible ({len(rows)} rows)"

    except httpx.HTTPError as e:
        return False, f"✗ HTTP error: {e}"
    except Exception as e:
        return False, f"✗ Error: {e}"


def _parse_rating_tier(rating: str, config: ConfigDict) -> int:
    """Map rating string to numeric tier. Defaults to 1 (Hold) for unknowns."""
    rating_tiers = config["normalization"]["rating_tiers"]
    return rating_tiers.get(rating.strip(), 1)


def _tier_to_recommendation(tier: int, config: ConfigDict) -> str:
    """Map numeric tier to canonical recommendation enum."""
    mapping = config["normalization"].get("tier_to_recommendation", {
        5: "STRONG_BUY",
        4: "BUY",
        3: "BUY",
        2: "WEAK_BUY",
        1: "HOLD",
        0: "SELL",
    })
    return mapping.get(tier, "HOLD")


def _parse_price_target(raw: str) -> tuple[float, float] | float | None:
    """Parse price target string like '500-550' or '1,150-1,250' or '300'.

    Returns (low, high) as floats, or single float, or None if not parseable.
    """
    if not raw or not raw.strip():
        return None

    cleaned = raw.strip().replace(",", "")

    # Try range pattern
    match = re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", cleaned)
    if match:
        try:
            low = float(match.group(1))
            high = float(match.group(2))
            return (low, high)
        except ValueError:
            return None

    # Try single value
    match_single = re.match(r"^(\d+(?:\.\d+)?)$", cleaned)
    if match_single:
        try:
            return float(match_single.group(1))
        except ValueError:
            return None

    return None


def _parse_date(raw: str, config: ConfigDict) -> date | None:
    """Parse date string using configured formats."""
    if not raw or not raw.strip():
        return None

    formats = config["normalization"]["data_hygiene"]["accepted_date_formats"]

    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue

    return None


def resolve_ticker(name: str, config: ConfigDict) -> str | None:
    """Resolve company name to ticker symbol.

    Checks manual overrides first, then persistent cache, then yfinance.
    """
    stripped = name.strip()

    # Manual overrides
    overrides = config["ticker_resolution"].get("manual_overrides", {})
    if stripped in overrides:
        return overrides[stripped]

    # Persistent cache
    ticker_map = _load_ticker_map(config)
    if stripped in ticker_map:
        return ticker_map[stripped]

    # yfinance fallback
    if not config["ticker_resolution"].get("use_yfinance_fallback", True):
        return None

    try:
        import yfinance as yf
        search = yf.Search(stripped)
        if search.quotes:
            ticker = search.quotes[0].get("symbol")
            if ticker:
                _save_ticker_map(stripped, ticker, config)
                return ticker
    except Exception as e:
        log.warning("ticker_resolution_failed", name=stripped, error=str(e))

    return None


def _load_ticker_map(config: ConfigDict) -> dict[str, str]:
    """Load the persistent name-to-ticker cache."""
    cache_path = Path(config["ticker_resolution"]["cache_path"])
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_ticker_map(name: str, ticker: str, config: ConfigDict) -> None:
    """Save a name-to-ticker mapping to the persistent cache."""
    cache_path = Path(config["ticker_resolution"]["cache_path"])
    ticker_map = _load_ticker_map(config)
    ticker_map[name] = ticker
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(ticker_map, indent=2))


def _parse_csv_rows(
    rows: list[list[str]],
    config: ConfigDict,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse CSV data rows into recommendation dicts.

    Returns (entries, skipped_reasons).
    """
    if today is None:
        today = date.today()

    max_age_days = config["freshness"]["max_age_days"]
    warn_age_days = config["freshness"]["warn_age_days"]
    stale_cutoff = today - timedelta(days=max_age_days)
    warn_cutoff = today - timedelta(days=warn_age_days)

    col_map = config["column_mapping"]
    entries: list[dict[str, Any]] = []
    skipped_reasons: list[str] = []
    row_num = config["source"]["data_starts_at_row"]

    for row in rows:
        if len(row) < 2:
            continue

        # Extract columns by letter (A=0, B=1, etc.)
        def get_col(letter: str) -> str:
            idx = ord(letter) - ord('A')
            return row[idx] if idx < len(row) else ""

        name = get_col(col_map["name"]).strip()
        rating = get_col(col_map["recommendation"]).strip()
        date_str = get_col(col_map.get("date_updated", "C")).strip()
        pt_2026_str = get_col(col_map.get("price_target_2026", "D")).strip()
        pt_2027_str = get_col(col_map.get("price_target_2027", "F")).strip()

        # Skip header and empty rows
        if not name or not rating:
            row_num += 1
            continue

        if name == "Name" or name.startswith("*"):
            row_num += 1
            continue

        # Skip #REF! errors if configured
        if config["normalization"]["data_hygiene"].get("drop_ref_errors", True):
            if "#REF!" in rating or "#REF!" in pt_2026_str or "#REF!" in pt_2027_str:
                skipped_reasons.append("#REF! error")
                row_num += 1
                continue

        # Resolve ticker
        ticker = resolve_ticker(name, config)
        if not ticker:
            skipped_reasons.append(f"no_ticker: {name}")
            row_num += 1
            continue

        # Parse rating
        rating_tier = _parse_rating_tier(rating, config)
        recommendation = _tier_to_recommendation(rating_tier, config)

        # Parse date
        date_updated = _parse_date(date_str, config)
        if date_updated:
            age_days = (today - date_updated).days
        else:
            age_days = 0
            date_updated = today

        aging = age_days >= warn_age_days
        stale = date_updated < stale_cutoff

        # Skip if too old
        if stale:
            skipped_reasons.append(f"archived: {name} ({age_days}d old)")
            row_num += 1
            continue

        # Parse price targets
        pt_2026 = _parse_price_target(pt_2026_str)
        pt_2027 = _parse_price_target(pt_2027_str)

        entries.append({
            "ticker": ticker,
            "name": name,
            "recommendation": recommendation,
            "raw_recommendation": rating,
            "rating_tier": rating_tier,
            "date_updated": date_updated.isoformat() if date_updated else None,
            "age_days": age_days,
            "aging": aging,
            "price_target_2026": pt_2026,
            "price_target_2027": pt_2027,
            "row_number": row_num,
        })

        row_num += 1

    return entries, skipped_reasons


def _cache_is_fresh(cache_path: Path, ttl_minutes: int = 60) -> bool:
    """Check if cache file exists and is within TTL."""
    if not cache_path.exists():
        return False

    try:
        meta_path = cache_path.with_suffix(".meta")
        if not meta_path.exists():
            return False

        meta = json.loads(meta_path.read_text())
        ts = datetime.fromisoformat(meta["cached_at"])
        return datetime.now(timezone.utc) - ts < timedelta(minutes=ttl_minutes)
    except (ValueError, OSError, json.JSONDecodeError):
        return False


def _compute_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from recommendations."""
    summary: dict[str, Any] = {
        "buy_count": 0,
        "sell_count": 0,
        "hold_count": 0,
        "aging_count": 0,
    }

    for entry in entries:
        rec = entry["recommendation"]
        if rec in ("BUY", "STRONG_BUY", "WEAK_BUY"):
            summary["buy_count"] += 1
        elif rec == "SELL":
            summary["sell_count"] += 1
        else:
            summary["hold_count"] += 1

        if entry["aging"]:
            summary["aging_count"] += 1

    return summary


def fetch_recommendations(
    config: ConfigDict,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch and parse recommendations from Google Sheet.

    Returns structured output JSON.
    """
    import httpx

    url = config["source"]["url"]
    sheet_id = config["source"]["sheet_id"]
    cache_path = Path(config["caching"]["cache_path"])
    cache_ttl = config["caching"]["cache_for_minutes"]
    today = date.today()

    # Check cache
    cached_result = None
    cache_age_minutes = None

    if not force_refresh and _cache_is_fresh(cache_path, cache_ttl):
        try:
            cached_result = json.loads(cache_path.read_text())
            meta_path = cache_path.with_suffix(".meta")
            meta = json.loads(meta_path.read_text())
            ts = datetime.fromisoformat(meta["cached_at"])
            cache_age_minutes = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
            log.info("fetch_from_cache", age_minutes=cache_age_minutes)
            cached_result["source"]["cached"] = True
            cached_result["source"]["cache_age_minutes"] = cache_age_minutes
            return cached_result
        except Exception as e:
            log.warning("cache_load_failed", error=str(e))

    # Fetch from sheet
    csv_text = None
    stale = False

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            csv_text = resp.text
            log.info("sheet_fetched", bytes=len(csv_text))
    except Exception as e:
        log.warning("sheet_fetch_failed", error=str(e))

        # Fall back to stale cache if available
        if cached_result:
            log.info("using_stale_cache")
            cached_result["source"]["stale"] = True
            cached_result["source"]["cached"] = True
            return cached_result

        # No cache, raise
        raise RuntimeError(f"Failed to fetch sheet and no cache available: {e}")

    # Parse CSV
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)

    # Skip header if present
    if rows and rows[0] and rows[0][0].strip() == "Name":
        rows = rows[1:]

    # Parse entries
    entries, skipped_reasons = _parse_csv_rows(rows, config, today)

    # Compute summary
    summary = _compute_summary(entries)

    # Count skipped by reason
    skip_counts: dict[str, int] = {}
    for reason in skipped_reasons:
        reason_type = reason.split(":")[0]
        skip_counts[reason_type] = skip_counts.get(reason_type, 0) + 1

    skip_reason_strs = [f"{k}: {v}" for k, v in skip_counts.items()]

    # Build output
    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "sheet_id": sheet_id,
            "row_count_total": len(rows),
            "row_count_parsed": len(entries),
            "row_count_skipped": len(rows) - len(entries),
            "skipped_reasons": skip_reason_strs,
            "cached": False,
            "stale": stale,
        },
        "summary": summary,
        "recommendations": entries,
    }

    # Write cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(entries, indent=2, default=str))

    meta_path = cache_path.with_suffix(".meta")
    meta_path.write_text(json.dumps({
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }))

    log.info("recommendations_fetched", count=len(entries))

    return result
