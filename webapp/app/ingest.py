"""DuckDB ingest layer — views + materialized projections over the
existing briefing JSON snapshots.

Design (per architecture doc §3):
  - JSON IS the source of truth; we never migrate, never reshape.
  - DuckDB views with `union_by_name=true` unify N versions of the
    briefing schema into one queryable superset.
  - Three small materialized tables drive the dashboards: portfolio
    time-series, positions time-series, Parkev rating history.
  - Full re-materialize on every connect (the tables total < 1 MB).

Failure mode: if no JSON files exist, the views still create (empty)
and every endpoint returns "no data yet" — never raises at startup.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from .config import briefings_delivery, duckdb_path, snapshots_root


@dataclass(frozen=True)
class IngestStatus:
    """Snapshot of the last ingest run, surfaced in the footer."""

    snapshot_count: int            # rows in portfolio_timeseries
    expected_count: int            # number of valid briefing_*.json found
    last_ingest_at: datetime       # when _refresh_projections finished
    delivery_dir: str
    snapshots_dir: str


# Module-level cache of the last ingest. Updated by connect().
_LAST_STATUS: IngestStatus | None = None


def get_last_status() -> IngestStatus | None:
    """Return the most recent IngestStatus or None if no ingest has run."""
    return _LAST_STATUS


def _valid_briefing_json_count() -> int:
    """Count of briefing_<DATE>.json files (excluding .DRAFT.json)."""
    delivery = briefings_delivery()
    if not delivery.exists():
        return 0
    files = list(delivery.glob("briefing_*.json"))
    return len([f for f in files if not f.name.endswith(".DRAFT.json")])


def _delivery_glob() -> str:
    """Posix glob string for read_json_auto. Forward slashes always."""
    return str(briefings_delivery() / "briefing_*.json").replace("\\", "/")


def _snapshot_positions_glob() -> str:
    return str(snapshots_root() / "*" / "positions.json").replace("\\", "/")


def _snapshot_balance_glob() -> str:
    return str(snapshots_root() / "*" / "balance.json").replace("\\", "/")


def _snapshot_recs_glob() -> str:
    return str(snapshots_root() / "*" / "recommendations_list.json").replace("\\", "/")


def _snapshot_regime_glob() -> str:
    return str(snapshots_root() / "*" / "regime.json").replace("\\", "/")


def _snapshot_technicals_glob() -> str:
    return str(snapshots_root() / "*" / "technicals.json").replace("\\", "/")


def connect() -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection. Idempotent and cheap.

    M2 change (issue #1): this no longer re-materializes the projections
    on every call. Materialization happens explicitly via
    ``refresh_projections()`` on startup and after each ``POST /refresh``.
    Per-request connects are now just a `duckdb.connect()` + view check.

    Tests that depend on fresh data on every connect still work because
    they call ``connect()`` once during fixture setup, which goes through
    the "first call" branch that materializes.
    """
    global _PROJECTIONS_READY
    db_path = duckdb_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Materialization (first call only): open a write connection via the
    # resilient helper, which transparently nukes + rebuilds a corrupt file
    # (or falls back to :memory: if the data dir is non-writable, e.g. CI).
    global _INMEM_MATERIALIZED, _LAST_MATERIALIZED_MTIME
    # First-time OR auto-refresh triggered by fresh briefings on disk
    needs_materialize = (not _PROJECTIONS_READY) or _should_auto_refresh()
    if needs_materialize:
        write_conn = _connect_write_resilient(db_path)
        try:
            _create_views(write_conn)
            _refresh_projections(write_conn)
            # Also refresh briefing_mentions so search/drill-downs see
            # the latest data. Cheap enough to run on every re-materialize.
            try:
                _refresh_briefing_mentions(write_conn)
            except Exception:
                pass  # non-fatal; tables just aren't updated
        finally:
            # If we ended up on the shared in-memory connection, DON'T
            # close it — subsequent requests reuse the same handle via
            # cursors. Otherwise close the disk-backed write connection.
            if write_conn is _INMEM_CONN:
                _INMEM_MATERIALIZED = True
            else:
                write_conn.close()
        _PROJECTIONS_READY = True
        _LAST_MATERIALIZED_MTIME = _newest_briefing_mtime()

    # Per-request connection. We deliberately use the same resilient open
    # logic — read-only mode would avoid touching the WAL but ALSO fails
    # on a corrupt file (and the user's file may have been corrupted
    # mid-session by an external process). The resilient helper returns
    # the shared in-memory DB as last resort so the route handler never
    # sees a raw IOException / SerializationException.
    conn = _connect_write_resilient(db_path)
    # In-memory case: hand out a CURSOR that shares the parent's tables
    # but is safe for the route handler to .close() at end of request.
    if conn is _INMEM_CONN:
        return conn.cursor()
    try:
        _create_views(conn)
    except Exception:
        conn.close()
        raise
    return conn


# Set to True once _refresh_projections has run successfully at least once
# in this process. refresh_projections() can be called explicitly to force
# a re-materialize (used by POST /refresh and the startup hook).
_PROJECTIONS_READY: bool = False


_CORRUPTION_MARKERS = (
    "serialization",
    "field id mismatch",
    "deserialize",
    "io error",
    "not a valid duckdb database",
    "could not remove",
    "corrupted",
    "wal",
)

# When the data dir isn't writable (CI / sandbox / locked file the OS won't
# let us delete), we fall back to a single process-wide in-memory DB so the
# app keeps working. The connection is kept alive at module scope because
# :memory: connections are isolated — opening a fresh :memory: per request
# would lose every materialized table.
_INMEM_CONN: duckdb.DuckDBPyConnection | None = None
_INMEM_MATERIALIZED: bool = False

# Mtime sentinel — tracks the newest file across delivery + snapshots dirs
# at the time of the last materialization. If a fresh briefing lands on
# disk (from a CLI pipeline run outside the webapp), the next connect()
# detects the newer mtime and auto re-materializes. Cheap: 1-2 stat()
# calls per request.
_LAST_MATERIALIZED_MTIME: float = 0.0
# Auto-refresh throttle — never check more than once per N seconds so
# rapid page loads don't stat the filesystem repeatedly.
_AUTO_REFRESH_MIN_INTERVAL_SEC: float = 2.0
_LAST_MTIME_CHECK: float = 0.0


def briefing_checksum(date: str, kind: str = "json") -> str | None:
    """Compute a short SHA-256 prefix of the briefing file for `date`.

    Used to display a "freshness fingerprint" on the UI + in the Telegram
    delivery message so the user can visually confirm the UI is showing
    the SAME briefing that was just generated.

    Args:
        date: YYYY-MM-DD
        kind: "json" (default) or "md" — which artifact to fingerprint

    Returns:
        First 8 hex chars of SHA-256, or None when the file is missing.

    Cheap: hashlib is stdlib, briefings are ~100KB, sub-ms compute.
    """
    import hashlib
    if not date:
        return None
    ext = "md" if kind == "md" else "json"
    p = briefings_delivery() / f"briefing_{date}.{ext}"
    if not p.exists():
        return None
    try:
        content = p.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()[:8]


def _newest_briefing_mtime() -> float:
    """Fastest possible check for "did a new briefing land?": stat the
    delivery + snapshots roots (directory mtime updates when any file
    is added/removed/renamed inside). Returns the max mtime, or 0 if
    neither dir exists.
    """
    import os as _os
    mtimes = []
    for d in (briefings_delivery(), snapshots_root()):
        try:
            mtimes.append(_os.stat(d).st_mtime)
        except (OSError, FileNotFoundError):
            pass
    return max(mtimes) if mtimes else 0.0


def _should_auto_refresh() -> bool:
    """True when a new briefing has landed on disk since last materialization.

    Throttled to at most one filesystem check per
    `_AUTO_REFRESH_MIN_INTERVAL_SEC` so rapid page loads don't hammer
    the disk. Returns False when the mtime hasn't advanced past the
    sentinel (common case — nothing changed).
    """
    global _LAST_MTIME_CHECK
    now = time.time()
    if now - _LAST_MTIME_CHECK < _AUTO_REFRESH_MIN_INTERVAL_SEC:
        return False
    _LAST_MTIME_CHECK = now
    current = _newest_briefing_mtime()
    return current > _LAST_MATERIALIZED_MTIME


def _connect_write_resilient(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a WRITE DuckDB connection, nuking + rebuilding the file if it
    fails to open due to corruption (schema-version drift, stale WAL,
    macOS permission races on briefings.duckdb.wal).

    This is safe because the DuckDB file is purely a derived materialization
    of the JSON snapshots — rebuilding from source takes seconds and loses
    nothing canonical.

    Fall-back order when the file is unusable:
      1. delete + recreate on disk (most common — schema drift)
      2. rename + recreate on disk (when delete is blocked by permissions)
      3. in-memory DB (last resort — keeps the app running in CI / sandboxes
         where the data dir isn't writable)

    STICKINESS: once we've committed to _INMEM_CONN in a previous call,
    ALWAYS return it. Switching backends mid-process would leave tables
    on one connection invisible to the other, causing intermittent
    "Table does not exist" errors (task #48).
    """
    global _INMEM_CONN
    # Sticky in-memory: if we're already in the in-memory fallback for
    # this process, don't try disk again — even if disk has become
    # accessible in the meantime. The tables are on _INMEM_CONN, not disk.
    if _INMEM_CONN is not None:
        return _INMEM_CONN

    try:
        return duckdb.connect(str(db_path))
    except Exception as e:  # noqa: BLE001 — broad on purpose, classified below
        msg = str(e).lower()
        if not any(s in msg for s in _CORRUPTION_MARKERS):
            raise

    # Step 1 + 2: try to remove the bad files (and any sidecars).
    sidecars = [Path(str(db_path) + ext) for ext in ("", ".wal", ".tmp")]
    for p in sidecars:
        try:
            if p.exists():
                p.unlink()
        except (FileNotFoundError, PermissionError):
            pass

    # If unlink failed and the file still exists, rename it out of the way
    # so duckdb.connect creates a fresh one alongside.
    if db_path.exists():
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        quarantine = db_path.with_suffix(f".corrupt.{ts}")
        try:
            db_path.rename(quarantine)
        except (PermissionError, OSError):
            pass

    # Retry on disk
    try:
        return duckdb.connect(str(db_path))
    except Exception:
        # Step 3: last resort — process-wide in-memory DB. Cached at module
        # scope so subsequent calls reuse the same connection (a per-call
        # :memory: would lose every table created during materialization).
        # (global declaration is at the top of this function for sticky
        # in-memory support — task #48).
        if _INMEM_CONN is None:
            _INMEM_CONN = duckdb.connect(":memory:")
        return _INMEM_CONN


def refresh_projections() -> None:
    """Force a fresh materialization of all projections + briefing_mentions.

    Called from:
      - app startup (lifespan)
      - after a POST /refresh subprocess finishes
      - tests' fixture setup (indirectly, via connect() on first call)
    """
    global _PROJECTIONS_READY, _INMEM_MATERIALIZED, _LAST_MATERIALIZED_MTIME
    db_path = duckdb_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect_write_resilient(db_path)
    try:
        _create_views(conn)
        _refresh_projections(conn)
        _refresh_briefing_mentions(conn)
        _PROJECTIONS_READY = True
        _LAST_MATERIALIZED_MTIME = _newest_briefing_mtime()
    finally:
        # If we ended up on the shared in-memory connection, do NOT close
        # it — subsequent per-request connect() calls reuse this handle
        # via .cursor() and closing the parent would lose every table.
        if conn is _INMEM_CONN:
            _INMEM_MATERIALIZED = True
        else:
            conn.close()


def _create_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the raw JSON-glob views. union_by_name handles schema drift.

    Each `CREATE OR REPLACE VIEW` is wrapped in a try/except so that a
    missing directory (e.g. a brand-new install with no snapshots) leaves
    an EMPTY view rather than crashing startup.
    """
    delivery = _delivery_glob()
    pos_glob = _snapshot_positions_glob()
    bal_glob = _snapshot_balance_glob()
    recs_glob = _snapshot_recs_glob()
    regime_glob = _snapshot_regime_glob()
    tech_glob = _snapshot_technicals_glob()

    # ─── briefings_raw: one row per briefing_<DATE>.json ─────────────
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW briefings_raw AS
            SELECT
                regexp_extract(filename, 'briefing_(\\d{{4}}-\\d{{2}}-\\d{{2}})\\.json$', 1) AS date,
                *
            FROM read_json_auto(
                '{delivery}',
                filename = true,
                union_by_name = true,
                maximum_object_size = 16777216,
                ignore_errors = true
            )
            WHERE filename NOT LIKE '%.DRAFT.json'
        """)
    except duckdb.Error:
        # No matching files yet — create an empty view with the canonical schema
        conn.execute("""
            CREATE OR REPLACE VIEW briefings_raw AS
            SELECT
                NULL::VARCHAR AS date,
                NULL::DOUBLE AS nlv,
                NULL::DOUBLE AS cash,
                NULL::VARCHAR AS regime,
                NULL::VARCHAR AS snapshot_dir,
                NULL::VARCHAR AS filename
            WHERE 1=0
        """)

    # ─── positions_raw: per-day position rows ─────────────────────────
    # positions.json is a top-level JSON ARRAY (one element per position).
    # `read_json_auto(..., format='array')` returns one DuckDB row per
    # element, with each element's keys becoming columns. We add the
    # `filename` column and derive `date` from it.
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW positions_raw AS
            SELECT
                regexp_extract(filename, '/(\\d{{4}}-\\d{{2}}-\\d{{2}})/[^/]+\\.json$', 1) AS date,
                * EXCLUDE (filename)
            FROM read_json_auto(
                '{pos_glob}',
                filename = true,
                union_by_name = true,
                format = 'array',
                maximum_object_size = 16777216,
                ignore_errors = true
            )
        """)
    except (duckdb.Error, Exception):
        conn.execute("""
            CREATE OR REPLACE VIEW positions_raw AS
            SELECT NULL::VARCHAR AS date, NULL::VARCHAR AS symbol,
                   NULL::VARCHAR AS assetType, NULL::DOUBLE AS qty,
                   NULL::DOUBLE AS price, NULL::DOUBLE AS marketValue,
                   NULL::VARCHAR AS underlying, NULL::VARCHAR AS type,
                   NULL::DOUBLE AS strike, NULL::VARCHAR AS expiration,
                   NULL::VARCHAR AS accountDesc
            WHERE 1=0
        """)

    # ─── balance_raw: per-day balance.json ───────────────────────────
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW balance_raw AS
            SELECT
                regexp_extract(filename, '/(\\d{{4}}-\\d{{2}}-\\d{{2}})/[^/]+\\.json$', 1) AS date,
                *
            FROM read_json_auto(
                '{bal_glob}',
                filename = true,
                union_by_name = true,
                maximum_object_size = 16777216,
                ignore_errors = true
            )
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE VIEW balance_raw AS
            SELECT NULL::VARCHAR AS date, NULL::DOUBLE AS accountValue,
                   NULL::DOUBLE AS cash, NULL::DOUBLE AS longMarketValue
            WHERE 1=0
        """)

    # ─── recs_raw: per-day recommendations_list.json ────────────────
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW recs_raw AS
            SELECT
                regexp_extract(filename, '/(\\d{{4}}-\\d{{2}}-\\d{{2}})/[^/]+\\.json$', 1) AS date,
                unnest(recommendations) AS rec
            FROM read_json_auto(
                '{recs_glob}',
                filename = true,
                union_by_name = true,
                maximum_object_size = 16777216,
                ignore_errors = true
            )
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE VIEW recs_raw AS
            SELECT NULL::VARCHAR AS date, NULL AS rec
            WHERE 1=0
        """)

    # ─── regime_raw: per-day regime.json ────────────────────────────
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW regime_raw AS
            SELECT
                regexp_extract(filename, '/(\\d{{4}}-\\d{{2}}-\\d{{2}})/[^/]+\\.json$', 1) AS date,
                *
            FROM read_json_auto(
                '{regime_glob}',
                filename = true,
                union_by_name = true,
                maximum_object_size = 16777216,
                ignore_errors = true
            )
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE VIEW regime_raw AS
            SELECT NULL::VARCHAR AS date, NULL::VARCHAR AS regime
            WHERE 1=0
        """)

    # ─── technicals_raw: per-day technicals.json ────────────────────
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW technicals_raw AS
            SELECT
                regexp_extract(filename, '/(\\d{{4}}-\\d{{2}}-\\d{{2}})/[^/]+\\.json$', 1) AS date,
                *
            FROM read_json_auto(
                '{tech_glob}',
                filename = true,
                union_by_name = true,
                maximum_object_size = 16777216,
                ignore_errors = true
            )
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE VIEW technicals_raw AS
            SELECT NULL::VARCHAR AS date
            WHERE 1=0
        """)


def _refresh_projections(conn: duckdb.DuckDBPyConnection) -> None:
    """Materialize the three small projections used by the dashboards.

    Full re-materialize is cheaper than incremental for <1 MB of data.
    Updates module-level _LAST_STATUS so the footer can render.
    """
    global _LAST_STATUS

    # ── portfolio_timeseries: one row per date (NLV, cash, regime) ──
    # Use COALESCE-style selects via try/except — a brand-new install with
    # zero snapshots needs to still produce an empty table cleanly.
    try:
        conn.execute("""
            CREATE OR REPLACE TABLE portfolio_timeseries AS
            SELECT
                date::DATE AS date,
                nlv,
                cash,
                CASE WHEN nlv > 0 THEN cash / nlv ELSE NULL END AS cash_pct,
                regime
            FROM briefings_raw
            WHERE date IS NOT NULL AND date != ''
            ORDER BY date
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE TABLE portfolio_timeseries (
                date DATE, nlv DOUBLE, cash DOUBLE,
                cash_pct DOUBLE, regime VARCHAR
            )
        """)

    # ── positions_timeseries: one row per (date, position) ──
    # positions_raw is flat (one row per array element with columns from
    # the JSON keys). We project only the canonical fields here; missing
    # ones (e.g. `strike` on an equity row) come through as NULL thanks
    # to union_by_name.
    try:
        conn.execute("""
            CREATE OR REPLACE TABLE positions_timeseries AS
            SELECT
                date::DATE AS date,
                symbol,
                assetType,
                COALESCE(underlying, symbol) AS underlying,
                qty::DOUBLE AS qty,
                price::DOUBLE AS price,
                marketValue::DOUBLE AS market_value,
                type AS opt_type,
                strike::DOUBLE AS strike,
                expiration::VARCHAR AS expiration,
                accountDesc
            FROM positions_raw
            WHERE date IS NOT NULL AND date != ''
        """)
    except (duckdb.Error, Exception):
        conn.execute("""
            CREATE OR REPLACE TABLE positions_timeseries (
                date DATE, symbol VARCHAR, assetType VARCHAR,
                underlying VARCHAR, qty DOUBLE, price DOUBLE,
                market_value DOUBLE, opt_type VARCHAR, strike DOUBLE,
                expiration VARCHAR, accountDesc VARCHAR
            )
        """)

    # ── parkev_history: per (date, ticker) rating + conviction + age ──
    try:
        conn.execute("""
            CREATE OR REPLACE TABLE parkev_history AS
            SELECT
                date::DATE AS date,
                rec.ticker AS ticker,
                rec.recommendation AS recommendation,
                rec.raw_recommendation AS raw_recommendation,
                rec.rating_tier::INTEGER AS rating_tier,
                rec.conviction AS conviction,
                rec.conviction_score::INTEGER AS conviction_score,
                rec.age_days::INTEGER AS age_days,
                rec.aging::BOOLEAN AS aging,
                rec.date_updated AS date_updated
            FROM recs_raw
            WHERE date IS NOT NULL AND date != ''
        """)
    except duckdb.Error:
        conn.execute("""
            CREATE OR REPLACE TABLE parkev_history (
                date DATE, ticker VARCHAR, recommendation VARCHAR,
                raw_recommendation VARCHAR, rating_tier INTEGER,
                conviction VARCHAR, conviction_score INTEGER,
                age_days INTEGER, aging BOOLEAN, date_updated VARCHAR
            )
        """)

    # M2 issue #2 — also materialize briefing_mentions so position
    # detail / search don't have to open every JSON per request.
    _refresh_briefing_mentions(conn)

    snap_count = conn.execute(
        "SELECT COUNT(*) FROM portfolio_timeseries"
    ).fetchone()[0]
    expected = _valid_briefing_json_count()

    _LAST_STATUS = IngestStatus(
        snapshot_count=int(snap_count),
        expected_count=expected,
        last_ingest_at=datetime.now(),
        delivery_dir=str(briefings_delivery()),
        snapshots_dir=str(snapshots_root()),
    )


def _refresh_briefing_mentions(conn: duckdb.DuckDBPyConnection) -> None:
    """Build the briefing_mentions table by walking every briefing JSON.

    M2 issue #2: position-detail used to open every briefing per request
    to find ticker mentions. This table denormalizes that work into a
    queryable shape:
      (date, ticker, source, recommendation, detail, ident, kind)

    `source` ∈ {equity_review, options_review, long_term_opportunity,
                 action, new_idea, strategy_upgrade}.

    For action items, `ticker` is parsed out of `ident` (e.g.
    `META_PUT_525_20260821` → `META`, `HEDGE:SPY` → `SPY`).
    """
    import json
    import re

    rows: list[tuple] = []
    delivery = briefings_delivery()
    if not delivery.exists():
        # Still create an empty table so queries don't crash
        conn.execute("""
            CREATE OR REPLACE TABLE briefing_mentions (
                date DATE, ticker VARCHAR, source VARCHAR,
                recommendation VARCHAR, detail VARCHAR,
                ident VARCHAR, kind VARCHAR
            )
        """)
        return

    _ticker_re = re.compile(r"\b([A-Z][A-Z0-9]{0,4})\b")
    for path in sorted(delivery.glob("briefing_*.json")):
        if path.name.endswith(".DRAFT.json"):
            continue
        m = re.match(r"briefing_(\d{4}-\d{2}-\d{2})\.json$", path.name)
        if not m:
            continue
        date = m.group(1)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        for er in data.get("equity_reviews") or []:
            tk = (er.get("ticker") or "").upper()
            if tk:
                rows.append((date, tk, "equity_review",
                             er.get("recommendation"),
                             (er.get("rationale") or "")[:500],
                             None, None))
        for o in data.get("options_reviews") or []:
            tk = (o.get("underlying") or "").upper()
            if tk:
                detail = f"{o.get('contract', '—')}: {o.get('rationale', '') or ''}"[:500]
                rows.append((date, tk, "options_review",
                             o.get("recommendation"), detail,
                             o.get("contract"), o.get("recommendation")))
        for op in data.get("long_term_opportunities") or []:
            tk = (op.get("ticker") or "").upper()
            if tk:
                detail = (op.get("concrete_trade") or op.get("rationale") or "")[:500]
                rows.append((date, tk, "long_term_opportunity",
                             op.get("kind"), detail, None, op.get("kind")))
        for ni in data.get("new_ideas") or []:
            tk = (ni.get("ticker") or "").upper()
            if tk:
                rows.append((date, tk, "new_idea",
                             ni.get("source"), (ni.get("rationale") or "")[:500],
                             None, None))
        for s in data.get("strategy_upgrades") or []:
            tk = (s.get("underlying") or "").upper()
            if tk:
                rows.append((date, tk, "strategy_upgrade",
                             s.get("type"), (s.get("rationale") or "")[:500],
                             None, s.get("type")))
        for a in data.get("actions") or []:
            ident = (a.get("ident") or "").upper()
            kind = a.get("kind")
            summary = (a.get("summary") or "")[:500]
            # Parse ticker out of ident: take the first plausible CAPS token
            tk = None
            # SYMBOL_PUT_..._YYYYMMDD → SYMBOL
            mp = re.match(r"^([A-Z][A-Z0-9]{0,4})_(?:PUT|CALL)", ident)
            if mp:
                tk = mp.group(1)
            else:
                # "HEDGE:SPY" → SPY, or bare ticker
                ident_clean = ident.split(":")[-1] if ":" in ident else ident
                m2 = _ticker_re.match(ident_clean)
                if m2:
                    tk = m2.group(1)
            if tk:
                rows.append((date, tk, "action", kind, summary, ident, kind))

    conn.execute("""
        CREATE OR REPLACE TABLE briefing_mentions (
            date DATE, ticker VARCHAR, source VARCHAR,
            recommendation VARCHAR, detail VARCHAR,
            ident VARCHAR, kind VARCHAR
        )
    """)
    if rows:
        conn.executemany(
            "INSERT INTO briefing_mentions VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )


# ─── Convenience queries used by the routes ───────────────────────────


def list_briefing_dates(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """All available briefing dates, descending (newest first)."""
    rows = conn.execute(
        "SELECT date FROM portfolio_timeseries ORDER BY date DESC"
    ).fetchall()
    return [str(r[0]) for r in rows]


def latest_briefing_date(conn: duckdb.DuckDBPyConnection) -> str | None:
    """The newest available date in portfolio_timeseries, or None if empty."""
    row = conn.execute(
        "SELECT date FROM portfolio_timeseries ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def load_briefing_json(date: str) -> dict[str, Any] | None:
    """Read one briefing_<DATE>.json straight from disk (bypass DuckDB).

    We use DuckDB for time-series queries but Pydantic-validated full
    briefing renders read the JSON directly — simpler, no column-pivot
    work needed.
    """
    import json

    path = briefings_delivery() / f"briefing_{date}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_snapshot_recs(date: str) -> list[dict[str, Any]]:
    """Read recommendations_list.json for one date. Returns []."""
    import json

    path = snapshots_root() / date / "recommendations_list.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("recommendations") or [])
    except (OSError, json.JSONDecodeError):
        return []


def load_snapshot_positions(date: str) -> list[dict[str, Any]]:
    """Read positions.json for one date. Returns [] on failure."""
    import json

    path = snapshots_root() / date / "positions.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data) if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# ─── Technicals (deep per-ticker read) ───────────────────────────────
# technicals.json is written by snapshot_inputs per snapshot date, keyed
# by ticker. Each entry may carry a "deep" payload (TechnicalSnapshot
# from analysis/technical_indicators.py) plus "support_resistance".
# Small file (~100-300 KB) — lazy-loaded per request with an mtime-keyed
# cache so repeated card renders on one page don't re-read the disk.

_TECH_JSON_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def load_snapshot_technicals(date: str) -> dict[str, Any]:
    """Full technicals.json for one snapshot date: {TICKER: {...}}.

    Returns {} when the file is missing or unparseable (fail closed —
    callers render "chart data unavailable", never fabricated values).
    """
    import json

    if not date:
        return {}
    path = snapshots_root() / date / "technicals.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _TECH_JSON_CACHE.get(date)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Normalize ticker keys to upper for case-safe lookups
    data = {str(k).upper(): v for k, v in data.items()}
    _TECH_JSON_CACHE[date] = (mtime, data)
    return data


def load_ticker_technicals(date: str, ticker: str) -> dict[str, Any] | None:
    """The `.deep` payload (TechnicalSnapshot dict) for one ticker on one
    date, or None when missing. See analysis/technical_indicators.py for
    the field contract."""
    entry = load_snapshot_technicals(date).get((ticker or "").upper())
    if not isinstance(entry, dict):
        return None
    deep = entry.get("deep")
    return deep if isinstance(deep, dict) else None


def positions_timeseries_for_ticker(
    conn: duckdb.DuckDBPyConnection, ticker: str
) -> list[dict[str, Any]]:
    """Per-day rows for a single ticker (equity OR option underlying).

    Includes both equity holdings of `ticker` and options whose underlying
    matches.
    """
    rows = conn.execute(
        """
        SELECT date, symbol, assetType, qty, price, market_value,
               opt_type, strike, expiration
        FROM positions_timeseries
        WHERE upper(underlying) = upper(?)
           OR upper(symbol) = upper(?)
        ORDER BY date, symbol
        """,
        [ticker, ticker],
    ).fetchall()
    cols = ["date", "symbol", "assetType", "qty", "price", "market_value",
            "opt_type", "strike", "expiration"]
    return [dict(zip(cols, r)) for r in rows]


def parkev_history_for_ticker(
    conn: duckdb.DuckDBPyConnection, ticker: str
) -> list[dict[str, Any]]:
    """All Parkev rating snapshots for one ticker, ascending date."""
    rows = conn.execute(
        """
        SELECT date, ticker, recommendation, raw_recommendation,
               rating_tier, conviction, conviction_score, age_days,
               aging, date_updated
        FROM parkev_history
        WHERE upper(ticker) = upper(?)
        ORDER BY date
        """,
        [ticker],
    ).fetchall()
    cols = ["date", "ticker", "recommendation", "raw_recommendation",
            "rating_tier", "conviction", "conviction_score", "age_days",
            "aging", "date_updated"]
    return [dict(zip(cols, r)) for r in rows]


def briefing_summary_rows(
    conn: duckdb.DuckDBPyConnection,
) -> list[dict[str, Any]]:
    """Full portfolio_timeseries as a list of dicts (for chart endpoints)."""
    rows = conn.execute(
        "SELECT date, nlv, cash, cash_pct, regime FROM portfolio_timeseries ORDER BY date"
    ).fetchall()
    cols = ["date", "nlv", "cash", "cash_pct", "regime"]
    return [dict(zip(cols, r)) for r in rows]


def briefing_mentions_for_ticker(
    conn: duckdb.DuckDBPyConnection, ticker: str
) -> list[dict[str, Any]]:
    """All briefing mentions of one ticker, most recent first.

    M2 issue #2: this hits the materialized `briefing_mentions` table
    instead of opening every briefing JSON per request.
    """
    rows = conn.execute(
        """
        SELECT date, source, recommendation, detail, ident, kind
        FROM briefing_mentions
        WHERE upper(ticker) = upper(?)
        ORDER BY date DESC
        """,
        [ticker],
    ).fetchall()
    cols = ["date", "source", "recommendation", "detail", "ident", "kind"]
    return [dict(zip(cols, r)) for r in rows]


def search_mentions(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    source_filter: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Cross-snapshot text search across briefing_mentions.

    Search semantics:
      - `query` is matched case-insensitively against ticker, kind,
        recommendation, and detail.
      - `source_filter` (optional) restricts to one source type:
        'action', 'equity_review', 'options_review',
        'long_term_opportunity', 'new_idea', 'strategy_upgrade'.
      - A query that LOOKS like a date prefix (e.g. '2026-06') also
        matches dates by string-prefix.

    Returns up to `limit` rows, most recent first.
    """
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    sql = """
        SELECT date, ticker, source, recommendation, detail, ident, kind
        FROM briefing_mentions
        WHERE (
            upper(ticker) LIKE upper(?)
            OR upper(COALESCE(kind, '')) LIKE upper(?)
            OR upper(COALESCE(recommendation, '')) LIKE upper(?)
            OR upper(COALESCE(detail, '')) LIKE upper(?)
            OR CAST(date AS VARCHAR) LIKE ?
        )
    """
    params = [like, like, like, like, like]
    if source_filter:
        sql += " AND source = ?"
        params.append(source_filter)
    sql += " ORDER BY date DESC, ticker LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    cols = ["date", "ticker", "source", "recommendation", "detail", "ident", "kind"]
    return [dict(zip(cols, r)) for r in rows]


def search_distinct_tickers(
    conn: duckdb.DuckDBPyConnection, query: str
) -> list[str]:
    """Distinct tickers matching a substring (for typeahead-style results)."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q.upper()}%"
    rows = conn.execute(
        """
        SELECT DISTINCT ticker
        FROM briefing_mentions
        WHERE upper(ticker) LIKE ?
        ORDER BY ticker
        """,
        [like],
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def time_since(when: datetime) -> str:
    """Human "12s ago" for the footer."""
    delta = (datetime.now() - when).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"
