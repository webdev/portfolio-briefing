"""Regression tests for the setups builder (task #56 and beyond).

The setups page merges two source reports (candidates + when_to_enter) into
one unified view. The two reports use different section-title conventions,
and merging by raw title string caused sections to split into duplicates.

Every test here pins a real bug the user saw or a critical merge contract.
"""

from __future__ import annotations

from app import setups


# ─── _normalize_section_title (task #56) ──────────────────────────────────


def test_normalize_strips_leading_emoji():
    """`## 🔭 Robotics & Autonomy` normalizes to same key as `## Robotics & Autonomy`."""
    a = setups._normalize_section_title("🔭 Robotics & Autonomy")
    b = setups._normalize_section_title("Robotics & Autonomy")
    assert a == b
    assert a == "robotics & autonomy"


def test_normalize_strips_trailing_count():
    """`## Robotics & Autonomy (10)` normalizes to same key as `## Robotics & Autonomy`."""
    a = setups._normalize_section_title("Robotics & Autonomy (10)")
    b = setups._normalize_section_title("Robotics & Autonomy")
    assert a == b


def test_normalize_strips_both_prefix_and_count():
    """Real-world case that caused the bug: candidates uses emoji, WTE uses count."""
    a = setups._normalize_section_title("🔭 Robotics & Autonomy")
    b = setups._normalize_section_title("Robotics & Autonomy (10)")
    assert a == b
    assert a == "robotics & autonomy"


def test_normalize_handles_none_and_empty():
    """Fail-open on empty/None (the merger uses this as a key — must be stable)."""
    assert setups._normalize_section_title(None) == ""
    assert setups._normalize_section_title("") == ""
    assert setups._normalize_section_title("   ") == ""


def test_normalize_preserves_ascii_content():
    """Non-emoji ASCII should pass through, lowercased."""
    assert setups._normalize_section_title("AI Data Centers") == "ai data centers"
    assert setups._normalize_section_title("Memory & Storage") == "memory & storage"


# ─── build_setups end-to-end (task #56 regression) ───────────────────────


def _minimal_candidates_md():
    """Minimal candidates_report.md content with emoji-prefixed section header."""
    return (
        "CAPACITY: coverage 0.16x | cash 10.6%\n\n"
        "# Candidate Research — Friday, July 03, 2026\n\n"
        "**5 companies analyzed:** 0 🎯 CANDIDATE · 0 ⏸ held by RSI · 3 👀 WATCH · 2 🔴 AVOID\n\n"
        "# ━━━ The AI Buildout ━━━\n\n"
        "## 🔭 Robotics & Autonomy\n"
        "_Companies: TSLA, MBLY, CGNX, OUST, LAZR · ETFs: BOTZ, ROBO_\n\n"
        "**👀 WATCH · `OUST` · $49.84**\n"
        "- RSI 55 · IV rank 100 · drawdown 20% · 5d +6%\n\n"
        "**🔴 AVOID · `MBLY` · $9.57**\n"
        "- RSI 56 · IV rank 100 · drawdown 50% · 5d +22%\n\n"
        "**🔴 AVOID · `TSLA` · $393.45**\n"
        "- RSI 55 · IV rank 60 · drawdown 15%\n\n"
    )


def _minimal_wte_md():
    """Minimal when_to_enter.md content with count-suffixed section header."""
    return (
        "# When To Enter — Friday, July 03, 2026\n\n"
        "## Robotics & Autonomy (5)\n\n"
        "#### `OUST` · $49.84 · **🟡 NEUTRAL**\n"
        "- Trigger: RSI < 50 AND 5-8% pullback\n\n"
        "#### `MBLY` · $9.57 · **🔴 AVOID — scout flag**\n"
        "- Trigger: none — thesis check needed\n\n"
        "#### `CGNX` · $67.80 · **🟢 ENTRY NOW — CSP** ⚠ no third-party rec\n"
        "- CSP entry: $65P Aug 21 for $2.10 premium\n\n"
        "#### `AEVA` · $23.98 · **🟡 WATCH — neutral**\n"
        "- No trigger yet\n\n"
    )


def test_build_setups_merges_emoji_and_count_into_one_section(tmp_path, monkeypatch):
    """THE regression test for task #56.

    User's symptom: added 6 tickers to Robotics theme, went to /setups/{date},
    saw them split across "🔭 Robotics & Autonomy" and "Robotics & Autonomy (10)"
    as two adjacent duplicate sections. Fix: normalize the merge key.
    """
    # Write the minimal fixture reports
    delivery = tmp_path / "briefings"
    delivery.mkdir()
    (delivery / "candidates_2099-01-01.md").write_text(_minimal_candidates_md(),
                                                       encoding="utf-8")
    (delivery / "when_to_enter_2099-01-01.md").write_text(_minimal_wte_md(),
                                                         encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(delivery))

    result = setups.build_setups("2099-01-01")

    # Robotics-related section count MUST be 1, not 2
    robo_sections = [s for s in result.get("sections", [])
                     if "robot" in (s.get("title") or "").lower()]
    assert len(robo_sections) == 1, (
        f"Expected 1 Robotics section, got {len(robo_sections)}: "
        f"{[s.get('title') for s in robo_sections]}"
    )

    # Display title should be the candidates version (with emoji), not WTE's count
    section = robo_sections[0]
    assert "🔭" in section["title"], f"Expected emoji-prefixed display title, got: {section['title']}"
    assert "(5)" not in section["title"], f"Trailing count leaked into display title: {section['title']}"

    # All tickers from BOTH sources should be in this one section
    tickers = {c.get("ticker") for c in section.get("cards", [])}
    # From candidates: OUST, MBLY, TSLA
    # From WTE: OUST, MBLY, CGNX, AEVA
    # Union: OUST, MBLY, TSLA, CGNX, AEVA
    expected = {"OUST", "MBLY", "TSLA", "CGNX", "AEVA"}
    assert tickers == expected, (
        f"Not all tickers merged into one section. Got {tickers}, expected {expected}"
    )


def test_build_setups_empty_when_no_reports(tmp_path, monkeypatch):
    """No reports on disk for the given date → empty result, not a crash."""
    delivery = tmp_path / "briefings"
    delivery.mkdir()
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(delivery))

    result = setups.build_setups("1999-12-31")
    assert result["sections"] == []


def test_build_setups_wte_only_ticker_appears(tmp_path, monkeypatch):
    """A ticker present only in when_to_enter (not candidates) should still show up."""
    delivery = tmp_path / "briefings"
    delivery.mkdir()
    # No candidates report; only WTE
    (delivery / "when_to_enter_2099-01-02.md").write_text(_minimal_wte_md(),
                                                         encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(delivery))

    result = setups.build_setups("2099-01-02")
    tickers = {c.get("ticker") for s in result["sections"] for c in s.get("cards", [])}
    # WTE has these
    assert "CGNX" in tickers
    assert "AEVA" in tickers
    assert "MBLY" in tickers
    assert "OUST" in tickers


def test_build_setups_preserves_source_order(tmp_path, monkeypatch):
    """Section order should follow candidates_report first, then WTE-only sections."""
    delivery = tmp_path / "briefings"
    delivery.mkdir()
    cand = (
        "# Candidates\n\n"
        "## 🔭 Semis\n\n"
        "**👀 WATCH · `NVDA` · $200**\n"
        "- RSI 55\n\n"
        "## 🔭 Robotics & Autonomy\n\n"
        "**👀 WATCH · `TSLA` · $400**\n"
        "- RSI 55\n\n"
    )
    wte = (
        "# When To Enter\n\n"
        "## Robotics & Autonomy (2)\n\n"
        "#### `TSLA` · $400 · **🟡 NEUTRAL**\n"
        "- Trigger: watch\n\n"
        "## Semis (1)\n\n"
        "#### `NVDA` · $200 · **🟡 NEUTRAL**\n"
        "- Trigger: watch\n\n"
    )
    (delivery / "candidates_2099-01-03.md").write_text(cand, encoding="utf-8")
    (delivery / "when_to_enter_2099-01-03.md").write_text(wte, encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(delivery))

    result = setups.build_setups("2099-01-03")
    section_titles = [s["title"] for s in result["sections"]]
    # Candidates order was Semis then Robotics; that must be preserved
    semis_idx = next(i for i, t in enumerate(section_titles) if "Semis" in t)
    robo_idx = next(i for i, t in enumerate(section_titles) if "Robotics" in t)
    assert semis_idx < robo_idx, f"Section order not preserved: {section_titles}"
