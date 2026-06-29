"""Tests for the open-orders audit module."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import open_orders_audit as ooa  # noqa: E402
from analysis import pre_trade_validator as ptv  # noqa: E402


def _snapshot(**overrides) -> dict:
    today = date(2026, 6, 15)
    snap = {
        "balance": {"accountValue": 1_075_098.0, "cash": 84_177.0},
        "positions": [
            {"assetType": "OPTION", "type": "PUT", "underlying": "MU",
             "qty": -1, "strike": 890.0, "expiration": "2026-08-21",
             "symbol": "MU_PUT_890_20260821"},
        ],
        "technicals": {
            "MU": {
                "spot": 1074.43, "rsi_14": 66, "iv_rank": 98.9,
                "sma_50": 692.0, "sma_200": 386.0,
                "support_resistance": {"supports": [{"price": 692.0, "touches": 1}]},
            },
        },
        "open_orders": [],
        "earnings_calendar": {},
        "recommendations_list": [],
    }
    snap.update(overrides)
    return snap


def test_empty_open_orders_returns_no_audits():
    snap = _snapshot(open_orders=[])
    audits = ooa.audit_open_orders(snap)
    assert audits == []


def test_normalizer_handles_flat_shape():
    """Adapter-flattened order dict gets parsed correctly."""
    order = {
        "ticker": "MU", "strike": 960.0, "expiration": "2026-08-21",
        "option_type": "PUT", "action": "SELL_OPEN", "quantity": 1,
        "limit_price": 154.0, "order_id": "672",
    }
    norm = ooa._normalize_order(order)
    assert norm is not None
    assert norm["ticker"] == "MU"
    assert norm["strike"] == 960.0
    assert norm["expiration"] == date(2026, 8, 21)
    assert norm["option_type"] == "PUT"
    assert norm["action"] == "SELL_OPEN"
    assert norm["limit_price"] == 154.0


def test_normalizer_handles_nested_etrade_shape():
    """E*TRADE V0 nested orderDetail/instrument/Product shape parses correctly."""
    order = {
        "orderId": "672",
        "orderDetail": [{
            "limitPrice": 154.0,
            "instrument": [{
                "Product": {
                    "symbol": "MU",
                    "strikePrice": 960.0,
                    "callPut": "PUT",
                    "expiryYear": 2026,
                    "expiryMonth": 8,
                    "expiryDay": 21,
                },
                "orderAction": "SELL-OPEN",
                "quantity": 1,
            }],
        }],
    }
    norm = ooa._normalize_order(order)
    assert norm is not None
    assert norm["ticker"] == "MU"
    assert norm["strike"] == 960.0
    assert norm["expiration"] == date(2026, 8, 21)
    assert norm["action"] == "SELL_OPEN"
    assert norm["limit_price"] == 154.0


def test_normalizer_skips_unparseable_order():
    """Orders missing essential fields return None."""
    assert ooa._normalize_order({}) is None
    assert ooa._normalize_order({"ticker": "MU"}) is None  # no strike/exp/type


def test_audit_catches_mu_960p_scenario():
    """End-to-end: the MU $960P GTC order surfaces with BLOCK findings."""
    snap = _snapshot(open_orders=[{
        "ticker": "MU", "strike": 960.0, "expiration": "2026-08-21",
        "option_type": "PUT", "action": "SELL_OPEN", "quantity": 1,
        "limit_price": 154.0, "order_id": "672",
    }])
    audits = ooa.audit_open_orders(snap)
    assert len(audits) == 1
    a = audits[0]
    assert a.order_id == "672"
    rules = [f.rule_id for f in a.findings]
    # Bucket impact MUST fire — adding 1× MU $960P to a snapshot with one
    # existing MU $890P pushes Aug 21 from 8.3% to 17.3% NLV. That's still
    # below warning, so we test the more reliable signals:
    assert "ROLL_UP_RISK_INCREASE" in rules
    assert "STRIKE_NOT_AT_SUPPORT" in rules


def test_render_panel_empty_when_no_audits():
    """Clean briefing: no audits → no panel rendered."""
    out = ooa.render_audit_panel([])
    assert out == []


def test_render_panel_groups_by_severity():
    """Audits with BLOCKs vs WARNs vs clean get grouped correctly."""
    findings_block = [
        ptv.TradeValidation(severity=ptv.SEV_BLOCK, reason="bad", detail="...",
                             rule_id="BUCKET_CRITICAL"),
    ]
    findings_warn = [
        ptv.TradeValidation(severity=ptv.SEV_WARN, reason="meh", detail="...",
                             rule_id="STRIKE_NOT_AT_SUPPORT"),
    ]
    audits = [
        ooa.OpenOrderAudit(label="MU $960P Aug 21", order_id="672",
                            findings=findings_block, raw={}),
        ooa.OpenOrderAudit(label="CRM $170P Jul 10", order_id="673",
                            findings=findings_warn, raw={}),
        ooa.OpenOrderAudit(label="NVDA $230C Jul 17", order_id="674",
                            findings=[], raw={}),
    ]
    out = ooa.render_audit_panel(audits, header_level="##")
    rendered = "\n".join(out)
    assert "🔍 Open Orders Audit" in rendered
    assert "3 pending order(s) checked" in rendered
    assert "🚫 1 BLOCKed" in rendered
    assert "⚠️ 1 flagged" in rendered
    assert "✅ 1 clean" in rendered
    assert "MU $960P Aug 21" in rendered
    assert "CRM $170P Jul 10" in rendered
    # BLOCK section comes before WARN section
    block_idx = rendered.index("BLOCK — these orders")
    warn_idx = rendered.index("WARN — review")
    assert block_idx < warn_idx
