"""Two-document briefing split — digest builder regression tests.

User symptom (2026-08-06): "The document should be pretty quickly readable
by a person." Even after compact mode the briefing ran ~1200-1500+ lines.
The fix: briefing_full_<date>.md keeps the ENTIRE render (verifiers +
webapp), while briefing_<date>.md / latest.md become a <=250-line digest.

Fixture: the REAL delivered briefing_2026-08-06.md (2530 lines), copied to
tests/fixtures/briefing_full_2026-08-06.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from render.digest import (
    build_digest,
    digest_enabled,
    max_candidates,
)

FIXTURE = Path(__file__).parent / "fixtures" / "briefing_full_2026-08-06.md"


@pytest.fixture(scope="module")
def full_md() -> str:
    return FIXTURE.read_text()


@pytest.fixture(scope="module")
def digest(full_md: str) -> str:
    return build_digest(
        full_md,
        config={"render": {"digest": True, "digest_max_candidates": 5}},
        extras={
            "date": "2026-08-06",
            "companion_files": [
                "briefing_full_2026-08-06.md",
                "candidates_2026-08-06.md",
                "when_to_enter_2026-08-06.md",
            ],
        },
    )


# ── size + shape ─────────────────────────────────────────────────────────


def test_digest_is_at_most_250_lines(full_md, digest):
    """The whole point of the split: 'The document should be pretty quickly
    readable by a person.' The real 2530-line briefing must digest to
    <=250 lines."""
    assert len(full_md.splitlines()) > 1000  # sanity: fixture is the real doc
    assert len(digest.splitlines()) <= 250


def test_digest_keeps_the_decision_sections_in_order(digest):
    headers = [ln for ln in digest.splitlines()
               if ln.startswith("## ")
               or (ln.startswith("# ") and not ln.startswith("## "))]
    joined = "\n".join(headers)
    for needle in ["Money Plan", "Daily Briefing", "Health", "Action List",
                   "Red Flags", "Since Yesterday", "Candidate Trades",
                   "Capital Plan", "Benchmark", "Full Detail", "Fable"]:
        assert needle in joined, f"digest lost section: {needle}"
    # Order: Action List before Red Flags before Candidate Trades before
    # Capital Plan before Benchmark before Fable.
    idx = {n: joined.find(n) for n in
           ["Action List", "Red Flags", "Candidate Trades", "Capital Plan",
            "Benchmark", "Fable"]}
    assert (idx["Action List"] < idx["Red Flags"] < idx["Candidate Trades"]
            < idx["Capital Plan"] < idx["Benchmark"] < idx["Fable"])


def test_digest_omits_the_research_sections(digest):
    """LTO / Watch / Technical Read / Scout / Strategy Upgrades / Analyst
    Brief live ONLY in the full render (pointer lines aside)."""
    headers = [ln for ln in digest.splitlines() if ln.startswith("## ")]
    joined = "\n".join(headers)
    for absent in ["Long-Term Opportunities", "Watch / Portfolio Review",
                   "Technical Read", "Thematic Scout", "Strategy Upgrades",
                   "Analyst Brief", "Stress Test", "Counterpoints"]:
        assert absent not in joined, f"digest should not carry: {absent}"


# ── the digest never loses an action ─────────────────────────────────────


def test_every_numbered_action_line_from_full_is_in_digest(full_md, digest):
    """Watch-panel URGENT/CLOSE/ROLL items surface in the Action List by
    design — so keeping the Action List verbatim means the digest never
    loses an action."""
    in_section = False
    numbered = []
    for ln in full_md.splitlines():
        if ln.startswith("## ") and "Action List" in ln:
            in_section = True
            continue
        if in_section and ln.startswith("## "):
            break
        if in_section and re.match(r"^\d+\. \*\*", ln):
            numbered.append(ln)
    assert numbered, "fixture has no numbered actions — fixture broke"
    for ln in numbered:
        assert ln in digest, f"digest lost action line: {ln[:80]}"


def test_transparency_footers_stripped_from_action_list(full_md, digest):
    """Standalone italic transparency footers are NOT in the digest (spec).
    The fixture's Action List carries blocked-CSP italics."""
    m = re.search(r"## Today's Action List.*?(?=\n## )", full_md, re.S)
    assert m
    italics = [ln for ln in m.group(0).splitlines()
               if re.match(r"^_.*_\s*$", ln)]
    assert italics, "fixture lost its Action List transparency footers"
    for ln in italics:
        assert ln not in digest


# ── candidates top-N ─────────────────────────────────────────────────────


def test_candidates_capped_at_top_n_with_computed_remainder(full_md, digest):
    total = int(re.search(r"Today's Candidates \((\d+)\)", full_md).group(1))
    cards_in_digest = [ln for ln in digest.splitlines()
                       if ln.startswith("**🎯 CANDIDATE")]
    assert len(cards_in_digest) == 5
    # Remainder line carries the COMPUTED count (rule #19).
    assert f"…and {total - 5} more candidates" in digest
    assert f"Top 5 of {total}" in digest


def test_candidate_tickets_keep_deferred_tag(digest):
    """Rule #24/#41 — a capacity-gated ticket must never read as green-lit;
    the inline ⏸ Deferred tag survives the digest verbatim."""
    section = digest[digest.find("## 🎯 Candidate Trades"):]
    section = section[:section.find("\n## ", 1)]
    assert "Deferred (capacity gated)" in section


def test_max_candidates_config_respected(full_md):
    d = build_digest(full_md,
                     config={"render": {"digest_max_candidates": 2}},
                     extras={"date": "2026-08-06"})
    cards = [ln for ln in d.splitlines() if ln.startswith("**🎯 CANDIDATE")]
    assert len(cards) == 2


# ── pointer lines with real computed counts (rule #19) ───────────────────


def test_pointer_block_lists_companion_files_actually_written(digest):
    assert ("_Full detail: briefing_full_2026-08-06.md · "
            "candidates_2026-08-06.md · when_to_enter_2026-08-06.md_"
            in digest)


def test_pointer_block_has_no_files_line_when_none_written(full_md):
    d = build_digest(full_md, extras={"date": "2026-08-06"})
    assert "_Full detail: " not in d


def test_omitted_watch_count_is_computed_not_guessed(full_md, digest):
    """`Watch (N equities · M options...)` counts must match the fixture's
    real markers — never hardcoded."""
    m = re.search(r"## Watch / Portfolio Review.*?(?=\n## )", full_md, re.S)
    body = m.group(0)
    eq = len(re.findall(r"^- \*\*[A-Z][A-Z0-9.\-]*\*\* @ ", body, re.M))
    opts = len(re.findall(r"^📌 ", body, re.M))
    assert eq > 0 and opts > 0
    assert f"Watch / Portfolio Review ({eq} equities · {opts} options" in digest


def test_omitted_scout_count_comes_from_the_full_render(full_md, digest):
    m = re.search(r"(\d+) analyzed across (\d+) themes", full_md)
    assert m
    assert f"({m.group(1)} names · {m.group(2)} themes)" in digest


def test_meta_verifier_panels_get_no_pointer_line(digest):
    pointer = digest[digest.find("## 📎 Full Detail"):]
    pointer = pointer[:pointer.find("\n## ", 1)]
    for absent in ["Coverage", "Appendix", "Inconsistencies"]:
        assert absent not in pointer


# ── fable + telegram compatibility ───────────────────────────────────────


def test_fable_section_survives_for_telegram_extraction(full_md, digest):
    """The Telegram bot's _extract_fable_section keys off this exact H2 —
    it must exist in the digest (latest.md is what the bot reads)."""
    assert "## 🔍 Fable's second opinion" in digest
    sys_path_root = Path(__file__).resolve().parents[1]
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tg_bot_for_digest_test", sys_path_root / "telegram_briefing_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    section = mod._extract_fable_section(digest)
    assert section and section.startswith("## 🔍 Fable")


# ── fail-open + config gates ─────────────────────────────────────────────


def test_fail_open_on_unsplittable_input():
    """Structure missing (no H1 / no Action List) → deliver the input
    unchanged, never a broken digest."""
    junk = "just some prose\nwith no headers at all\n"
    assert build_digest(junk) == junk
    no_actions = "# Title\n\n## Health\n\nstuff\n"
    assert build_digest(no_actions) == no_actions


def test_digest_enabled_defaults_and_legacy_off():
    assert digest_enabled(None) is True
    assert digest_enabled({}) is True
    assert digest_enabled({"render": {}}) is True
    assert digest_enabled({"render": {"digest": False}}) is False


def test_max_candidates_default_and_bad_values():
    assert max_candidates(None) == 5
    assert max_candidates({"render": {"digest_max_candidates": 3}}) == 3
    assert max_candidates({"render": {"digest_max_candidates": 0}}) == 5
    assert max_candidates({"render": {"digest_max_candidates": "x"}}) == 5


# ── digest is a subset + generated lines only ────────────────────────────


def test_digest_lines_are_subset_of_full_plus_generated(full_md, digest):
    """Every digest line either exists verbatim in the full render or is a
    generated header/pointer/count line — the digest never REWRITES
    content (rule #19: no fabricated data)."""
    full_lines = set(full_md.splitlines())
    generated_ok = re.compile(
        r"^(## 🎯 Candidate Trades — Top \d+ of \d+"
        r"|## 📎 Full Detail"
        r"|_Full detail: "
        r"|_Also in the full briefing: "
        r"|_…and \d+ more"
        r"|_Capital Plan sub-detail trimmed"
        r"|_See the Candidate Trades section)"
    )
    for ln in digest.splitlines():
        if not ln.strip():
            continue
        if ln in full_lines:
            continue
        assert generated_ok.match(ln), f"rewritten line in digest: {ln[:100]}"


# ── delivery wiring ──────────────────────────────────────────────────────


def test_deliver_briefing_copies_digest_as_latest_and_full_alongside(tmp_path):
    """briefing_<date>.md + latest.md = digest (what Telegram sends);
    briefing_full_<date>.md delivered alongside (what the webapp parses)."""
    from steps.deliver import deliver_briefing
    src = tmp_path / "out"
    src.mkdir()
    digest_p = src / "briefing_2026-08-06.md"
    digest_p.write_text("DIGEST\n")
    full_p = src / "briefing_full_2026-08-06.md"
    full_p.write_text("FULL\n")
    dest = tmp_path / "delivery"
    res = deliver_briefing(digest_p, delivery_dir=str(dest), full_path=full_p)
    assert (dest / "briefing_2026-08-06.md").read_text() == "DIGEST\n"
    assert (dest / "latest.md").read_text() == "DIGEST\n"
    assert (dest / "briefing_full_2026-08-06.md").read_text() == "FULL\n"
    assert res["full"] == str(dest / "briefing_full_2026-08-06.md")


def test_deliver_briefing_without_full_path_is_legacy_identical(tmp_path):
    """render.digest: false → no full sidecar; legacy behavior unchanged."""
    from steps.deliver import deliver_briefing
    src = tmp_path / "out"
    src.mkdir()
    p = src / "briefing_2026-08-06.md"
    p.write_text("FULL-AS-BEFORE\n")
    dest = tmp_path / "delivery"
    res = deliver_briefing(p, delivery_dir=str(dest))
    assert (dest / "latest.md").read_text() == "FULL-AS-BEFORE\n"
    assert "full" not in res
    assert not (dest / "briefing_full_2026-08-06.md").exists()
