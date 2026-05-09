# E*TRADE MCP Setup

**Status:** Short runbook for v1
**Date:** 2026-05-07

This skill integrates with the E*TRADE MCP server via subprocess calls. In v1, mock mode (fixture) is the default.

## Mock Mode (v1)

```bash
python3 scripts/run_briefing.py \
  --config config/briefing.yaml \
  --etrade-fixture assets/etrade_mock_fixture.json
```

The fixture is a JSON file with accounts, positions, balance, quotes, chains, and theses. Useful for testing without live API access.

## Live Mode (v1.1+)

When the E*TRADE MCP server is connected, the briefing will fetch live data:
- Accounts list
- Holdings by account
- Current prices and Greeks
- Open orders
- Option chains

Requirements:
- E*TRADE MCP server running and authenticated (OAuth 1.0a)
- API rate limits: 4 req/s market data, 2 req/s account data

## Fixture Schema

See `assets/etrade_mock_fixture.json` for structure. Key fields:
- `accounts`: List of account dicts (accountIdKey, accountName, balance)
- `positions`: Holdings and options (symbol, assetType, qty, price, costBasis)
- `balance`: Cash and total value
- `quotes`: Symbol → {last, bid, ask}
- `chains`: Option chains by symbol
- `open_orders`: Pending orders
- `theses`: Trader-memory-core thesis data
