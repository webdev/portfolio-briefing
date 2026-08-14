"""Scout cache TTL + morning freshness + staleness transparency.

George (2026-08-14): "How long does the Scout take? I don't think we can
afford 24 hours. I think we need to run it every morning. Should we run it
at 6:30 a.m. before, to build up morning cache?"

Context: the scout cache had a hardcoded 24h TTL; yesterday's stale scout
RSI was the source of the SNDK "RSI 48 while live 76" bug (rule #47).
Contract pinned here:
  (a) TTL is config-driven (briefing.yaml scout.cache_ttl_hours = 6;
      legacy 24h stays the CODE default when config is absent),
  (b) force_refresh_morning refreshes any cache that predates today's
      local date regardless of TTL,
  (c) a refresh failure fails OPEN to the stale cache with a loud
      "⚠ scout cache Xh old — refresh failed" provenance note,
  (d) the scout / candidates headers state the MEASURED data age
      (rule #19),
  (e) the optional 6:30 AM pre-warm wrapper + launchd plist exist and
      reference valid paths.
"""

from __future__ import annotations

import inspect
import json
import plistlib
import sys
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import candidate_research as cr  # noqa: E402
from steps import thematic_research as tr  # noqa: E402

_SKILL_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- helpers

def _stub_scout(calls: list | None = None):
    """Deterministic zero-network scout stand-in (same as test_thematic_parallel)."""

    def _research_ticker(ticker, theme, cfg, recs_map, held_weights,
                         existing_short_puts=None):
        if calls is not None:
            calls.append(ticker)
        return {"ticker": ticker, "theme": theme, "verdict": "WATCH",
                "rationale": [], "spot": 100.0, "rsi_14": 50.0,
                "fivedayret_pct": 1.0}

    return types.SimpleNamespace(_research_ticker=_research_ticker)


@pytest.fixture()
def small_rules(tmp_path, monkeypatch):
    """Point the scout at a 2-ticker universe so refreshes stay instant."""
    rules = tmp_path / "theme_universes.yaml"
    rules.write_text(yaml.safe_dump({
        "themes": {"semis": {"name": "Semis", "group": "Test",
                             "anchors": ["NVDA", "AMD"]}},
    }))
    monkeypatch.setattr(tr, "_SCOUT_RULES", rules)
    return rules


@pytest.fixture()
def snapshot_dir(tmp_path):
    d = tmp_path / "snapshots" / "2026-08-14"
    d.mkdir(parents=True)
    return d


def _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=None,
                 when: datetime | None = None) -> Path:
    """Run one real (stubbed) refresh, then back-date the cache file."""
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())
    payload = tr.run_thematic_research(snapshot_dir, refresh=True, parallel=False)
    assert payload is not None and payload["_scout_meta"]["fresh"]
    cache_file = snapshot_dir.parent / "scout_cache.json"
    if age_hours is not None or when is not None:
        data = json.loads(cache_file.read_text())
        ts = when if when is not None else datetime.now() - timedelta(hours=age_hours)
        data["generated_at_iso"] = ts.isoformat()
        cache_file.write_text(json.dumps(data))
    return cache_file


# ------------------------------------------------- (a) TTL config honored

def test_code_default_ttl_is_legacy_24h():
    """Absent config, the CODE default stays 24h — callers without the new
    `scout:` block are byte-identical to the old behavior."""
    sig = inspect.signature(tr.run_thematic_research)
    assert sig.parameters["ttl_hours"].default == 24
    assert sig.parameters["force_refresh_morning"].default is False


def test_briefing_yaml_sets_6h_ttl_and_morning_refresh():
    """briefing.yaml carries scout: {cache_ttl_hours: 6, force_refresh_morning:
    true} — 'I don't think we can afford 24 hours.'"""
    cfg = yaml.safe_load((_SKILL_DIR / "config" / "briefing.yaml").read_text())
    scout = cfg.get("scout") or {}
    assert scout.get("cache_ttl_hours") == 6
    assert scout.get("force_refresh_morning") is True


def test_run_briefing_wires_scout_config():
    """The orchestrator reads the `scout:` block (not a hardcoded 24)."""
    src = (_SKILL_DIR / "scripts" / "run_briefing.py").read_text()
    assert 'config.get("scout")' in src
    assert "cache_ttl_hours" in src
    assert "force_refresh_morning" in src
    assert "ttl_hours=24" not in src, "TTL must come from config, not a literal"


def test_cache_within_ttl_is_served(monkeypatch, small_rules, snapshot_dir):
    """A 3h-old cache is fresh under a 6h TTL — served, scout not re-run."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=3.0)

    def _boom():
        raise AssertionError("scout must not run on a fresh cache")

    monkeypatch.setattr(tr, "_load_scout_module", _boom)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=6)
    assert payload is not None
    meta = payload["_scout_meta"]
    assert meta["fresh"] is False and meta["refresh_failed"] is False
    assert meta["age_hours"] == pytest.approx(3.0, abs=0.1)


def test_cache_older_than_6h_ttl_refreshes(monkeypatch, small_rules, snapshot_dir):
    """A 10h-old cache passes the legacy 24h TTL but NOT the 6h TTL: with
    ttl_hours=6 the scout re-runs; with the legacy default it is served."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=10.0)

    # Legacy default (24h): served from cache.
    calls: list = []
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout(calls))
    served = tr.run_thematic_research(snapshot_dir)
    assert served["_scout_meta"]["fresh"] is False
    assert calls == []

    # 6h TTL: refreshed.
    refreshed = tr.run_thematic_research(snapshot_dir, ttl_hours=6)
    assert refreshed["_scout_meta"]["fresh"] is True
    assert calls, "scout must re-run when the cache is older than the TTL"


# ----------------------------- (b) force_refresh_morning on stale-date cache

def test_force_refresh_morning_triggers_on_yesterdays_cache(
        monkeypatch, small_rules, snapshot_dir):
    """'I think we need to run it every morning.' A cache generated
    yesterday 23:59 is within ANY 24h TTL, yet force_refresh_morning=True
    must refresh it — every morning run gets a same-day scout."""
    yesterday_late = datetime.combine(
        date.today() - timedelta(days=1), time(23, 59))
    _build_cache(snapshot_dir, monkeypatch, small_rules, when=yesterday_late)

    calls: list = []
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout(calls))
    payload = tr.run_thematic_research(
        snapshot_dir, ttl_hours=24, force_refresh_morning=True)
    assert payload["_scout_meta"]["fresh"] is True
    assert calls, "stale-date cache must refresh under force_refresh_morning"


def test_same_day_cache_not_forced(monkeypatch, small_rules, snapshot_dir):
    """force_refresh_morning only fires on a PREVIOUS-date cache — a cache
    generated earlier today (within TTL) is still served."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=0.5)

    def _boom():
        raise AssertionError("same-day fresh cache must be served")

    monkeypatch.setattr(tr, "_load_scout_module", _boom)
    payload = tr.run_thematic_research(
        snapshot_dir, ttl_hours=6, force_refresh_morning=True)
    assert payload is not None
    assert payload["_scout_meta"]["fresh"] is False


def test_force_refresh_morning_off_serves_yesterdays_cache(
        monkeypatch, small_rules, snapshot_dir):
    """Control: with force_refresh_morning=False (legacy), yesterday's cache
    inside the TTL is served unchanged."""
    yesterday_late = datetime.combine(
        date.today() - timedelta(days=1), time(23, 59))
    _build_cache(snapshot_dir, monkeypatch, small_rules, when=yesterday_late)

    def _boom():
        raise AssertionError("legacy path must serve the in-TTL cache")

    monkeypatch.setattr(tr, "_load_scout_module", _boom)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=24)
    assert payload is not None
    assert payload["_scout_meta"]["fresh"] is False


# --------------------------------------- (c) refresh failure fails OPEN

def test_refresh_failure_fails_open_to_stale_cache(
        monkeypatch, small_rules, snapshot_dir, capsys):
    """A failed refresh must NOT die: the stale cache is served with
    refresh_failed=True and a loud warn — a stale scout that SAYS it's
    stale beats no scout at all."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=26.0)

    # Refresh path fails: scout module not loadable.
    monkeypatch.setattr(tr, "_load_scout_module", lambda: None)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=6)
    assert payload is not None, "fail OPEN — never None when a cache exists"
    meta = payload["_scout_meta"]
    assert meta["refresh_failed"] is True
    assert meta["fresh"] is False
    assert meta["age_hours"] == pytest.approx(26.0, abs=0.1)
    assert payload["summary"]["total"] > 0
    err = capsys.readouterr().err
    assert "failing open" in err


def test_refresh_crash_also_fails_open(monkeypatch, small_rules, snapshot_dir):
    """An EXCEPTION inside the refresh (not just a None return) also fails
    open to the stale cache."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=26.0)

    def _crash():
        raise RuntimeError("yfinance exploded")

    monkeypatch.setattr(tr, "_load_scout_module", _crash)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=6)
    assert payload is not None
    assert payload["_scout_meta"]["refresh_failed"] is True


def test_refresh_failure_no_cache_returns_none(monkeypatch, small_rules, tmp_path):
    """With NO cache at all and a failed refresh there is nothing to fail
    open to — None, and the pipeline's existing no-scout path handles it."""
    d = tmp_path / "empty" / "2026-08-14"
    d.mkdir(parents=True)
    monkeypatch.setattr(tr, "_load_scout_module", lambda: None)
    assert tr.run_thematic_research(d, ttl_hours=6) is None


def test_refresh_failed_provenance_note_renders(
        monkeypatch, small_rules, snapshot_dir):
    """The rendered scout header carries '⚠ scout cache Xh old — refresh
    failed' with the MEASURED age (rule #19)."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=26.0)
    monkeypatch.setattr(tr, "_load_scout_module", lambda: None)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=6)

    out = "\n".join(tr.render_scout_section(payload, config={}))
    assert "⚠ scout cache 26.0h old — refresh failed" in out


# ------------------------------------- (d) measured age line on headers

def test_age_line_fresh_this_run(monkeypatch, small_rules, snapshot_dir):
    """Fresh refresh → header says 'scout data: fresh this run'."""
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())
    payload = tr.run_thematic_research(snapshot_dir, refresh=True, parallel=False)
    out = "\n".join(tr.render_scout_section(payload, config={}))
    assert "scout data: fresh this run" in out
    assert "refresh failed" not in out


def test_age_line_cached_measured(monkeypatch, small_rules, snapshot_dir):
    """Cache-served payload → 'scout data: 3.2h old (cache)' — the number is
    MEASURED from the cache timestamp, never invented."""
    _build_cache(snapshot_dir, monkeypatch, small_rules, age_hours=3.2)
    monkeypatch.setattr(tr, "_load_scout_module", lambda: None)
    payload = tr.run_thematic_research(snapshot_dir, ttl_hours=6)
    # 3.2h < 6h TTL → served from cache, loader untouched.
    assert payload["_scout_meta"]["fresh"] is False
    out = "\n".join(tr.render_scout_section(payload, config={}))
    assert "scout data: 3.2h old (cache)" in out


def test_age_line_legacy_payload_measures_from_timestamp():
    """A payload WITHOUT _scout_meta (older cache readers / fixtures) still
    gets a measured age from generated_at_iso — rule #19, no fabrication."""
    ts = (datetime.now() - timedelta(hours=5.0)).isoformat()
    line = tr.scout_age_line({"generated_at_iso": ts})
    assert line.startswith("scout data: 5.0h old (cache)")
    assert tr.scout_age_line(None) == ""
    assert tr.scout_age_line({"generated_at_iso": "garbage"}) == "scout data: age unknown"


def test_candidate_report_headers_carry_age_line(
        monkeypatch, small_rules, snapshot_dir, tmp_path):
    """Both candidates surfaces (standalone report + briefing section) state
    the scout data age in their headers."""
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())
    payload = tr.run_thematic_research(snapshot_dir, refresh=True, parallel=False)

    report = cr.render_candidate_report(
        payload, fv_by_ticker={}, config={}, generated_at="2026-08-14",
        verdict_state_path=tmp_path / "verdicts.yaml")
    assert "scout data: fresh this run" in report

    briefing = cr.render_candidate_briefing(
        payload, fv_by_ticker={}, config={}, generated_at="2026-08-14",
        as_section=True)
    assert "scout data: fresh this run" in briefing


# ------------------------------- (e) pre-warm wrapper + launchd plist

def test_prewarm_wrapper_exists_and_references_refresher():
    wrapper = _SKILL_DIR / "scripts" / "run_scout_prewarm.sh"
    assert wrapper.exists()
    src = wrapper.read_text()
    assert "refresh_scout.py" in src
    assert "PORTFOLIO_BRIEFING_PYTHON" in src
    assert "OPTIONAL" in src, "wrapper must document the pipeline self-refreshes"
    refresher = _SKILL_DIR / "scripts" / "refresh_scout.py"
    assert refresher.exists()
    # The refresher must call the SAME function the pipeline uses.
    assert "run_thematic_research" in refresher.read_text()


def test_prewarm_plist_valid_and_scheduled_weekdays_0630():
    plist_path = (_SKILL_DIR / "launchd"
                  / "com.trade-analysis.scout-prewarm.plist")
    assert plist_path.exists()
    d = plistlib.loads(plist_path.read_bytes())
    assert d["Label"] == "com.trade-analysis.scout-prewarm"
    prog = d["ProgramArguments"][0]
    assert prog.endswith("scripts/run_scout_prewarm.sh")
    # The referenced wrapper exists at the same repo-relative path.
    rel = prog.split("skills/daily-portfolio-briefing/", 1)[1]
    assert (_SKILL_DIR / rel).exists()
    # Weekdays only (launchd Mon=2 .. Fri=6), 6:30 AM.
    sched = d["StartCalendarInterval"]
    assert sorted(e["Weekday"] for e in sched) == [2, 3, 4, 5, 6]
    assert all(e["Hour"] == 6 and e["Minute"] == 30 for e in sched)
    assert d.get("KeepAlive") is False


def test_refresher_compiles():
    import py_compile
    py_compile.compile(str(_SKILL_DIR / "scripts" / "refresh_scout.py"),
                       doraise=True)
