"""Task #44 — ignored-rec forward ledger (🤖 vs 🧠).

Pins: recording of EXECUTED/IGNORED dispositions (PARTIAL/UNVERIFIED never
recorded), rec-day marks from the prior snapshot, dedupe on re-runs,
forward-marking at +7d/+30d with estimate labels, mark-unavailable honesty,
outcome scoring semantics for short options, and the benchmark-section
panel incl. the <20 sample caveat.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import ignored_ledger as il  # noqa: E402


def _short_put(symbol="MU_PUT_700_20261218", mid=12.50, qty=-1):
    return {"symbol": symbol, "assetType": "OPTION", "type": "PUT",
            "underlying": symbol.split("_")[0], "strike": 700.0,
            "expiration": "2026-12-18", "qty": qty, "currentMid": mid}


def _equity(symbol="NVDA", price=182.0, qty=100):
    return {"symbol": symbol, "assetType": "EQUITY", "qty": qty,
            "price": price}


RECON = {
    "CLOSE:MU_PUT_700_20261218": "IGNORED",
    "TRIM:NVDA": "EXECUTED",
    "ROLL_OUT:VRT_PUT_280_20260918": "UNVERIFIED",
    "CLOSE:AMD_PUT_420_20261218": "PARTIAL",
}
PREV_POS = [_short_put(), _equity()]


def test_record_only_executed_and_ignored():
    ledger = {"entries": []}
    added = il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    assert added == 2
    keys = {e["key"] for e in ledger["entries"]}
    assert keys == {"CLOSE:MU_PUT_700_20261218", "TRIM:NVDA"}
    mu = next(e for e in ledger["entries"] if e["ident"].startswith("MU"))
    assert mu["disposition"] == "IGNORED"
    assert mu["rec_mark"] == 12.50            # rec-day currentMid
    assert mu["side"] == "short_put"
    nv = next(e for e in ledger["entries"] if e["ident"] == "NVDA")
    assert nv["disposition"] == "EXECUTED"
    assert nv["rec_mark"] == 182.0
    assert nv["side"] == "equity"


def test_record_dedupes_on_rerun():
    ledger = {"entries": []}
    il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    added = il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    assert added == 0 and len(ledger["entries"]) == 2
    # A NEW rec day appends fresh entries.
    added = il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-02")
    assert added == 2 and len(ledger["entries"]) == 4


def test_forward_mark_7d_then_30d():
    ledger = {"entries": []}
    il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    # +9 days: only the 7d horizon stamps (approximate, labeled estimate).
    n = il.forward_mark(ledger, [_short_put(mid=8.00)], "2026-07-10")
    mu = next(e for e in ledger["entries"] if e["ident"].startswith("MU"))
    assert "7d" in mu["marks"] and "30d" not in mu["marks"]
    assert mu["marks"]["7d"]["mark"] == 8.00
    assert mu["marks"]["7d"]["estimated"] is True
    assert mu["marks"]["7d"]["change_ps"] == -4.50
    assert n == 2                              # MU + NVDA both stamped
    # +35 days: 30d stamps; 7d never re-stamped.
    il.forward_mark(ledger, [_short_put(mid=3.00)], "2026-08-05")
    assert mu["marks"]["30d"]["change_ps"] == -9.50
    assert mu["marks"]["7d"]["mark"] == 8.00


def test_forward_mark_position_gone_is_honest():
    """Contract not in today's snapshot → mark null with the reason —
    never an estimated value."""
    ledger = {"entries": []}
    il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    il.forward_mark(ledger, [], "2026-08-05")
    mu = next(e for e in ledger["entries"] if e["ident"].startswith("MU"))
    assert mu["marks"]["30d"]["mark"] is None
    assert "not in snapshot" in mu["marks"]["30d"]["basis"]
    assert "change_ps" not in mu["marks"]["30d"]


def test_outcome_scoring_semantics():
    """Short-put IGNORED close: mark fell 12.50 → 3.00 → holding banked
    +$950/contract (ignoring paid). The same drift on an EXECUTED close
    scores −$950 (closed early, decay left on the table)."""
    ledger = {"entries": []}
    recon = {"CLOSE:MU_PUT_700_20261218": "IGNORED",
             "CLOSE:AMD_PUT_420_20261218": "EXECUTED"}
    prev = [_short_put(),
            _short_put(symbol="AMD_PUT_420_20261218", mid=12.50)]
    il.record_dispositions(ledger, recon, prev, "2026-07-01")
    today = [_short_put(mid=3.00),
             _short_put(symbol="AMD_PUT_420_20261218", mid=3.00)]
    il.forward_mark(ledger, today, "2026-08-05")
    stats = il.compute_stats(ledger, 30)
    assert stats["ignored"]["avg_value"] == 950.0
    assert stats["executed"]["avg_value"] == -950.0


def test_equity_entries_are_unscored():
    ledger = {"entries": []}
    il.record_dispositions(ledger, {"TRIM:NVDA": "EXECUTED"},
                           [_equity()], "2026-07-01")
    il.forward_mark(ledger, [_equity(price=200.0)], "2026-08-05")
    stats = il.compute_stats(ledger, 30)
    assert stats["executed"]["scored"] == 0
    assert stats["unscored"] == 1


def test_panel_renders_counts_and_sample_caveat():
    ledger = {"entries": []}
    il.record_dispositions(ledger, RECON, PREV_POS, "2026-07-01")
    il.forward_mark(ledger, [_short_put(mid=3.00)], "2026-08-05")
    md = "\n".join(il.render_outcomes_panel(ledger, as_of="2026-08-05"))
    assert "🤖 vs 🧠 — Recommendation Outcomes" in md
    assert "**Ignored:** 1 rec(s)" in md
    assert "+$950/contract" in md
    assert "insufficient sample" in md         # 1 scored < 20
    assert "ESTIMATES from snapshot marks" in md


def test_panel_empty_ledger_renders_nothing():
    assert il.render_outcomes_panel({"entries": []}) == []


def test_update_and_render_persists(tmp_path):
    snapshot_dir = tmp_path / "state" / "briefing_snapshots" / "2026-07-02"
    snapshot_dir.mkdir(parents=True)
    aging = {"reconciliation": RECON, "prev_positions": PREV_POS,
             "prev_date": "2026-07-01"}
    panel = il.update_and_render(snapshot_dir, aging, PREV_POS, "2026-07-02")
    ledger_path = tmp_path / "state" / "rec_outcome_ledger.json"
    assert ledger_path.exists()
    assert len(il.load_ledger(ledger_path)["entries"]) == 2
    assert any("Recommendation Outcomes" in ln for ln in panel)


def test_test_snapshot_dir_gets_test_ledger(tmp_path):
    sd = tmp_path / "state" / "briefing_snapshots" / "2026-07-02.test"
    assert il.default_ledger_path(sd).name == "rec_outcome_ledger.test.json"
    sd2 = tmp_path / "state" / "briefing_snapshots" / "2026-07-02"
    assert il.default_ledger_path(sd2).name == "rec_outcome_ledger.json"
