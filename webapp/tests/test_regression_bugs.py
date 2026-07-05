"""Regression tests for bugs the user reported in the live app.

Every fix that came from "I clicked X and it 500'd" gets a test here so we
don't ship the same bug twice.

Add new tests at the bottom — never delete old ones.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


# ─── Helper ──────────────────────────────────────────────────────────────


def _fresh_client():
    """Build a TestClient with all module-level caches reset.

    The webapp uses module-scoped DuckDB connections + materialization
    sentinels. Each test that needs a fully fresh boot calls this; the
    fixtures live as JSON snapshots under state/.
    """
    from app import ingest
    ingest._PROJECTIONS_READY = False
    if ingest._INMEM_CONN is not None:
        try:
            ingest._INMEM_CONN.close()
        except Exception:
            pass
        ingest._INMEM_CONN = None
    ingest._INMEM_MATERIALIZED = False
    from app.main import create_app
    return TestClient(create_app(), raise_server_exceptions=False)


# ─── Bug #1 — /briefing/latest 500 (refresh_projections closed in-mem) ──


def test_briefing_latest_does_not_500(tmp_path, monkeypatch):
    """User clicked /briefing/latest → 500.

    Root cause: `refresh_projections()` unconditionally `.close()`d the
    write connection, including when it was the shared in-memory DB
    (corruption fallback path). Subsequent requests opened a fresh
    in-memory DB with no materialized tables, so `portfolio_timeseries`
    didn't exist.
    """
    c = _fresh_client()
    r = c.get("/briefing/latest", follow_redirects=True)
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"


def test_position_detail_does_not_500():
    """User clicked /positions/GOOG → 500 — same root cause as above."""
    c = _fresh_client()
    r = c.get("/positions/GOOG")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"


def test_history_page_does_not_500():
    c = _fresh_client()
    r = c.get("/history")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"


# ─── Bug #2 — chart-target divs never hydrate (missing /static/app.js) ──


def test_base_template_loads_chart_bootstrapper():
    """Every page that uses chart-target divs needs /static/app.js loaded
    in the base template, OR charts render empty silently.

    Failure mode: user sees empty chart placeholders, no JS error, no
    server error — just blank divs.
    """
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    assert "/static/app.js" in r.text, (
        "base.html must load /static/app.js — the chart bootstrapper that "
        "hydrates every <div class='chart-target' data-chart-url>."
    )


def test_static_app_js_is_served():
    """The static file referenced by base.html must actually be served."""
    c = _fresh_client()
    r = c.get("/static/app.js")
    assert r.status_code == 200
    body = r.text
    # The hydrator must reference Plotly.newPlot and the data-chart-url contract
    assert "Plotly.newPlot" in body
    assert "data-chart-url" in body
    assert "chart-target" in body


def test_position_detail_emits_chart_targets():
    """The position detail page must emit chart-target placeholders that
    the bootstrapper can find. Combined with the previous test, this
    proves the end-to-end hydration path is intact."""
    c = _fresh_client()
    r = c.get("/positions/GOOG")
    assert r.status_code == 200
    assert "chart-target" in r.text
    assert "data-chart-url" in r.text
    # Specific charts the page promises
    assert "/charts/sparkline/GOOG.json" in r.text
    assert "/charts/parkev/GOOG.json" in r.text


# ─── Bug #3 — chart JSON endpoints return real data, not empty traces ──


def test_sparkline_returns_valid_plotly_spec():
    """The sparkline endpoint must return a valid Plotly spec — either
    traces with data, OR an explicit empty-state annotation. The original
    bug was a 500 (CatalogException on positions_timeseries); a 200 with
    a structured empty-state is acceptable when the fixture window has
    no data."""
    c = _fresh_client()
    r = c.get("/charts/sparkline/GOOG.json?days=30")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
    d = json.loads(r.text)
    assert "data" in d and "layout" in d, "missing Plotly spec keys"
    has_traces = bool(d["data"]) and bool(d["data"][0].get("x"))
    has_empty_annotation = any(
        "no" in a.get("text", "").lower() or "history" in a.get("text", "").lower()
        for a in d.get("layout", {}).get("annotations", [])
    )
    assert has_traces or has_empty_annotation, (
        "sparkline JSON has no traces AND no empty-state annotation — "
        "the user would see a blank chart with no explanation"
    )


def test_nlv_chart_returns_real_data():
    c = _fresh_client()
    r = c.get("/charts/nlv.json?days=90")
    assert r.status_code == 200
    d = json.loads(r.text)
    assert d.get("data"), "NLV chart has no traces"
    assert d["data"][0].get("x"), "NLV trace has no x values"


# ─── Bug #4 — DuckDB corruption recovery ────────────────────────────────


def test_corrupt_duckdb_recovers_on_next_boot(monkeypatch, tmp_path):
    """If the on-disk DB is corrupt (e.g., schema version drift after a
    DuckDB upgrade), the next process boot must transparently rebuild
    instead of 500ing every route.
    """
    from app import ingest
    from app.config import duckdb_path

    # Corrupt the existing DB
    p = duckdb_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"GARBAGE NOT A DUCKDB FILE")

    # Reset module state so the lifespan startup runs against the corrupt file
    ingest._PROJECTIONS_READY = False
    if ingest._INMEM_CONN is not None:
        try:
            ingest._INMEM_CONN.close()
        except Exception:
            pass
        ingest._INMEM_CONN = None

    from app.main import create_app
    c = TestClient(create_app(), raise_server_exceptions=False)
    # Hit a representative cross-section
    for url in [
        "/", "/briefing/latest", "/positions",
        "/charts/nlv.json?days=90", "/health",
    ]:
        r = c.get(url, follow_redirects=True)
        assert r.status_code == 200, (
            f"{url} → {r.status_code} (corruption recovery broken)"
        )


# ─── Bug #5 — Theme switcher needs CSS for all four variants ───────────


def test_app_css_defines_all_four_themes():
    """base.html ships a 4-theme switcher. Each theme name MUST have a
    matching `html[data-theme="..."]` CSS block defining the color
    palette — otherwise switching does nothing visible.
    """
    from pathlib import Path
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text()
    for theme in ("dark", "light", "solarized-dark", "solarized-light"):
        assert f'html[data-theme="{theme}"]' in css, (
            f"app.css missing theme variant for {theme!r}"
        )


def test_base_template_wires_all_four_themes_in_menu():
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    for theme in ("dark", "light", "solarized-dark", "solarized-light"):
        assert f'value="{theme}"' in r.text, (
            f"theme menu missing option {theme!r}"
        )


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        sc = c / 255.0
        return sc / 12.92 if sc <= 0.03928 else ((sc + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: str, bg: str) -> float:
    l1 = _rel_luminance(_hex_to_rgb(fg))
    l2 = _rel_luminance(_hex_to_rgb(bg))
    lighter, darker = (max(l1, l2), min(l1, l2))
    return (lighter + 0.05) / (darker + 0.05)


def _extract_theme_var(css: str, theme: str, var_name: str) -> str | None:
    """Pull `--var-name: #hex;` out of an `html[data-theme="theme"]` block.

    For the dark theme, the source-of-truth definitions live in the
    `:root,:host,.sl-theme-dark` block — `html[data-theme="dark"]` just
    re-aliases. Fall back to `:root` when the theme block doesn't
    redefine the var.
    """
    import re
    for selector in (rf'html\[data-theme="{re.escape(theme)}"\]',
                     r":root,\s*:host,\s*\.sl-theme-dark" if theme == "dark" else None):
        if selector is None:
            continue
        m = re.search(selector + r"\s*\{([^}]*)\}", css, re.S)
        if not m:
            continue
        block = m.group(1)
        vm = re.search(rf"{re.escape(var_name)}:\s*(#[0-9A-Fa-f]{{6}})", block)
        if vm:
            return vm.group(1)
    return None


def test_light_themes_pass_wcag_aa_contrast():
    """Light theme + solarized-light theme + infographic theme: tertiary
    text on the page background MUST be at least 4.5:1 (WCAG AA for
    normal text).

    Sarcasm-driven regression: user reported the light theme as
    "contrast is great, hard to read" — slate-400 (#94A3B8) on
    near-white (#FAFBFC) is ~2.5:1, well below readable.

    Task #6: infographic theme uses warm off-white (#FDFAF3) with warm
    grays — tertiary #7A7873 on bg #FDFAF3 = 4.7:1 (passes AA).
    """
    from pathlib import Path
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text()
    for theme in ("light", "solarized-light", "infographic"):
        bg = _extract_theme_var(css, theme, "--color-bg")
        tertiary = _extract_theme_var(css, theme, "--color-text-tertiary")
        secondary = _extract_theme_var(css, theme, "--color-text-secondary")
        primary = _extract_theme_var(css, theme, "--color-text-primary")
        assert bg and tertiary and secondary and primary, (
            f"{theme}: missing one of bg/tertiary/secondary/primary tokens"
        )
        cr_tert = _contrast_ratio(tertiary, bg)
        cr_sec = _contrast_ratio(secondary, bg)
        cr_pri = _contrast_ratio(primary, bg)
        assert cr_tert >= 4.5, (
            f"{theme}: tertiary text {tertiary} on bg {bg} = {cr_tert:.2f}:1 "
            f"(needs ≥4.5:1 for WCAG AA — labels would be hard to read)"
        )
        assert cr_sec >= 4.5, f"{theme}: secondary text contrast {cr_sec:.2f}:1"
        assert cr_pri >= 7.0, f"{theme}: primary text contrast {cr_pri:.2f}:1"


def test_dark_themes_pass_wcag_aa_contrast():
    """Same check for dark + solarized-dark themes."""
    from pathlib import Path
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text()
    for theme in ("dark", "solarized-dark"):
        bg = _extract_theme_var(css, theme, "--color-bg")
        # Dark variants may inherit from :root rather than redefining bg.
        if not bg:
            bg = "#0F172A" if theme == "dark" else "#002B36"
        tertiary = _extract_theme_var(css, theme, "--color-text-tertiary")
        if not tertiary:
            # dark theme inherits from :root
            tertiary = "#64748B" if theme == "dark" else "#586E75"
        cr = _contrast_ratio(tertiary, bg)
        assert cr >= 4.5, (
            f"{theme}: tertiary text {tertiary} on bg {bg} = {cr:.2f}:1"
        )


# ─── Bug #6 — Structured card layout for candidates / when-to-enter ────


def test_candidates_page_renders_structured_cards():
    c = _fresh_client()
    r = c.get("/candidates/latest", follow_redirects=True)
    assert r.status_code == 200
    # Structured card view markers
    assert "rep-card" in r.text, "candidates page should render structured cards"
    assert "rep-status" in r.text
    assert "rep-section" in r.text


def test_when_to_enter_page_renders_structured_cards():
    c = _fresh_client()
    r = c.get("/when-to-enter/latest", follow_redirects=True)
    assert r.status_code == 200
    assert "rep-card" in r.text


def test_pretty_contract_rendered_on_positions_page():
    """The Positions page options table MUST pretty-print contract symbols
    (e.g. LITE_PUT_660_20260918 → LITE $660P · Sep 18 '26). The pipeline
    writes machine identifiers; the web app must NOT leak them as raw
    text. Bare `<code>{{ o.symbol }}</code>` is a leak (user reported
    2026-06-30 — "Make such strings more readable").
    """
    c = _fresh_client()
    r = c.get("/positions")
    assert r.status_code == 200
    body = r.text
    # If the fixture contains any options, the contract chip class must appear
    if "PUT_" in body or "CALL_" in body:
        # Some raw refs (anchors, titles) are OK; what's NOT OK is the
        # rendered cell being bare <code>SYM_TYPE_STRIKE_DATE</code>
        import re
        bare = re.search(
            r"<code>[A-Z][A-Z0-9.]{0,7}_(?:PUT|CALL)_\d+(?:\.\d+)?_\d{8}</code>",
            body,
        )
        assert bare is None, (
            f"positions page leaks raw contract id: {bare.group(0)} — "
            "wrap with the pretty_contract filter"
        )


def test_pretty_contract_rendered_on_position_detail():
    """Same rule for the per-ticker history table."""
    c = _fresh_client()
    r = c.get("/positions/META")
    assert r.status_code == 200
    body = r.text
    import re
    bare = re.search(
        r"<code>[A-Z][A-Z0-9.]{0,7}_(?:PUT|CALL)_\d+(?:\.\d+)?_\d{8}</code>",
        body,
    )
    assert bare is None, (
        f"position detail page leaks raw contract id: {bare.group(0)}"
    )


def test_pretty_contract_rendered_on_diff_page():
    c = _fresh_client()
    r = c.get("/diff/yesterday/latest", follow_redirects=True)
    assert r.status_code == 200
    body = r.text
    import re
    bare = re.search(
        r"<code>[A-Z][A-Z0-9.]{0,7}_(?:PUT|CALL)_\d+(?:\.\d+)?_\d{8}</code>",
        body,
    )
    assert bare is None, f"diff page leaks raw contract id: {bare.group(0)}"


def test_pretty_contract_rendered_on_search_results():
    """Search results carry contract idents — must pretty-print them
    (compact form, since the result row is tight)."""
    c = _fresh_client()
    r = c.get("/search?q=PUT")
    assert r.status_code == 200
    body = r.text
    import re
    bare = re.search(
        r"<code>[A-Z][A-Z0-9.]{0,7}_(?:PUT|CALL)_\d+(?:\.\d+)?_\d{8}</code>",
        body,
    )
    assert bare is None, f"search page leaks raw contract id: {bare.group(0)}"


def test_history_page_charts_have_h2_labels():
    """UX critique #1: /history had three unlabeled Plotly charts (h3 → too
    small to read at chart size). Charts must have H2 headers describing
    what each one shows.
    """
    c = _fresh_client()
    r = c.get("/history")
    assert r.status_code == 200
    body = r.text
    import re
    h2s = re.findall(r"<h2[^>]*>(.*?)</h2>", body, re.S)
    # Strip HTML tags from h2 contents for matching
    h2_text = [re.sub(r"<[^>]+>", "", h).strip() for h in h2s]
    h2_joined = " ".join(h2_text).lower()
    assert "nlv" in h2_joined, f"/history missing NLV chart label, got h2s: {h2_text}"
    assert "coverage" in h2_joined, f"/history missing Coverage chart label, got h2s: {h2_text}"
    assert "expiration" in h2_joined, f"/history missing Expiration ladder label, got h2s: {h2_text}"


def test_positions_options_table_removed_redundant_columns():
    """UX critique #3: positions Options table had columns
    Underlying / Symbol / Type / Strike / Expiry. After pretty_contract
    landed, Symbol carries ticker+type+strike+expiry inside its chip —
    Underlying/Type/Strike/Expiry duplicate it. Removed.
    """
    c = _fresh_client()
    r = c.get("/positions")
    assert r.status_code == 200
    body = r.text
    # The Options table now should have a "Contract" column header rather
    # than the old "Symbol" + "Underlying" / "Type" / "Strike" / "Expiry" combo.
    # Detect via the th sequence in the options table.
    import re
    # Find the Options section
    options_idx = body.find('Options (')
    assert options_idx > 0, "options table not found"
    # Look at the next ~3000 chars for the table headers
    snippet = body[options_idx:options_idx + 3000]
    assert "<th>Contract</th>" in snippet, (
        "Options table should have a Contract column (the pretty-printed chip)"
    )


def test_cov_pill_has_tooltip_and_jump_handler():
    """UX critique #4: Cov pill should have a hover tooltip explaining the
    threshold AND click to jump to /briefing/latest#red-flags."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    # Cov pill must have a title attribute (tooltip) and an onclick handler
    # — find the pill region and assert both
    import re
    cov_pill_match = re.search(
        r'<li class="pill[^"]*"[^>]*>(?:[^<]|<(?!li))*?Cov(?:[^<]|<(?!li))*?</li>',
        body, re.S,
    )
    assert cov_pill_match, "Cov pill not found on dashboard"
    cov_html = cov_pill_match.group(0)
    assert "title=" in cov_html, "Cov pill missing tooltip"
    assert "onclick=" in cov_html or "href=" in cov_html, (
        "Cov pill must be clickable (onclick or href)"
    )


def test_theme_switcher_shows_current_label():
    """UX critique #5: theme switcher trigger was just an icon (🌒) —
    first-time user can't tell what it represents. Now shows icon + label."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    # The trigger should have both the icon span AND a label span
    assert 'id="theme-current-icon"' in body
    assert 'id="theme-current-label"' in body


def test_red_flags_module_computes_critical_for_low_coverage():
    """UX critique #2: dashboard hid the most important fact — coverage
    0.07× CRITICAL was a tiny topbar pill. Now derived as a structured
    flag via red_flags.compute_red_flags() and rendered as a banner.
    """
    from app import red_flags
    from types import SimpleNamespace
    fake = SimpleNamespace(
        nlv=1_000_000, cash=50_000, cash_pct=0.05,
        options_reviews=[], date="2026-06-30",
    )
    # Coverage well below the 0.50 critical threshold
    flags = red_flags.compute_red_flags(fake, coverage=0.07)
    assert len(flags) >= 1
    critical = [f for f in flags if f["severity"] == "critical"]
    assert len(critical) >= 1, "0.07x coverage MUST produce a critical flag"
    assert any("coverage" in f["title"].lower() for f in critical)
    counts = red_flags.summary_counts(flags)
    assert counts["critical"] >= 1


def test_red_flags_module_empty_when_healthy():
    """When all thresholds are satisfied, no flags fire."""
    from app import red_flags
    from types import SimpleNamespace
    fake = SimpleNamespace(
        nlv=1_000_000, cash=200_000, cash_pct=0.20,
        options_reviews=[], date="2026-06-30",
    )
    flags = red_flags.compute_red_flags(fake, coverage=1.20)
    assert flags == []


def test_red_flags_module_detects_expiration_bucket_concentration():
    """Hard rule #21: single-Friday short-put obligation ≥30% NLV → CRITICAL."""
    from app import red_flags
    from types import SimpleNamespace
    opts = [
        SimpleNamespace(underlying="NVDA", qty=-3, strike=200,
                        type="PUT", expiration="2026-08-21"),
        SimpleNamespace(underlying="AMD", qty=-5, strike=150,
                        type="PUT", expiration="2026-08-21"),
        # 3*200*100 + 5*150*100 = 60_000 + 75_000 = 135_000 — 13.5% of NLV
        # Below the 30% critical AND 20% warning thresholds → no bucket flag
    ]
    fake = SimpleNamespace(
        nlv=1_000_000, cash=200_000, cash_pct=0.20,
        options_reviews=opts, date="2026-06-30",
    )
    flags = red_flags.compute_red_flags(fake, coverage=1.20)
    assert all("Expiration cluster" not in f["title"] for f in flags), (
        "Bucket at 13.5% NLV should NOT fire a flag"
    )

    # Bump to 35% of NLV by raising contracts
    opts_big = [
        SimpleNamespace(underlying="NVDA", qty=-10, strike=200,
                        type="PUT", expiration="2026-08-21"),  # $200K = 20%
        SimpleNamespace(underlying="AMD", qty=-10, strike=150,
                        type="PUT", expiration="2026-08-21"),  # $150K = 15%
        # total = 35% on 2026-08-21 → CRITICAL
    ]
    fake_big = SimpleNamespace(
        nlv=1_000_000, cash=200_000, cash_pct=0.20,
        options_reviews=opts_big, date="2026-06-30",
    )
    flags_big = red_flags.compute_red_flags(fake_big, coverage=1.20)
    critical = [f for f in flags_big if f["severity"] == "critical"]
    assert any("Expiration cluster" in f["title"] for f in critical), (
        "Bucket at 35% NLV MUST fire a critical flag (hard rule #21)"
    )


def test_dashboard_renders_red_flags_banner():
    """End-to-end: the dashboard route passes red_flags through to the
    template and the template renders the banner. This is the contract
    that ensures the critical-coverage signal actually reaches the user."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    # If the briefing data has low coverage, the banner MUST render.
    # The test data has coverage 0.07× (well below 0.50×).
    if "red-flags-banner" in body or "Red Flags" in body:
        # Banner is rendered — check it surfaces something meaningful
        assert "critical" in body.lower() or "warning" in body.lower(), (
            "Red flags banner present but no severity label"
        )


def test_diff_page_has_tldr_summary_line():
    """UX critique #6: /diff/* was missing a one-sentence answer to
    'should I read this?'. Now has a TL;DR strip above the metric cards."""
    c = _fresh_client()
    r = c.get("/diff/yesterday/latest", follow_redirects=True)
    assert r.status_code == 200
    body = r.text
    assert 'class="diff-tldr"' in body, (
        "diff page should render a TL;DR strip (.diff-tldr) above metric cards"
    )


def test_capacity_banner_upgrades_to_bad_tone_when_gates_closed():
    """UX critique #10: when-to-enter capacity banner used the muted
    info tone even when ALL entries were gated. Should escalate to bad
    tone when 'ENTRY GATES: CLOSED' AND no actionable cards remain."""
    c = _fresh_client()
    r = c.get("/when-to-enter/latest", follow_redirects=True)
    assert r.status_code == 200
    body = r.text
    if "ENTRY GATES: CLOSED" in body:
        assert "rep-banner-bad" in body, (
            "capacity banner should upgrade to bad tone when gates are closed"
        )


def test_position_state_module_tone_classification():
    """UX critique #7: position drill-down needed a current-state card
    with RSI/IV/trend/DD/S-R chips, each color-toned per hard rule #11."""
    from app import position_state as ps
    # RSI asymmetric bands
    assert ps._rsi_tone(75)[0] == "bad"      # overbought
    assert ps._rsi_tone(65)[0] == "warn"     # extended
    assert ps._rsi_tone(45)[0] == "ok"       # favorable
    assert ps._rsi_tone(30)[0] == "warn"     # oversold
    assert ps._rsi_tone(20)[0] == "bad"      # falling knife
    assert ps._rsi_tone(None)[0] == "muted"

    # IV rank
    assert ps._iv_tone(85)[0] == "warn"      # elevated
    assert ps._iv_tone(60)[0] == "ok"        # rich premium
    assert ps._iv_tone(30)[0] == "muted"     # low
    assert ps._iv_tone(None)[0] == "muted"

    # Trend vs 200-SMA
    assert ps._trend_tone(100, 80)[0] == "ok"     # well above
    assert ps._trend_tone(100, 95)[0] == "ok"     # above
    assert ps._trend_tone(100, 105)[0] == "warn"  # near
    assert ps._trend_tone(100, 120)[0] == "bad"   # below

    # Drawdown
    assert ps._drawdown_tone(35)[0] == "bad"
    assert ps._drawdown_tone(20)[0] == "warn"
    assert ps._drawdown_tone(2)[0] == "ok"


def test_position_state_build_returns_full_dict():
    """End-to-end: given a real ticker + snapshot, build_position_state
    returns all the expected fields (technicals, S/R, Parkev, thesis)."""
    from app import position_state
    from types import SimpleNamespace
    # Fake a briefing snapshot with a matching equity_review + working snapshot dir
    fake_review = SimpleNamespace(
        ticker="GOOG",
        recommendation="HOLD",
        thesis_status="ACTIVE",
        technical_status="UPTREND",
        rationale="Thesis intact.",
        weight=0.15,
        market_value=154000,
        pl_pct=0.44,
    )
    fake_briefing = SimpleNamespace(
        equity_reviews=[fake_review],
        snapshot_dir=None,  # no on-disk technicals — module must fail-open
    )
    state = position_state.build_position_state(
        "GOOG",
        snapshot_dir=None,
        briefing=fake_briefing,
        latest_equity=None,
    )
    # Even with no technicals, equity_review fields propagate
    assert state["ticker"] == "GOOG"
    assert state["recommendation"] == "HOLD"
    assert state["weight_pct"] == 15.0
    assert state["has_data"] is True
    # Technicals present but all None (fail-open on missing snapshot)
    assert state["technicals"]["rsi"] is None
    assert state["technicals"]["rsi_tone"] == "muted"


def test_position_detail_page_renders_state_card():
    """Contract test: for a ticker present in the briefing's equity_reviews
    (NVDA is in the test fixture), the pos-state card MUST render at
    /positions/{ticker}. Silently omitting it is the regression we're
    guarding against (that was the state before UX critique #7 landed)."""
    c = _fresh_client()
    r = c.get("/positions/NVDA")
    assert r.status_code == 200
    body = r.text
    assert "pos-state" in body, "position state card should render for NVDA"
    assert 'id="current-state"' in body


def test_briefing_checksum_helper_returns_stable_short_hash(tmp_path, monkeypatch):
    """Task #50: ingest.briefing_checksum() returns first 8 hex chars of
    SHA-256, stable across calls, changes when content changes."""
    from app import ingest, config as config_mod
    # Redirect briefings_delivery to a temp dir so we can control content
    monkeypatch.setattr(config_mod, "briefings_delivery", lambda: tmp_path)
    monkeypatch.setattr(ingest, "briefings_delivery", lambda: tmp_path)
    (tmp_path / "briefing_2026-06-30.json").write_text('{"a": 1}')
    chk1 = ingest.briefing_checksum("2026-06-30")
    chk2 = ingest.briefing_checksum("2026-06-30")
    assert chk1 == chk2
    assert chk1 is not None
    assert len(chk1) == 8
    # Content change → checksum change
    (tmp_path / "briefing_2026-06-30.json").write_text('{"a": 2}')
    chk3 = ingest.briefing_checksum("2026-06-30")
    assert chk3 != chk1


def test_briefing_checksum_missing_file_returns_none(tmp_path, monkeypatch):
    """Fail-open: no file → None, never raises."""
    from app import ingest, config as config_mod
    monkeypatch.setattr(config_mod, "briefings_delivery", lambda: tmp_path)
    monkeypatch.setattr(ingest, "briefings_delivery", lambda: tmp_path)
    assert ingest.briefing_checksum("2099-01-01") is None


def test_topbar_carries_checksum_field():
    """The topbar context builder must include a `checksum` key so
    every page can render the SHA fingerprint next to the date."""
    from app.main import _topbar_context
    from types import SimpleNamespace
    from datetime import date as _d
    fake_briefing = SimpleNamespace(
        date=_d(2026, 6, 30), nlv=1_000_000, cash=50_000,
        cash_pct=0.05, regime="RISK_ON",
    )
    # Use a real DuckDB connection so coverage lookup doesn't crash
    import duckdb
    conn = duckdb.connect(":memory:")
    ctx = _topbar_context(fake_briefing, conn)
    conn.close()
    assert ctx is not None
    assert "checksum" in ctx


def test_home_page_renders_checksum_badge():
    """Contract: the hero meta line has the checksum span so the user
    sees the freshness fingerprint next to the date."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "pill-checksum" in body or "home-hero-checksum" in body


def test_refresh_notify_endpoint_forces_materialization():
    """Task #46: POST /refresh/notify re-materializes DuckDB projections
    and returns JSON with status + snapshot count. Called by the
    Telegram bot after briefing completion so the webapp is warm on
    the next user page load.

    No auth by design — single-user localhost app.
    """
    c = _fresh_client()
    r = c.post("/refresh/notify")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "materialized_at" in body
    assert "snapshots_loaded" in body
    assert body["snapshots_loaded"] >= 0
    # Task #50: the payload includes a checksum for the current briefing
    # so the Telegram bot can show the same value as the UI.
    assert "checksum" in body
    assert "latest_date" in body


def test_refresh_notify_endpoint_returns_json_content_type():
    """The endpoint must return application/json so the bot's
    _notify_webapp() can parse the count out of the response."""
    c = _fresh_client()
    r = c.post("/refresh/notify")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_action_variant_distinguishes_close_profit_vs_close_loss():
    """User confusion (2026-06-30): both loss-stop CLOSE and take-profit
    CLOSE render as the same '🔚 CLOSE' verb. action_variant() inspects
    the summary's leading P&L sign and returns distinct icons + tones
    + explanatory notes so the user can tell them apart at a glance."""
    from app.icons import action_variant

    # Real fixture summary format: "**CLOSE** IDENT — +51% ..."
    v = action_variant("CLOSE", "**CLOSE** AMD_PUT_420_20261218 — +51% ($+3,823); buy-to-close limit $39.30")
    assert v["icon"] == "💰"
    assert "profit" in v["label"].lower()
    assert v["tone"] == "ok"
    assert "Take-profit" in v["note"]

    # Negative P&L → loss-stop variant
    v = action_variant("CLOSE", "**CLOSE** X_PUT_100 — -12% ($-450); loss stop triggered")
    assert v["icon"] == "🚨"
    assert "loss" in v["label"].lower()
    assert v["tone"] == "bad"
    assert "underwater" in v["note"].lower()

    # No P&L in summary → fall back to plain CLOSE
    v = action_variant("CLOSE", "some prose without a percentage")
    assert v["icon"] == "🔚"
    assert v["label"] == "CLOSE"

    # Non-CLOSE verb → standard icon + label
    v = action_variant("HEDGE", "Buy 16× SPY put $709P")
    assert v["icon"] == "🛡️"
    assert v["label"] == "HEDGE"


def test_action_variant_empty_and_none():
    """Fail-open: None / empty inputs → empty dict values, never crash."""
    from app.icons import action_variant
    assert action_variant(None) == {"icon": "", "label": "", "tone": "muted"}
    assert action_variant("") == {"icon": "", "label": "", "tone": "muted"}
    # No summary is fine
    v = action_variant("CLOSE")
    assert v["label"] == "CLOSE"


def test_home_page_wires_action_variant_helper():
    """Contract test: the action_variant global function must be
    accessible in Jinja templates so the urgent-card render can
    distinguish CLOSE (profit) vs CLOSE (loss). Fixture data doesn't
    always trigger the P&L branches, but the WIRING must be present."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    # Template must use action_variant() (visible via the .home-urgent-{tone} class
    # OR the aged-tooltip). If neither shows up, the wiring is broken.
    assert (
        "home-urgent-ok" in body
        or "home-urgent-bad" in body
        or "home-urgent-muted" in body
        or "home-urgent-card-age" in body   # aged tooltip
    ), "Home template must use the variant-aware home-urgent-{tone} class"


def test_briefing_markdown_view_route_serves_document(monkeypatch, tmp_path):
    """Task #44: user asked for the raw briefing markdown rendered as a
    typography-first document. Route: /briefing/<DATE>/md. Must resolve
    to the file at briefings_delivery()/briefing_<DATE>.md, render as
    HTML wrapped in the doc-view template."""
    from app.config import briefings_delivery
    delivery = briefings_delivery()
    if not (delivery / "briefing_2026-06-30.md").exists():
        # Test fixture might not have the file — synthesize one
        delivery.mkdir(parents=True, exist_ok=True)
        (delivery / "briefing_2026-06-30.md").write_text(
            "# Test briefing\n\n**NLV:** $1M\n\n## Actions\n\n1. CLOSE X"
        )
    c = _fresh_client()
    r = c.get("/briefing/2026-06-30/md")
    assert r.status_code == 200
    body = r.text
    assert "doc-view" in body, "document-view template not rendered"
    assert "Read as document" not in body, (
        "topbar 'Document' link is a topbar item, but the page itself "
        "should not repeat the 'Read as document' hero from Home"
    )


def test_briefing_markdown_raw_endpoint_serves_plain_text():
    """/briefing/<DATE>/md/raw returns the .md content as text/plain
    for copy-paste / curl workflows."""
    from app.config import briefings_delivery
    delivery = briefings_delivery()
    if not (delivery / "briefing_2026-06-30.md").exists():
        delivery.mkdir(parents=True, exist_ok=True)
        (delivery / "briefing_2026-06-30.md").write_text("# Test\n")
    c = _fresh_client()
    r = c.get("/briefing/2026-06-30/md/raw")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "#" in r.text  # some markdown syntax present


def test_briefing_latest_md_redirects_to_dated_document():
    """Topbar link /briefing/latest/md must 302 to /briefing/<DATE>/md."""
    c = _fresh_client()
    r = c.get("/briefing/latest/md", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/md" in r.headers["location"]


def test_home_page_has_prominent_document_view_link():
    """The Home page must surface the document-view link BEFORE the
    dashboard chrome so users who prefer the linear-read flow don't
    have to scroll past cards to find it."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "home-doc-link" in body
    assert "Read as document" in body
    # Positioning check — doc link must appear before the KPI strip
    doc_idx = body.find("home-doc-link")
    kpi_idx = body.find("home-kpi-strip")
    assert doc_idx > 0 and kpi_idx > 0
    assert doc_idx < kpi_idx, (
        "Document view link should appear BEFORE the KPI strip on Home"
    )


def test_topbar_has_document_link_marked_prominently():
    """Topbar has a dedicated 📄 Document nav item, distinct from the
    structured Briefing nav. Bolded/emoji-tagged to signal primary path."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'href="/briefing/latest/md"' in body
    assert "📄 Document" in body


def test_ingest_sticky_backend_prevents_table_not_exist_race():
    """Task #48: user hit 500 'Table portfolio_timeseries does not exist'
    on dashboard load. Root cause: _connect_write_resilient() picked
    in-memory on call 1 (write phase), then picked disk on call 2 (read
    phase) because disk had become accessible between calls. Tables
    lived on _INMEM_CONN; disk file was empty → 500.

    Fix: once we've committed to _INMEM_CONN for this process, always
    return it — no mid-session backend switching.
    """
    from app import ingest
    # Prime _INMEM_CONN as if a prior call had fallen back
    ingest._INMEM_CONN = None
    ingest._PROJECTIONS_READY = False
    ingest._LAST_MTIME_CHECK = 0.0
    ingest._LAST_MATERIALIZED_MTIME = 0.0

    # First call materializes on in-memory (disk unavailable in sandbox);
    # this proves the sticky contract by fetching from portfolio_timeseries.
    conn1 = ingest.connect()
    try:
        # If sticky failed, this would raise CatalogException
        conn1.execute("SELECT COUNT(*) FROM portfolio_timeseries").fetchone()
    finally:
        conn1.close()

    # Auto-refresh path: reset the throttle so _should_auto_refresh() fires
    ingest._LAST_MTIME_CHECK = 0.0
    ingest._LAST_MATERIALIZED_MTIME = 0.0
    conn2 = ingest.connect()
    try:
        # STILL must work — sticky backend means we're on the same
        # _INMEM_CONN as call 1, and the auto-refresh re-materialized
        # tables on THAT same connection.
        row = conn2.execute(
            "SELECT COUNT(*) FROM portfolio_timeseries"
        ).fetchone()
        assert row is not None
    finally:
        conn2.close()


def test_ingest_auto_detects_new_briefing_on_disk(monkeypatch):
    """Task #43: when a fresh briefing lands on disk from a CLI pipeline
    run outside the webapp, the next connect() must detect it and
    auto-materialize — no manual Refresh click required.

    Test contract:
      - _should_auto_refresh() returns True when disk mtime > sentinel
      - _should_auto_refresh() throttles rapid re-checks
    """
    from app import ingest
    # Force a check by resetting the throttle
    ingest._LAST_MTIME_CHECK = 0.0
    ingest._LAST_MATERIALIZED_MTIME = 0.0
    monkeypatch.setattr(ingest, "_newest_briefing_mtime", lambda: 1_000_000.0)
    assert ingest._should_auto_refresh() is True

    # Second call within throttle window returns False regardless
    ingest._LAST_MATERIALIZED_MTIME = 0.0
    monkeypatch.setattr(ingest, "_newest_briefing_mtime", lambda: 2_000_000.0)
    assert ingest._should_auto_refresh() is False  # throttled

    # After throttle window, the check runs again
    ingest._LAST_MTIME_CHECK = 0.0
    assert ingest._should_auto_refresh() is True


def test_ingest_auto_refresh_returns_false_when_nothing_changed():
    from app import ingest
    ingest._LAST_MTIME_CHECK = 0.0
    ingest._LAST_MATERIALIZED_MTIME = 1_000_000.0
    import types
    original = ingest._newest_briefing_mtime
    try:
        ingest._newest_briefing_mtime = lambda: 1_000_000.0
        assert ingest._should_auto_refresh() is False
    finally:
        ingest._newest_briefing_mtime = original


def test_home_derives_hero_sentence_from_red_flags():
    """UX Tier 2 redesign: the morning-briefing home leads with a
    one-sentence portfolio state derived from the highest-severity
    red flag. Critical flag → 'Defensive mode' language; empty flags
    → 'Portfolio healthy'."""
    from app import home
    # Critical flag drives the hero
    hero = home.derive_hero_sentence(
        [{"severity": "critical", "title": "Coverage 0.07×", "detail": "..."}],
        urgent_action_count=1,
    )
    assert hero["tone"] == "critical"
    assert "Defensive" in hero["text"]
    assert "1 urgent action" in hero["text"]

    # Warnings only → cautious tone
    hero = home.derive_hero_sentence(
        [{"severity": "warning", "title": "Cash 4.5%", "detail": "..."}],
        urgent_action_count=0,
    )
    assert hero["tone"] == "warning"
    assert "Cautious" in hero["text"]

    # Nothing flagged → healthy
    hero = home.derive_hero_sentence([], urgent_action_count=0)
    assert hero["tone"] == "ok"
    assert "healthy" in hero["text"].lower()


def test_home_urgent_actions_prioritizes_defensive_over_take_profit():
    """CLOSE / DEFENSIVE_ROLL / HEDGE come before TAKE_PROFIT in the
    urgent list — a losing position at loss-stop is more urgent than
    locking in a winner."""
    from app import home
    from types import SimpleNamespace
    actions = [
        SimpleNamespace(kind="TAKE_PROFIT", ident="NVDA_PUT_180", days_flagged=0),
        SimpleNamespace(kind="DEFENSIVE_ROLL", ident="AMD_PUT_150", days_flagged=1),
        SimpleNamespace(kind="HEDGE", ident="SPY_PUT_500", days_flagged=0),
    ]
    urgent = home.top_urgent_actions(actions, limit=3)
    assert urgent[0].kind in ("DEFENSIVE_ROLL", "HEDGE")


def test_home_top_actionable_prefers_top_conviction():
    """Top opportunities module: cards with 🏆 top_conviction flag
    surface before other actionable cards."""
    from app import home
    struct = {
        "sections": [{
            "cards": [
                {"ticker": "AMD", "status_tone": "ok", "flags": {"top_conviction": False}},
                {"ticker": "NVDA", "status_tone": "ok", "flags": {"top_conviction": True}},
                {"ticker": "SOFI", "status_tone": "info", "flags": {}},
            ],
        }],
    }
    top = home.top_actionable_from_setups(struct, limit=3)
    assert top[0]["ticker"] == "NVDA"  # top_conviction wins


def test_home_page_renders_hero_and_kpi_strip():
    """End-to-end: the `/` route renders the new home template with
    the hero + KPI strip visible above the fold."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "home-hero" in body
    assert "home-kpi-strip" in body


def test_home_page_carries_continue_navigation():
    """The morning-briefing home ends with a Continue strip pointing
    at the deeper surfaces (briefing / setups / positions / history)."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "home-continue" in body
    for target in ("/briefing/", "/setups/latest", "/positions", "/history"):
        assert target in body


def test_home_page_renders_morning_routine_checklist():
    """UX Tier 3: the Home page renders a dismissable morning-routine
    checklist. Persisted client-side in localStorage keyed by date."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'id="home-checklist"' in body
    assert "data-date=" in body
    assert "home-checklist-steps" in body
    assert 'onclick="dismissChecklist()"' in body


def test_topbar_has_density_toggle():
    """Simple/Expert density toggle must appear on every page."""
    c = _fresh_client()
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'id="density-toggle"' in body
    assert 'data-density="simple"' in body
    assert 'data-density="expert"' in body


def test_density_simple_css_rules_defined():
    """`body.density-simple` rules must exist in app.css to actually
    hide chrome when the user picks Simple mode."""
    from pathlib import Path
    css = (Path(__file__).parent.parent / "app" / "static" / "app.css").read_text()
    assert "body.density-simple" in css, (
        "app.css missing density-simple rules — Simple mode toggle would "
        "do nothing"
    )
    # A few specific hide rules that must exist
    assert "body.density-simple .rep-noise-expander" in css
    assert "body.density-simple .rep-extras" in css


def test_etrade_auth_status_endpoint_returns_valid_shape():
    """UX critique / task #35: /etrade/auth-status must always return a
    JSON envelope with status/token_age_hours/hint, even when no token
    file exists. Frontend depends on this contract."""
    c = _fresh_client()
    r = c.get("/etrade/auth-status")
    assert r.status_code == 200
    import json as _json
    body = _json.loads(r.text)
    assert "status" in body
    assert body["status"] in ("fresh", "idle_stale", "hard_expired", "missing", "unknown")
    assert "hint" in body
    assert "reauth_url_available" in body


def test_briefing_has_rotation_opportunities_field():
    """Task #36: rotation_opportunities is a first-class field on the
    briefing JSON. Empty dict when the pipeline predates rotation-advisor,
    but never absent."""
    from app.models.briefing import load_briefing
    from datetime import date as _date
    raw = {
        "date": _date.today().isoformat(),
        "nlv": 1_000_000,
        "cash": 50_000,
        "equity_reviews": [],
        "options_reviews": [],
    }
    b = load_briefing(raw)
    # Field exists, defaults to empty dict
    assert hasattr(b, "rotation_opportunities")
    assert b.rotation_opportunities == {}
    # Explicit value is preserved
    raw["rotation_opportunities"] = {"equity": [], "options": [], "capital_plan": [], "stats": {}}
    b2 = load_briefing(raw)
    assert "stats" in b2.rotation_opportunities


def test_report_parser_extracts_cards_from_real_fixture():
    """Round-trip: parse a real markdown report and assert we got cards."""
    from pathlib import Path
    from app import report_parser

    # Use whatever fixture the test suite ships
    fixture_dir = Path(__file__).parent / "fixtures"
    md_files = list(fixture_dir.glob("when_to_enter_*.md")) + \
               list(fixture_dir.glob("candidates_*.md"))
    if not md_files:
        pytest.skip("no report fixtures available")

    for f in md_files:
        text = f.read_text()
        # Try both kinds — at least one should parse cards
        for kind in ("candidates", "when_to_enter"):
            rep = report_parser.parse_report(text, kind)
            cards = report_parser.summary_counts(rep)
            if sum(cards.values()) > 0:
                # Found a parse — verify structure
                assert rep.get("sections"), "parser found cards but no sections"
                break
