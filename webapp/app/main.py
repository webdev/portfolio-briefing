"""FastAPI app for the briefing dashboard.

Milestone 1 routes (read-only):
    GET  /                              dashboard shell
    GET  /briefing/latest               302 to latest dated briefing
    GET  /briefing/{date}               full briefing render
    GET  /positions                     all current positions (latest snapshot)
    GET  /positions/{ticker}            per-ticker drill-down
    GET  /parkev/{ticker}               Parkev rating timeline
    GET  /history                       3 charts page
    GET  /charts/nlv.json               Plotly JSON
    GET  /charts/coverage.json          Plotly JSON
    GET  /charts/expiration-ladder.json Plotly JSON
    GET  /charts/parkev/{ticker}.json   Plotly JSON
    GET  /fragment/refresh-status       HTMX target
    GET  /health                        liveness check

Milestone 2 routes (interactive):
    POST /refresh                        kick off a pipeline run (single-flight)
    GET  /jobs/{id}                      job status JSON
    GET  /events?job_id=...              SSE: log lines + ingest_complete event
    GET  /diff/{a}/{b}                   side-by-side briefing diff
    GET  /diff/yesterday/latest          redirect to most-recent pair
    GET  /search?q=...&type=...          cross-snapshot search
    GET  /fragment/ticker_card/{tk}      ticker hover popup (HTMX swap target)
    GET  /fragment/counterpoint/{key}    counterpoint disclosure (HTMX swap)
    GET  /charts/sparkline/{tk}.json     30d mini sparkline (Plotly JSON)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import contract, counterpoints, diff, home, icons, ideas, inline_md, ingest, jobs, md_render, position_state, program_edge, red_flags, report_parser, setups, tech_card, unified_card
from .chips import (
    clear_recs_cache,
    parkev_chip_renderer,
    tier_badge_renderer,
)
from .charts import (
    build_benchmark_figure,
    build_coverage_figure,
    build_expiration_ladder_figure,
    build_nlv_figure,
    build_parkev_timeline,
    build_program_edge_figure,
    build_ticker_sparkline,
)
from .config import briefings_delivery
from .models import load_briefing


HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
# Register action/severity/state icon helpers as Jinja filters so templates
# can render visual cues on action verbs (e.g. "🔚 CLOSE", "🛡️ HEDGE").
icons.register_jinja_filters(templates.env)
# Inline-markdown filters so summary lines render bold / code / italic
# properly instead of leaking raw markdown literals into the UI.
inline_md.register_jinja_filters(templates.env)
# Pretty-print option contract symbols (LITE_PUT_660_20260918 →
# LITE $660P · Sep 18 '26) so action IDs are scannable, not cryptic.
contract.register_jinja_filters(templates.env)


# ─── App + shared connection ──────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: materialize all projections once so the first request is fast.

    M2 issue #1 fix: per-request connects no longer re-materialize.
    """
    ingest.refresh_projections()
    yield


def create_app() -> FastAPI:
    """Factory — used by tests to get a fresh app instance."""
    app = FastAPI(
        title="Portfolio Briefing",
        description="Dashboard over the daily briefing snapshots.",
        version="0.2.0",
        lifespan=_lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: HTTPException) -> HTMLResponse:
        return _render(
            request,
            "error.html",
            _ctx(request, code=404, title="Not Found", message=str(exc.detail) or "Not found."),
            status_code=404,
        )

    @app.exception_handler(500)
    async def _server_error(request: Request, exc: Exception) -> HTMLResponse:
        return _render(
            request,
            "error.html",
            _ctx(request, code=500, title="Server Error", message=str(exc) or "Server error."),
            status_code=500,
        )

    _wire_routes(app)
    return app


def _get_conn() -> duckdb.DuckDBPyConnection:
    """Per-request DuckDB connection. M2: this is now lightweight — the
    materialized projections live in the DuckDB file (built at startup)
    and refresh runs only after POST /refresh."""
    return ingest.connect()


def _topbar_context(briefing: Any | None, conn: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not briefing:
        return None
    cov = None
    try:
        from .charts.coverage import _coverage_for_date
        cov = _coverage_for_date(conn, briefing.date, briefing.cash)
    except Exception:
        cov = None
    date_iso = briefing.date.isoformat()
    return {
        "date": date_iso,
        "nlv": briefing.nlv,
        "cash_pct": briefing.cash_pct,
        "coverage": cov,
        "regime": briefing.regime,
        # Short SHA-256 fingerprint so the user can visually confirm the
        # UI is serving the SAME briefing they just generated (task #50).
        "checksum": ingest.briefing_checksum(date_iso),
    }


def _status_context() -> dict[str, Any]:
    s = ingest.get_last_status()
    if not s:
        return {}
    return {
        "snapshot_count": s.snapshot_count,
        "expected_count": s.expected_count,
        "last_ingest_rel": ingest.time_since(s.last_ingest_at),
        "delivery_dir": s.delivery_dir,
    }


def _ctx(request: Request, *, briefing_date: str | None = None, **kwargs) -> dict[str, Any]:
    """Build the standard Jinja context.

    M2 fix (issue #3): pass ``briefing_date`` so the Parkev chip renderer
    looks up THAT date's ratings, not today's. Falls back to the latest
    snapshot's recs when no date is given.
    """
    # Resolve the effective recs date
    eff_date = briefing_date
    if eff_date is None:
        # Default to the latest snapshot — old behavior for dashboard/positions
        try:
            conn = ingest.connect()
            try:
                eff_date = ingest.latest_briefing_date(conn)
            finally:
                conn.close()
        except Exception:
            eff_date = None

    base = {
        "request": request,
        "status": _status_context(),
        "parkev_chip": parkev_chip_renderer(eff_date),
        "tier_badge": tier_badge_renderer(),
        "running_job": jobs.get_running_job(),
        "chip_date": eff_date,
    }
    base.update(kwargs)
    return base


def _render(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


def _wire_routes(app: FastAPI) -> None:

    # ─── Pages ────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            briefing = None
            coverage = None
            if latest:
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
                    from .charts.coverage import _coverage_for_date
                    coverage = _coverage_for_date(conn, briefing.date, briefing.cash)
            # ─── Morning-briefing Home page (UX Tier 2 redesign) ───
            # Optimized for "read once, act, close" workflow. The old
            # dashboard.html is kept as /dashboard for users who prefer
            # the dense view. Everything above the fold: one-sentence
            # state, urgent actions, KPI strip, diff summary, top opps.
            flags = red_flags.compute_red_flags(briefing, coverage)
            urgent_actions = home.top_urgent_actions(briefing.actions or [], limit=3)
            hero = home.derive_hero_sentence(
                flags, urgent_action_count=len(urgent_actions), briefing=briefing,
            )

            # Diff summary — build against yesterday when available
            diff_summary = None
            try:
                all_dates = ingest.list_briefing_dates(conn)
                if all_dates and len(all_dates) >= 2 and briefing.date.isoformat() == all_dates[0]:
                    prev_date = all_dates[1]
                    prev_raw = ingest.load_briefing_json(prev_date)
                    if prev_raw:
                        diff_result = diff.compute_diff(prev_raw, raw)
                        diff_summary = home.build_diff_summary(diff_result)
            except Exception:
                pass  # diff is nice-to-have; never block the home page

            # Top actionable opportunities from setups (consolidated)
            top_opportunities = []
            try:
                structured_setups = setups.build_setups(latest)
                top_opportunities = home.top_actionable_from_setups(
                    structured_setups, limit=3,
                )
            except Exception:
                pass

            # Task #53 — Fable review preview on home. Pulls the first
            # cross-section observation for a one-line callout with a
            # link to the full review tab. When no review exists yet
            # (fresh install / API down / disabled), fable_preview is
            # None and the template hides the callout entirely.
            fable_preview = None
            try:
                from . import fable as _fable
                fable_data = _fable.load_review_for_date(latest) if latest else None
                if fable_data and fable_data.get("present"):
                    fable_preview = {
                        "first_observation": _fable.first_observation(fable_data),
                        "date": latest,
                        "metadata": fable_data.get("metadata") or {},
                    }
            except Exception:
                pass  # home page must never fail on fable errors

            # 💪 Program Edge — real vs no-options ghost, daily. Fail-open:
            # missing/short/corrupt state file → "building history (N days)"
            # card, never a 500 (house rule: state corruption recovers).
            program_edge_card = None
            try:
                program_edge_card = program_edge.build_card(program_edge.load_state())
            except Exception:
                pass

            return _render(
                request,
                "home.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    briefing=briefing,
                    coverage=coverage,
                    hero_sentence=hero,
                    urgent_actions=urgent_actions,
                    diff_summary=diff_summary,
                    top_opportunities=top_opportunities,
                    red_flags=flags,
                    red_flag_counts=red_flags.summary_counts(flags),
                    topbar=_topbar_context(briefing, conn),
                    delivery_dir=str(briefings_delivery()),
                    fable_preview=fable_preview,
                    program_edge=program_edge_card,
                ),
            )
        finally:
            conn.close()

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_legacy(request: Request) -> HTMLResponse:
        """Legacy dense dashboard (pre-UX-redesign). Kept as backup for
        users who prefer everything visible at once. New default is the
        morning-briefing home at /."""
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            briefing = None
            coverage = None
            if latest:
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
                    from .charts.coverage import _coverage_for_date
                    coverage = _coverage_for_date(conn, briefing.date, briefing.cash)
            flags = red_flags.compute_red_flags(briefing, coverage)
            return _render(
                request,
                "dashboard.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    briefing=briefing,
                    coverage=coverage,
                    red_flags=flags,
                    red_flag_counts=red_flags.summary_counts(flags),
                    topbar=_topbar_context(briefing, conn),
                    delivery_dir=str(briefings_delivery()),
                ),
            )
        finally:
            conn.close()

    @app.get("/briefing/latest")
    async def briefing_latest() -> RedirectResponse:
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            if not latest:
                raise HTTPException(status_code=404, detail="No briefings loaded yet.")
            return RedirectResponse(url=f"/briefing/{latest}", status_code=302)
        finally:
            conn.close()

    @app.get("/briefing/latest/md")
    async def briefing_latest_md() -> RedirectResponse:
        """302 to the latest briefing's document view. Enables the
        topbar link /briefing/latest/md to always land on today."""
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            if not latest:
                raise HTTPException(status_code=404, detail="No briefings loaded yet.")
            return RedirectResponse(url=f"/briefing/{latest}/md", status_code=302)
        finally:
            conn.close()

    @app.get("/briefing/{date}/md", response_class=HTMLResponse)
    async def briefing_markdown_view(request: Request, date: str) -> HTMLResponse:
        """Serve the raw briefing markdown as a typography-first document.

        User feedback: the chrome-heavy dashboard is harder to read than
        the pipeline's own markdown output. This route reads
        ``~/Documents/briefings/briefing_<DATE>.md`` and renders it
        with minimal styling — headings, prose, code blocks, no chips.

        Read top-to-bottom like a memo. Zero context-switching.
        """
        from pathlib import Path
        md_path = briefings_delivery() / f"briefing_{date}.md"
        if not md_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No briefing markdown for {date} at {md_path}",
            )
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

        rendered = md_render.render(text)

        # Also compute the neighbor dates so the reader can jump between days
        conn = _get_conn()
        try:
            all_dates = ingest.list_briefing_dates(conn)
        finally:
            conn.close()
        neighbor_dates = {"prev": None, "next": None}
        if date in all_dates:
            idx = all_dates.index(date)
            if idx + 1 < len(all_dates):
                neighbor_dates["prev"] = all_dates[idx + 1]
            if idx - 1 >= 0:
                neighbor_dates["next"] = all_dates[idx - 1]

        return _render(
            request,
            "briefing_markdown.html",
            _ctx(
                request,
                briefing_date=date,
                date=date,
                rendered_html=rendered,
                source_path=str(md_path),
                neighbor_dates=neighbor_dates,
                # Show both JSON + MD checksums so the user can confirm
                # both artifacts are from the same run (task #50).
                checksum_json=ingest.briefing_checksum(date, "json"),
                checksum_md=ingest.briefing_checksum(date, "md"),
                topbar=None,
            ),
        )

    @app.get("/briefing/{date}/md/raw", response_class=PlainTextResponse)
    async def briefing_markdown_raw(date: str) -> PlainTextResponse:
        """Serve the raw markdown as text/plain — for users who want
        to copy-paste into their own tools, or for `curl | less`."""
        md_path = briefings_delivery() / f"briefing_{date}.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail=f"No briefing markdown for {date}")
        try:
            return PlainTextResponse(md_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/briefing/{date}", response_class=HTMLResponse)
    async def briefing_detail(request: Request, date: str) -> HTMLResponse:
        raw = ingest.load_briefing_json(date)
        if not raw:
            raise HTTPException(status_code=404, detail=f"No briefing for {date}.")
        briefing = load_briefing(raw)
        conn = _get_conn()
        try:
            all_dates = ingest.list_briefing_dates(conn)
            neighbor_dates = {"prev": None, "next": None}
            if date in all_dates:
                idx = all_dates.index(date)
                if idx + 1 < len(all_dates):
                    neighbor_dates["prev"] = all_dates[idx + 1]
                if idx - 1 >= 0:
                    neighbor_dates["next"] = all_dates[idx - 1]

            # M2: attach counterpoints to each action so the template can
            # render an expandable disclosure inline.
            cmap = counterpoints.counterpoints_for_date(date)

            # M2 follow-up (hard rule #32): merge new_ideas with the
            # actionable opportunity kinds from long_term_opportunities so
            # the Ideas tab never shows ONLY a capacity-blocked placeholder.
            merged_ideas = ideas.build_merged_ideas(briefing)

            # Rule #43 UX (GOOG $345P, 2026-08-04): the tab badge counts
            # ONLY actionable ideas; waiting/gated cards render in muted
            # sections so a demoted ticket never reads as a rec.
            ideas_groups = ideas.group_merged_ideas(merged_ideas)
            ideas_actionable_count, ideas_gated_count = (
                ideas.ideas_badge_counts(merged_ideas)
            )

            # Task #17 — the Strategy tab is retired. CC/collar proposals
            # attach to the Options tab (inline affordance on the matching
            # option card, or a "Strategy proposals" subsection); sub-lot
            # completions merge into Ideas via build_merged_ideas above.
            strategy_attached, strategy_standalone = (
                unified_card.split_strategy_upgrades(briefing)
            )

            # Task #53 — surface the fable review (v1 critic or v2
            # advisor) as a first-class tab. The parser reads the
            # section out of the delivered briefing markdown and pulls
            # audit metadata from state/briefing_snapshots/<date>/. When
            # no review is present, the tab is suppressed entirely (not
            # a broken empty tab).
            from . import fable as _fable
            fable_review = _fable.load_review_for_date(date)

            # Task #7 — "🎯 Technical Read" tab: infographic-style tech
            # cards for every ticker in the day's technicals.json, grouped
            # by portfolio role. Empty dict → tab suppressed.
            tech_read = tech_card.build_groups(date)

            # Task #10 — unified ticker cards on EVERY tab: ticker → tech
            # card view-model for each ticker with a deep read this cycle.
            # Fail-open: any error → {} and every card renders the compact
            # "chart data unavailable" skeleton instead of 500ing the page.
            try:
                tech_by_ticker = tech_card.build_map(date)
            except Exception:
                tech_by_ticker = {}

            return _render(
                request,
                "briefing.html",
                _ctx(
                    request,
                    briefing_date=date,
                    briefing=briefing,
                    topbar=_topbar_context(briefing, conn),
                    neighbor_dates=neighbor_dates,
                    counterpoint_keys=set(cmap.keys()),
                    merged_ideas=merged_ideas,
                    ideas_groups=ideas_groups,
                    ideas_actionable_count=ideas_actionable_count,
                    ideas_gated_count=ideas_gated_count,
                    strategy_attached=strategy_attached,
                    strategy_standalone=strategy_standalone,
                    strategy_affordance_label=unified_card.strategy_affordance_label,
                    fable_review=fable_review,
                    tech_read=tech_read,
                    tech_by_ticker=tech_by_ticker,
                    # Helpers the unified_card macro calls (imported with
                    # context in briefing.html).
                    resolve_ticker=unified_card.resolve_ticker,
                    option_pl_pct=unified_card.option_pl_pct,
                    extras_tone=unified_card.extras_tone,
                ),
            )
        finally:
            conn.close()

    @app.get("/positions", response_class=HTMLResponse)
    async def positions(request: Request) -> HTMLResponse:
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            equities: list[dict] = []
            options: list[dict] = []
            all_positions: list[dict] = []
            briefing = None
            if latest:
                all_positions = ingest.load_snapshot_positions(latest)
                equities = [p for p in all_positions if p.get("assetType") != "OPTION"]
                options = [p for p in all_positions if p.get("assetType") == "OPTION"]
                equities.sort(key=lambda p: -(p.get("marketValue") or 0))
                options.sort(key=lambda p: (p.get("underlying") or "", p.get("expiration") or ""))
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
            return _render(
                request,
                "positions.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    positions=all_positions,
                    equities=equities,
                    options=options,
                    as_of=latest or "n/a",
                    topbar=_topbar_context(briefing, conn),
                ),
            )
        finally:
            conn.close()

    @app.get("/positions/{ticker}", response_class=HTMLResponse)
    async def position_detail(request: Request, ticker: str) -> HTMLResponse:
        ticker = ticker.upper()
        conn = _get_conn()
        try:
            history = ingest.positions_timeseries_for_ticker(conn, ticker)
            latest = ingest.latest_briefing_date(conn)

            latest_equity = None
            if latest:
                positions = ingest.load_snapshot_positions(latest)
                for p in positions:
                    if p.get("symbol") == ticker and p.get("assetType") != "OPTION":
                        latest_equity = p
                        break

            # M2 issue #2 fix: hit the materialized briefing_mentions table.
            mentions = ingest.briefing_mentions_for_ticker(conn, ticker)
            briefing = None
            if latest:
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
            # Assemble the current-state card (UX critique #7) — RSI, IV,
            # trend, drawdown, S/R, Parkev — pulled from snapshot + briefing.
            # The briefing carries snapshot_dir as a repo-relative path (e.g.
            # "state/briefing_snapshots/2026-06-30"); resolve to the actual
            # location under snapshots_root() which the config knows about.
            snap_dir = None
            if latest:
                snap_dir = ingest.snapshots_root() / latest
                if not snap_dir.exists():
                    snap_dir = None
            state = position_state.build_position_state(
                ticker,
                snapshot_dir=snap_dir,
                briefing=briefing,
                latest_equity=latest_equity,
            )
            # Task #7 — infographic-style tech card (deep read from
            # technicals.json). Skeleton when the deep payload is missing.
            tech = tech_card.build_for_ticker(latest, ticker)
            return _render(
                request,
                "position_detail.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    ticker=ticker,
                    history=history,
                    latest_equity=latest_equity,
                    mentions=mentions,
                    state=state,
                    tech=tech,
                    topbar=_topbar_context(briefing, conn),
                ),
            )
        finally:
            conn.close()

    @app.get("/parkev/{ticker}", response_class=HTMLResponse)
    async def parkev_page(request: Request, ticker: str) -> HTMLResponse:
        ticker = ticker.upper()
        conn = _get_conn()
        try:
            history = ingest.parkev_history_for_ticker(conn, ticker)
            latest = ingest.latest_briefing_date(conn)
            briefing = None
            if latest:
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
            return _render(
                request,
                "parkev.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    ticker=ticker,
                    history=history,
                    topbar=_topbar_context(briefing, conn),
                ),
            )
        finally:
            conn.close()

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request) -> HTMLResponse:
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            briefing = None
            if latest:
                raw = ingest.load_briefing_json(latest)
                if raw:
                    briefing = load_briefing(raw)
            snap_count = ingest.get_last_status().snapshot_count if ingest.get_last_status() else 0
            return _render(
                request,
                "history.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    snapshot_count=snap_count,
                    latest_date=latest,
                    topbar=_topbar_context(briefing, conn),
                ),
            )
        finally:
            conn.close()

    # ─── M2: Diff page ────────────────────────────────────────────────

    @app.get("/diff/yesterday/latest")
    async def diff_yesterday_latest() -> RedirectResponse:
        """Redirect to the most-recent two snapshots."""
        conn = _get_conn()
        try:
            dates = ingest.list_briefing_dates(conn)
        finally:
            conn.close()
        if len(dates) < 2:
            raise HTTPException(status_code=404, detail="Need ≥2 snapshots to diff.")
        # list_briefing_dates returns DESC
        return RedirectResponse(
            url=f"/diff/{dates[1]}/{dates[0]}", status_code=302
        )

    @app.get("/diff/{date_a}/{date_b}", response_class=HTMLResponse)
    async def diff_page(request: Request, date_a: str, date_b: str) -> HTMLResponse:
        raw_a = ingest.load_briefing_json(date_a)
        raw_b = ingest.load_briefing_json(date_b)
        if not raw_a:
            raise HTTPException(status_code=404, detail=f"No briefing for {date_a}.")
        if not raw_b:
            raise HTTPException(status_code=404, detail=f"No briefing for {date_b}.")

        positions_a = ingest.load_snapshot_positions(date_a)
        positions_b = ingest.load_snapshot_positions(date_b)
        recs_a = {(r.get("ticker") or "").upper(): r
                  for r in ingest.load_snapshot_recs(date_a) if r.get("ticker")}
        recs_b = {(r.get("ticker") or "").upper(): r
                  for r in ingest.load_snapshot_recs(date_b) if r.get("ticker")}

        d = diff.diff_briefings(
            raw_a, raw_b,
            recs_a=recs_a, recs_b=recs_b,
            positions_a=positions_a, positions_b=positions_b,
        )

        conn = _get_conn()
        try:
            all_dates = ingest.list_briefing_dates(conn)
            return _render(
                request,
                "diff.html",
                _ctx(
                    request,
                    briefing_date=date_b,  # chip on diff renders for the newer date
                    diff=d,
                    date_a=date_a,
                    date_b=date_b,
                    all_dates=all_dates,
                    topbar=None,
                ),
            )
        finally:
            conn.close()

    # HTMX swap target — just the body of the diff so the date pickers can
    # change the comparison without a full reload.
    @app.get("/fragment/diff/{date_a}/{date_b}", response_class=HTMLResponse)
    async def diff_fragment(request: Request, date_a: str, date_b: str) -> HTMLResponse:
        raw_a = ingest.load_briefing_json(date_a)
        raw_b = ingest.load_briefing_json(date_b)
        if not raw_a or not raw_b:
            return HTMLResponse("<div class='empty'>One or both dates not found.</div>", status_code=404)
        positions_a = ingest.load_snapshot_positions(date_a)
        positions_b = ingest.load_snapshot_positions(date_b)
        recs_a = {(r.get("ticker") or "").upper(): r
                  for r in ingest.load_snapshot_recs(date_a) if r.get("ticker")}
        recs_b = {(r.get("ticker") or "").upper(): r
                  for r in ingest.load_snapshot_recs(date_b) if r.get("ticker")}
        d = diff.diff_briefings(
            raw_a, raw_b,
            recs_a=recs_a, recs_b=recs_b,
            positions_a=positions_a, positions_b=positions_b,
        )
        return _render(
            request, "diff_body.html",
            _ctx(request, briefing_date=date_b, diff=d, date_a=date_a, date_b=date_b),
        )

    # ─── Companion markdown reports (candidates_*, when_to_enter_*) ──
    # These render the pipeline's per-day companion .md files inside the
    # web app shell so the user can browse them without context-switching
    # to a terminal/editor. Hard rule #19 — fail closed when missing.

    def _markdown_report_response(
        request: Request,
        *,
        kind: md_render.ReportKind,
        date: str,
        title: str,
        subtitle: str,
        base_url: str,
    ) -> HTMLResponse:
        text = md_render.load_report(kind, date)
        # 404 only when the file is genuinely missing — empty file renders
        # the navigator + a friendly empty card instead.
        if text is None:
            raise HTTPException(
                status_code=404,
                detail=f"No {kind} report for {date}.",
            )
        # Try structured parsing first; fall back to raw markdown render
        # when no cards parse (handles schema drift + alt fixture formats).
        # Try BOTH header patterns so a when-to-enter fixture written in the
        # candidates style still renders structured.
        structured = report_parser.parse_report(text, kind)
        counts = report_parser.summary_counts(structured) if structured else {}
        total_cards = sum(counts.values())
        if total_cards == 0 and kind == "when_to_enter":
            # Try parsing it as a candidates doc instead
            alt = report_parser.parse_report(text, "candidates")
            alt_total = sum(report_parser.summary_counts(alt).values())
            if alt_total > 0:
                structured = alt
                counts = report_parser.summary_counts(alt)
                total_cards = alt_total
        rendered_html = md_render.render(text) if total_cards == 0 else None
        if total_cards == 0:
            structured = None  # ensures template falls into the markdown branch
        return _render(
            request,
            "report_view.html",
            _ctx(
                request,
                briefing_date=date,
                date=date,
                report_title=title,
                report_subtitle=subtitle,
                report_base_url=base_url,
                neighbor_dates=md_render.neighbor_dates(kind, date),
                structured=structured,
                summary_counts=counts,
                rendered_html=rendered_html,
                source_path=md_render.report_path_str(kind, date),
                topbar=None,
            ),
        )

    @app.get("/candidates/latest")
    async def candidates_latest() -> RedirectResponse:
        latest = md_render.latest_date("candidates")
        if not latest:
            raise HTTPException(
                status_code=404,
                detail="No candidate reports on disk yet.",
            )
        return RedirectResponse(url=f"/candidates/{latest}", status_code=302)

    @app.get("/candidates/{date}", response_class=HTMLResponse)
    async def candidates_page(request: Request, date: str) -> HTMLResponse:
        return _markdown_report_response(
            request,
            kind="candidates",
            date=date,
            title="Candidate Research",
            subtitle=(
                "Per-company research across every Scout theme — RSI, IV, "
                "drawdown, valuation, verdict. Status: CANDIDATE / WATCH / AVOID."
            ),
            base_url="/candidates",
        )

    # ─── Setups (consolidated Candidates + When-to-Enter) ─────────────

    @app.get("/setups/latest")
    async def setups_latest() -> RedirectResponse:
        latest = md_render.latest_date("candidates") or md_render.latest_date("when_to_enter")
        if not latest:
            raise HTTPException(
                status_code=404,
                detail="No candidate/when-to-enter reports on disk yet.",
            )
        return RedirectResponse(url=f"/setups/{latest}", status_code=302)

    @app.get("/setups/{date}", response_class=HTMLResponse)
    async def setups_page(request: Request, date: str) -> HTMLResponse:
        structured = setups.build_setups(date)
        counts = setups.summary_counts_split(structured) if structured.get("sections") else {}
        return _render(
            request,
            "report_view.html",
            _ctx(
                request,
                briefing_date=date,
                date=date,
                # Task #7 — ticker → tech-card view-model for candidates
                # whose deep read exists (rendered as an expander per card).
                tech_cards=tech_card.build_map(date),
                report_title="Setups",
                report_subtitle=structured.get("subtitle") or "",
                report_base_url="/setups",
                neighbor_dates=md_render.neighbor_dates("candidates", date),
                structured=structured,
                summary_counts=counts,
                rendered_html=None,
                source_path=(md_render.report_path_str("candidates", date)
                             + " + "
                             + md_render.report_path_str("when_to_enter", date)),
                topbar=None,
            ),
        )

    @app.get("/when-to-enter/latest")
    async def when_to_enter_latest() -> RedirectResponse:
        latest = md_render.latest_date("when_to_enter")
        if not latest:
            raise HTTPException(
                status_code=404,
                detail="No when-to-enter reports on disk yet.",
            )
        return RedirectResponse(url=f"/when-to-enter/{latest}", status_code=302)

    @app.get("/when-to-enter/{date}", response_class=HTMLResponse)
    async def when_to_enter_page(request: Request, date: str) -> HTMLResponse:
        return _markdown_report_response(
            request,
            kind="when_to_enter",
            date=date,
            title="When to Enter",
            subtitle=(
                "Every theme company classified with an explicit entry trigger — "
                "ENTRY NOW / WAIT / WATCH / AVOID. Triggers are precise (RSI band, "
                "support price, IV gate), not vague."
            ),
            base_url="/when-to-enter",
        )

    # ─── M2: Search page ─────────────────────────────────────────────

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = "", type: str = "") -> HTMLResponse:
        conn = _get_conn()
        try:
            rows: list[dict] = []
            tickers: list[str] = []
            if q.strip():
                rows = ingest.search_mentions(conn, q, source_filter=type or None, limit=300)
                tickers = ingest.search_distinct_tickers(conn, q)
            # Group by ticker for the results display
            by_ticker: dict[str, list[dict]] = {}
            by_date: dict[str, list[dict]] = {}
            by_source: dict[str, list[dict]] = {}
            for r in rows:
                by_ticker.setdefault(r["ticker"], []).append(r)
                by_date.setdefault(str(r["date"]), []).append(r)
                by_source.setdefault(r["source"], []).append(r)
            latest = ingest.latest_briefing_date(conn)
            return _render(
                request,
                "search.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    q=q, source_filter=type,
                    results=rows,
                    by_ticker=by_ticker,
                    by_date=by_date,
                    by_source=by_source,
                    distinct_tickers=tickers,
                    topbar=None,
                ),
            )
        finally:
            conn.close()

    # ─── /refresh/notify — external ping to re-materialize ─────────
    # The Telegram bot (or any external caller) pings this endpoint
    # after a briefing is delivered to disk. We force a fresh DuckDB
    # materialization so the next user page load lands on hot data
    # instead of relying on the auto-detect mtime probe (task #43).
    # No auth by design — this webapp is single-user localhost-only.
    # Idempotent + fast: if another refresh is in flight, we just skip.
    @app.post("/refresh/notify")
    async def refresh_notify() -> JSONResponse:
        """Force webapp re-materialization. Called by the Telegram bot
        after a briefing finishes. Also OK to call manually via curl.

        Returns:
            {"status": "ok" | "already_running", "materialized_at": ISO ts,
             "snapshots_loaded": int}
        """
        try:
            ingest.refresh_projections()
            clear_recs_cache()
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
        conn = _get_conn()
        latest_date = None
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM portfolio_timeseries"
            ).fetchone()
            snapshots_loaded = int(row[0]) if row and row[0] else 0
            latest_date = ingest.latest_briefing_date(conn)
        except Exception:
            snapshots_loaded = -1
        finally:
            conn.close()
        return JSONResponse({
            "status": "ok",
            "materialized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "snapshots_loaded": snapshots_loaded,
            "latest_date": latest_date,
            "checksum": ingest.briefing_checksum(latest_date) if latest_date else None,
        })

    # ─── E*TRADE auth status ─────────────────────────────────────────
    # Surfaces the token freshness so the UI can pop a re-auth CTA when
    # the token has aged past midnight ET (hard expiry — requires a fresh
    # browser OAuth handshake per etrade_auth.py). Read-only: reads the
    # token file's mtime; never touches OAuth secrets.

    @app.get("/etrade/auth-status")
    async def etrade_auth_status() -> JSONResponse:
        """Report E*TRADE OAuth token freshness.

        Returns:
          - status: "fresh" | "idle_stale" | "hard_expired" | "missing" | "unknown"
          - token_age_hours: float | None
          - hint: str  (human-readable next step)
          - reauth_url_available: bool
        """
        import os
        from datetime import datetime, timedelta, timezone
        token_path_env = os.environ.get("PORTFOLIO_BRIEFING_TOKEN_FILE")
        candidates = []
        if token_path_env:
            candidates.append(Path(token_path_env))
        candidates.append(Path.home() / ".config" / "portfolio-briefing" / "etrade_tokens.json")
        token_path = next((p for p in candidates if p.exists()), None)

        if not token_path:
            return JSONResponse({
                "status": "missing",
                "token_age_hours": None,
                "hint": "No E*TRADE token file found. Run the CLI once: `uv run python "
                        "skills/daily-portfolio-briefing/scripts/etrade_auth.py "
                        "--authenticate` to complete the OAuth handshake.",
                "reauth_url_available": True,
            })

        try:
            mtime = datetime.fromtimestamp(token_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return JSONResponse({
                "status": "unknown",
                "token_age_hours": None,
                "hint": "Token file exists but couldn't be stat'd.",
                "reauth_url_available": False,
            })

        age = datetime.now(timezone.utc) - mtime
        age_h = age.total_seconds() / 3600.0

        # E*TRADE tokens: 2h idle timeout (renewable), midnight ET hard expiry.
        # Simplify: > 24h since last mtime → hard-expired (needs browser); 2-24h
        # → idle_stale (renewable via non-browser call); < 2h → fresh.
        if age_h > 24:
            return JSONResponse({
                "status": "hard_expired",
                "token_age_hours": round(age_h, 1),
                "hint": "Token past midnight-ET hard expiry — a browser OAuth handshake "
                        "is required. Run `uv run python skills/daily-portfolio-briefing/"
                        "scripts/etrade_auth.py --authenticate` in a terminal.",
                "reauth_url_available": True,
            })
        if age_h > 2:
            return JSONResponse({
                "status": "idle_stale",
                "token_age_hours": round(age_h, 1),
                "hint": "Token is idle (past 2h) but renewable. Kicking off /refresh "
                        "will trigger auto-renewal; no browser needed unless renewal fails.",
                "reauth_url_available": False,
            })
        return JSONResponse({
            "status": "fresh",
            "token_age_hours": round(age_h, 2),
            "hint": "Token is fresh — refresh should proceed cleanly.",
            "reauth_url_available": False,
        })

    # ─── M2: Refresh endpoints (subprocess + SSE) ────────────────────

    @app.post("/refresh")
    async def refresh_post(request: Request) -> JSONResponse:
        """Kick off a refresh. Single-flight: returns 409 if one's running.

        Body always JSON. On 202: {job_id, status: 'started'}.
        On 409: {job_id, status: 'already_running'}.
        """
        def _on_complete(state: jobs.JobState) -> None:
            # Re-materialize so the dashboard picks up the new snapshot
            try:
                ingest.refresh_projections()
                clear_recs_cache()
            except Exception:
                # Errors are surfaced in the SSE stream already
                pass

        state, started = jobs.start_refresh_job(on_complete=_on_complete)
        if not started:
            return JSONResponse(
                {"job_id": state.id, "status": "already_running",
                 "started_at": state.started_at.isoformat()},
                status_code=409,
            )
        return JSONResponse(
            {"job_id": state.id, "status": "started",
             "started_at": state.started_at.isoformat()},
            status_code=202,
        )

    @app.get("/jobs/{job_id}")
    async def job_status(job_id: str) -> JSONResponse:
        state = jobs.get_job(job_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}")
        return JSONResponse(state.to_dict())

    @app.get("/events")
    async def events(job_id: str) -> StreamingResponse:
        """SSE stream of one job's logs. Emits `ingest_complete` and
        closes when the job finishes."""
        if jobs.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}")
        return StreamingResponse(
            jobs.sse_stream(job_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ─── M2: HTMX fragments ──────────────────────────────────────────

    @app.get("/fragment/ticker_card/{ticker}", response_class=HTMLResponse)
    async def ticker_card(request: Request, ticker: str) -> HTMLResponse:
        """Mini popup card for a ticker — 30d sparkline, Parkev chip,
        latest 3 actions involving this ticker.

        Returned as a self-contained HTML fragment. The HTMX swap target
        renders this into a popover near the hovered element.
        """
        ticker = ticker.upper()
        conn = _get_conn()
        try:
            latest = ingest.latest_briefing_date(conn)
            # Latest 3 mentions
            all_mentions = ingest.briefing_mentions_for_ticker(conn, ticker)
            mentions = all_mentions[:5]
            # Latest price snapshot
            row = conn.execute(
                """
                SELECT date, price
                FROM positions_timeseries
                WHERE upper(symbol) = upper(?)
                  AND assetType != 'OPTION'
                  AND price IS NOT NULL
                ORDER BY date DESC LIMIT 1
                """,
                [ticker],
            ).fetchone()
            current_price = float(row[1]) if row else None
            current_date = str(row[0]) if row else None
            return _render(
                request,
                "fragment_ticker_card.html",
                _ctx(
                    request,
                    briefing_date=latest,
                    ticker=ticker,
                    current_price=current_price,
                    current_date=current_date,
                    mentions=mentions,
                ),
            )
        finally:
            conn.close()

    @app.get("/fragment/counterpoint/{date}/{key:path}", response_class=HTMLResponse)
    async def counterpoint_fragment(request: Request, date: str, key: str) -> HTMLResponse:
        """Return the counterpoint text for one (date, action_key)."""
        # Decode trailing slash in `key` since it can include `:` like `CLOSE:NVDA_PUT_180_20260918`
        # FastAPI's `path:path` lets the colon through. Try ident + kind fallbacks too.
        if ":" in key:
            kind, _, ident = key.partition(":")
        else:
            kind, ident = None, key
        cp_text = counterpoints.counterpoint_for_action(date, key, ident=ident, kind=kind)
        if not cp_text:
            return HTMLResponse("", status_code=200)  # empty disclosure → renders nothing
        return _render(
            request,
            "fragment_counterpoint.html",
            _ctx(request, briefing_date=date, counterpoint_text=cp_text),
        )

    @app.get("/fragment/refresh-status", response_class=PlainTextResponse)
    async def refresh_status() -> PlainTextResponse:
        s = ingest.get_last_status()
        if not s:
            return PlainTextResponse("never")
        return PlainTextResponse(ingest.time_since(s.last_ingest_at))

    # ─── Chart endpoints (Plotly JSON) ───────────────────────────────

    @app.get("/charts/nlv.json")
    async def chart_nlv(days: int = 90) -> JSONResponse:
        conn = _get_conn()
        try:
            rows = ingest.briefing_summary_rows(conn)
            return JSONResponse(build_nlv_figure(rows, days=days))
        finally:
            conn.close()

    @app.get("/charts/coverage.json")
    async def chart_coverage(days: int = 90) -> JSONResponse:
        conn = _get_conn()
        try:
            return JSONResponse(build_coverage_figure(conn, days=days))
        finally:
            conn.close()

    @app.get("/charts/expiration-ladder.json")
    async def chart_expiration() -> JSONResponse:
        conn = _get_conn()
        try:
            return JSONResponse(build_expiration_ladder_figure(conn))
        finally:
            conn.close()

    @app.get("/charts/parkev/{ticker}.json")
    async def chart_parkev(ticker: str) -> JSONResponse:
        conn = _get_conn()
        try:
            history = ingest.parkev_history_for_ticker(conn, ticker.upper())
            return JSONResponse(build_parkev_timeline(history, ticker))
        finally:
            conn.close()

    @app.get("/charts/sparkline/{ticker}.json")
    async def chart_sparkline(ticker: str, days: int = 30) -> JSONResponse:
        conn = _get_conn()
        try:
            return JSONResponse(build_ticker_sparkline(conn, ticker.upper(), days=days))
        finally:
            conn.close()

    @app.get("/charts/benchmark.json")
    async def chart_benchmark(date: str = "") -> JSONResponse:
        """Task #16 — portfolio vs SPY cumulative-return chart. Series comes
        from the briefing JSON's benchmark_report (no market-data fetch here).
        Missing/older briefings render an empty-figure placeholder, never 500."""
        target = date
        if not target:
            conn = _get_conn()
            try:
                target = ingest.latest_briefing_date(conn) or ""
            finally:
                conn.close()
        raw = ingest.load_briefing_json(target) if target else None
        br = (raw or {}).get("benchmark_report") or {}
        bench = br.get("benchmark") or {}
        return JSONResponse(build_benchmark_figure(
            bench.get("series"),
            benchmark_ticker=bench.get("benchmark_ticker") or "SPY",
        ))

    @app.get("/charts/program-edge.json")
    async def chart_program_edge() -> JSONResponse:
        """💪 Program Edge sparkline — cumulative gap (real − ghost) series
        from the pipeline's ghost_portfolio.json state file. Missing/short
        history renders an empty-figure placeholder, never 500."""
        try:
            points = program_edge.series_points(program_edge.load_state())
        except Exception:
            points = []
        return JSONResponse(build_program_edge_figure(points))

    # ─── Health check ─────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        s = ingest.get_last_status()
        return JSONResponse({
            "status": "ok",
            "snapshots_loaded": s.snapshot_count if s else 0,
            "expected": s.expected_count if s else 0,
            "last_ingest": s.last_ingest_at.isoformat() if s else None,
            "running_job_id": (jobs.get_running_job().id
                               if jobs.get_running_job() else None),
        })


# Module-level app for `uvicorn app.main:app`.
app = create_app()
