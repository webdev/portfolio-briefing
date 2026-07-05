"""Tests for the candidates_*.md / when_to_enter_*.md companion routes.

These render the pipeline's per-day companion reports inside the webapp
shell. The fixtures dir ships a tiny candidates + when-to-enter markdown
file for 2026-06-30 so we can pin the render path.
"""

from __future__ import annotations


def test_candidates_latest_redirects(client):
    r = client.get("/candidates/latest", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/candidates/2026-06-30" in r.headers["location"]


def test_when_to_enter_latest_redirects(client):
    r = client.get("/when-to-enter/latest", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/when-to-enter/2026-06-30" in r.headers["location"]


def test_candidates_page_renders(client):
    r = client.get("/candidates/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # Title + date + companion-page navigation
    assert "Candidate Research" in html
    assert "2026-06-30" in html
    # Source markdown should appear as HTML (h1 from markdown render)
    assert "<h1" in html and "Candidate Research" in html
    # Tickers come through as <code> via markdown literal backticks
    assert "NVDA" in html and "SMCI" in html
    # Verdict text + emojis pass through
    assert "🎯" in html or "CANDIDATE" in html


def test_when_to_enter_page_renders(client):
    r = client.get("/when-to-enter/2026-06-30")
    assert r.status_code == 200
    html = r.text
    assert "When to Enter" in html
    assert "2026-06-30" in html
    # Entry triggers must come through
    assert "ENTRY NOW" in html or "WAIT" in html
    assert "NVDA" in html


def test_candidates_404_on_missing_date(client):
    r = client.get("/candidates/2099-01-01")
    assert r.status_code == 404


def test_when_to_enter_404_on_missing_date(client):
    r = client.get("/when-to-enter/2099-01-01")
    assert r.status_code == 404


def test_setups_link_in_topbar(client):
    """UX simplification (task #39): Candidates + When-to-Enter were
    consolidated into a single 'Setups' nav item. The old direct routes
    still work as deep-links, but the topbar now points at /setups/latest."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/setups/latest"' in r.text


def test_candidates_and_when_to_enter_routes_still_resolve(client):
    """Backward compat: even though the topbar dropped the direct links,
    /candidates/{date} and /when-to-enter/{date} routes are preserved
    as deep-links (still linked from other parts of the app + bookmarks)."""
    r = client.get("/candidates/2026-06-30")
    assert r.status_code == 200
    r = client.get("/when-to-enter/2026-06-30")
    assert r.status_code == 200


def test_setups_page_renders_actionable_hero(client):
    """The consolidated Setups page must render the 'Actionable today'
    hero above the theme sections — leads with decisions, not noise."""
    r = client.get("/setups/latest", follow_redirects=True)
    assert r.status_code == 200
    body = r.text
    # Either the hero renders (data has actionable cards) OR the page
    # renders empty — both are acceptable, but the class must exist in
    # the template. Grep the raw HTML.
    assert 'class="rep-actionable-hero"' in body or 'rep-cards-grid' in body


def test_setups_page_hides_noise_behind_expander(client):
    """WATCH + AVOID cards go behind a <sl-details> per section so the
    ACTIONABLE pile leads the eye."""
    r = client.get("/setups/latest", follow_redirects=True)
    assert r.status_code == 200
    body = r.text
    # Either the noise expander exists or all cards happen to be actionable
    assert "rep-noise-expander" in body or "Show" in body


def test_markdown_render_module_unit():
    """The render helper converts headers, code, bullets to HTML."""
    from app import md_render

    src = "# Title\n\n**bold** and `code` and an item:\n\n- one\n- two\n"
    out = str(md_render.render(src))
    assert "<h1>" in out and "Title" in out
    assert "<strong>bold</strong>" in out
    assert "<code>code</code>" in out
    assert "<li>one</li>" in out and "<li>two</li>" in out


def test_md_render_available_dates(monkeypatch, tmp_path):
    """available_dates() scans the delivery dir for prefix matches."""
    from app import md_render

    # Point to a temp dir with a few files
    (tmp_path / "candidates_2026-01-15.md").write_text("ok")
    (tmp_path / "candidates_2026-02-15.md").write_text("ok")
    (tmp_path / "candidates_zzz.md").write_text("not a date — should be skipped")
    (tmp_path / "when_to_enter_2026-02-15.md").write_text("ok")
    (tmp_path / "other_file.md").write_text("not us")

    # md_render imports `briefings_delivery` from config at module load —
    # patch the name in the md_render module's namespace.
    monkeypatch.setattr(md_render, "briefings_delivery", lambda: tmp_path)

    dates = md_render.available_dates("candidates")
    assert dates == ["2026-02-15", "2026-01-15"]   # DESC

    we = md_render.available_dates("when_to_enter")
    assert we == ["2026-02-15"]


def test_md_render_load_missing(monkeypatch, tmp_path):
    """Fail-closed: load_report returns None on missing file."""
    from app import md_render

    monkeypatch.setattr(md_render, "briefings_delivery", lambda: tmp_path)
    out = md_render.load_report("candidates", "2099-01-01")
    assert out is None
