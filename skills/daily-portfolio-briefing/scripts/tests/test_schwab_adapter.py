import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import schwab_adapter  # noqa: E402

_FIX = Path(__file__).resolve().parent / "fixtures" / "schwab_accounts_raw.json"


def _raw():
    return json.loads(_FIX.read_text())


def test_parse_osi_symbol():
    sym, typ, strike, exp = schwab_adapter._parse_osi("NVDA  260619C00150000")
    assert sym == "NVDA" and typ == "CALL" and strike == 150.0 and exp == "2026-06-19"


def test_normalize_equity_position():
    pos = _raw()[0]["securitiesAccount"]["positions"][0]
    out = schwab_adapter._normalize_position(pos, "INDIVIDUAL")
    assert out["assetType"] == "EQUITY" and out["symbol"] == "NVDA"
    assert out["qty"] == 100.0 and out["accountDesc"] == "INDIVIDUAL"
    assert out["price"] == 120.0  # 12000 / 100


def test_normalize_short_option_position():
    pos = _raw()[0]["securitiesAccount"]["positions"][1]
    out = schwab_adapter._normalize_position(pos, "INDIVIDUAL")
    assert out["assetType"] == "OPTION" and out["type"] == "CALL"
    assert out["underlying"] == "NVDA" and out["strike"] == 150.0
    assert out["expiration"] == "2026-06-19"
    assert out["qty"] == -1.0           # short → negative
    assert out["positionType"] == "SHORT"
    assert out["delta"] is None         # not provided by accounts endpoint


def test_build_snapshot_scopes_by_label_whitelist():
    snap = schwab_adapter._build_snapshot(
        _raw(),
        account_labels={"123456789": "INDIVIDUAL"},
        account_desc_whitelist=["INDIVIDUAL"],
    )
    assert snap.source == "schwab"
    assert {p["symbol"] for p in snap.positions} == {"NVDA", "NVDA_CALL_150_20260619".replace("-", "")}
    assert snap.balance["accountValue"] == 250000.0
    assert len(snap.accounts) == 1 and snap.accounts[0]["accountDesc"] == "INDIVIDUAL"


def test_build_snapshot_excludes_unwhitelisted_account():
    snap = schwab_adapter._build_snapshot(
        _raw(),
        account_labels={"123456789": "JOINT"},
        account_desc_whitelist=["INDIVIDUAL"],
    )
    assert snap.positions == [] and snap.accounts == []
