"""Regression tests for the 2026-08-04 live-run failure.

Observed output (briefing_2026-08-04.log):

    FATAL: Briefing failed at step: Object of type Decimal is not JSON serializable
      File ".../run_briefing.py", line 598, in main
        json.dump(briefing_json, f, indent=2)
    TypeError: Object of type Decimal is not JSON serializable

Root cause: the NLV reconciliation fix passed the E*TRADE adapter's
``totalAccountValue`` (a ``Decimal`` from pyetrade) through
``_compose_balance`` into ``briefing_json``. Two-layer fix pinned here:

1. Boundary coercion — ``_compose_balance`` converts every Decimal in the
   adapter balance dict to float before it enters snapshot_data.
2. Belt-and-suspenders — every pipeline ``json.dump`` passes
   ``default=json_default`` (analysis/json_utils.py) so a stray Decimal /
   date / Path / set can never kill a live run again.
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.json_utils import json_default  # noqa: E402
from steps.snapshot_inputs import _compose_balance  # noqa: E402


# ── Layer 1: boundary coercion in _compose_balance ──────────────────────

def test_compose_balance_decimal_inputs_come_out_as_floats():
    """Adapter Decimals (totalAccountValue, cash, buying power, ...) must be
    coerced to float — the whole balance dict, not just the NLV field."""
    base = {
        "cash": Decimal("76207.42"),
        "totalAccountValue": Decimal("1087501.55"),
        "netCash": Decimal("76207.42"),
        "marginBuyingPower": Decimal("152414.84"),
        "accountDesc": "INDIVIDUAL",
        "nested": {"optionLevel": Decimal("3")},
        "lots": [Decimal("1.5"), 2],
    }
    balance = _compose_balance(base, 950000.0, -65000.0)

    def _no_decimals(v):
        if isinstance(v, Decimal):
            return False
        if isinstance(v, dict):
            return all(_no_decimals(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return all(_no_decimals(x) for x in v)
        return True

    assert _no_decimals(balance), f"Decimal leaked through: {balance}"
    assert isinstance(balance["cash"], float)
    assert isinstance(balance["totalAccountValue"], float)
    # Broker truth wins as accountValue, as a float
    assert balance["accountValue"] == pytest.approx(1087501.55)
    assert isinstance(balance["accountValue"], float)
    # And the composed dict must dump with a PLAIN json.dump (no default hook)
    json.dumps(balance)


def test_compose_balance_float_inputs_unchanged():
    """Float/fixture path is byte-identical — coercion is a no-op on floats."""
    base = {"cash": 1000.0, "totalAccountValue": 0}
    balance = _compose_balance(base, 5000.0, -500.0)
    assert balance["accountValue"] == pytest.approx(5500.0)
    assert balance["computedAccountValue"] == pytest.approx(5500.0)
    json.dumps(balance)


# ── Layer 2: shared json_default encoder ────────────────────────────────

def test_briefing_json_with_decimal_and_date_dumps_cleanly():
    """A briefing_json carrying a stray Decimal in balance AND a date must
    dump cleanly via default=json_default (the run_briefing Step 10 path)."""
    briefing_json = {
        "date": "2026-08-04",
        "balance": {
            "accountValue": Decimal("1087501.55"),
            "cash": Decimal("76207.42"),
        },
        "generated_at": datetime(2026, 8, 4, 11, 58, 12),
        "as_of": date(2026, 8, 4),
        "snapshot_dir": Path("state/briefing_snapshots/2026-08-04"),
        "tickers_seen": {"NVDA", "AMZN"},
    }
    out = json.dumps(briefing_json, indent=2, default=json_default)
    parsed = json.loads(out)
    assert parsed["balance"]["accountValue"] == pytest.approx(1087501.55)
    assert parsed["generated_at"] == "2026-08-04T11:58:12"
    assert parsed["as_of"] == "2026-08-04"
    assert parsed["tickers_seen"] == ["AMZN", "NVDA"]  # sorted list
    assert "briefing_snapshots" in parsed["snapshot_dir"]


def test_json_default_conversions_and_strictness():
    assert json_default(Decimal("1.25")) == 1.25
    assert json_default(date(2026, 8, 4)) == "2026-08-04"
    assert json_default(datetime(2026, 8, 4, 9, 30)) == "2026-08-04T09:30:00"
    assert json_default(Path("a/b")) == str(Path("a/b"))
    assert json_default({"b", "a"}) == ["a", "b"]
    with pytest.raises(TypeError):
        json_default(object())  # unknown types still fail loudly (in tests)
