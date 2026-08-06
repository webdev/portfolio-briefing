"""Two-document briefing split — webapp reads the FULL render.

User symptom (2026-08-06): "The document should be pretty quickly readable
by a person." The pipeline now delivers briefing_full_<date>.md (complete
render) alongside briefing_<date>.md (digest). The webapp must parse/render
the FULL document, falling back to briefing_<date>.md for historical dates
that predate the split.
"""

from __future__ import annotations


def _write_pair(delivery, date: str):
    digest = f"# Daily Briefing — {date}\n\n## Money Plan\n\nDIGEST BODY\n"
    full = (
        f"# Daily Briefing — {date}\n\n## Money Plan\n\nFULL BODY\n\n"
        "## Watch / Portfolio Review\n\nfull-only section\n"
    )
    (delivery / f"briefing_{date}.md").write_text(digest)
    (delivery / f"briefing_full_{date}.md").write_text(full)
    return digest, full


def test_briefing_md_path_prefers_full_render(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(tmp_path))
    from app.config import briefing_md_path
    _write_pair(tmp_path, "2026-08-06")
    assert briefing_md_path("2026-08-06").name == "briefing_full_2026-08-06.md"


def test_briefing_md_path_falls_back_for_pre_split_history(tmp_path, monkeypatch):
    """Historical dates only have briefing_<date>.md — that file IS the
    full render for them."""
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(tmp_path))
    from app.config import briefing_md_path
    (tmp_path / "briefing_2026-05-08.md").write_text("# old full render\n")
    assert briefing_md_path("2026-05-08").name == "briefing_2026-05-08.md"


def test_md_raw_route_serves_full_render_not_digest(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(tmp_path))
    digest, full = _write_pair(tmp_path, "2026-08-06")
    r = client.get("/briefing/2026-08-06/md/raw")
    assert r.status_code == 200
    assert "FULL BODY" in r.text
    assert "full-only section" in r.text
    assert "DIGEST BODY" not in r.text


def test_md_raw_route_falls_back_to_digest_named_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(tmp_path))
    (tmp_path / "briefing_2026-05-08.md").write_text("# pre-split render\n")
    r = client.get("/briefing/2026-05-08/md/raw")
    assert r.status_code == 200
    assert "pre-split render" in r.text
