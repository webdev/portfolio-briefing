import importlib.util
import sys
from pathlib import Path

_FETCH = (Path(__file__).resolve().parents[3]
          / "etrade-chain-fetcher" / "scripts" / "fetch.py")


def _load_fetch(monkeypatch, broker):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_BROKER", broker)
    sys.modules.pop("etrade_chain_fetcher_test", None)
    spec = importlib.util.spec_from_file_location("etrade_chain_fetcher_test", _FETCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etrade_chain_fetcher_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fetcher_uses_schwab_backend_when_env_set(monkeypatch):
    mod = _load_fetch(monkeypatch, "schwab")
    assert mod._backend_name() == "schwab"


def test_fetcher_defaults_to_etrade(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BRIEFING_BROKER", raising=False)
    mod = _load_fetch(monkeypatch, "etrade")
    assert mod._backend_name() == "etrade"


import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from steps import snapshot_inputs  # noqa: E402


def test_select_snapshot_adapter_defaults_to_etrade():
    fn = snapshot_inputs._select_snapshot_adapter({})
    assert fn.__name__ == "fetch_etrade_snapshot"


def test_select_snapshot_adapter_schwab():
    fn = snapshot_inputs._select_snapshot_adapter({"brokerage": "schwab"})
    assert fn.__name__ == "fetch_schwab_snapshot"


import importlib


def test_broker_market_reexports_by_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_BROKER", "etrade")
    from adapters import broker_market
    importlib.reload(broker_market)
    assert hasattr(broker_market, "get_option_chain")
    assert hasattr(broker_market, "get_option_expirations")
    assert hasattr(broker_market, "find_put_strike_near")
