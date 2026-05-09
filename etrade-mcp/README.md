# E*TRADE MCP Server

Model Context Protocol (MCP) server for E*TRADE API, enabling LLMs like Claude to retrieve stock and options market data.

## Features

- **OAuth 1.0 Authentication** - Secure, persistent token storage with automatic lifecycle management
- **Stock Quotes** - Real-time and delayed stock quotes with optional earnings dates
- **Batch Quotes** - Retrieve up to 25 stock quotes in a single request
- **Options Chains** - Full options chain data with strikes, expirations, Greeks, and bid/ask
- **Options Quotes** - Detailed quotes for specific option contracts
- **Account Management** - List accounts, get balances, retrieve positions (stocks and options)
- **Options Expirations** - Get available option expiration dates for any symbol
- **Order Management** - List open and historical orders with filtering
- **Authentication Diagnostics** - Check current auth status without forced re-authentication
- **Automatic Retries** - Built-in retry logic for handling API rate limits and token activation
- **MCP Integration** - Seamless integration with Claude Desktop and other MCP clients

## Prerequisites

- Python 3.10 or higher
- E*TRADE account
- E*TRADE API credentials ([Get them here](https://developer.etrade.com))
- `uv` package manager ([Install here](https://github.com/astral-sh/uv))

## Installation

### 1. Install uv (if not already installed)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Clone and Setup Project

```bash
cd etrade-mcp

# Create virtual environment
uv venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install dependencies
uv pip install -e .
```

### 3. Configure Environment

Create a `.env` file in the project root:

```bash
# Copy example file
cp .env.example .env

# Edit .env with your credentials
```

**.env file:**
```env
ETRADE_CONSUMER_KEY=your_consumer_key_here
ETRADE_CONSUMER_SECRET=your_consumer_secret_here
ETRADE_ENVIRONMENT=sandbox  # Use 'production' for live trading
```

**Important:**
- Start with `sandbox` environment for testing
- Switch to `production` only after thorough testing
- Production requires live E*TRADE brokerage account

## OAuth Setup

OAuth authentication is handled automatically through the MCP server. When you first use the server with Claude, you'll be prompted to authorize:

1. Claude will call the `setup_oauth` tool (no verification code needed initially)
2. Your browser will open to the E*TRADE authorization page
3. After authorizing, E*TRADE will display a 5-character verification code
4. Provide the code to Claude, who will call `setup_oauth` again with the code
5. Tokens are saved encrypted locally and automatically renewed

Alternatively, you can run the standalone OAuth setup:

```bash
python -m server.auth.setup
```

## Running Tests

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=server tests/

# Run specific test file
pytest tests/test_token_store.py -v
```

## Claude Desktop Configuration

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "etrade": {
      "command": "python",
      "args": [
        "-m",
        "server.main"
      ],
      "cwd": "/absolute/path/to/etrade-mcp",
      "env": {
        "ETRADE_CONSUMER_KEY": "your_consumer_key",
        "ETRADE_CONSUMER_SECRET": "your_consumer_secret",
        "ETRADE_ENVIRONMENT": "sandbox"
      }
    }
  }
}
```

Replace `/absolute/path/to/etrade-mcp` with your actual project path and add your credentials.

## Usage with Claude

Once configured, Claude can access E*TRADE data through natural language:

**Stock Quotes:**
- "What's the current price of Apple stock?"
- "Get quotes for AAPL, MSFT, and GOOGL with earnings dates"
- "Show me the price and volume for TSLA"

**Options Data:**
- "Show me the option chain for Tesla expiring in January 2025"
- "What are the Greeks for AAPL calls at $180 strike?"
- "Get the bid/ask spread for SPY weekly options near $450"
- "Show me put options for NVDA expiring next month"

## Available Tools

### Authentication

**`setup_oauth`** - OAuth 1.0 authentication flow
- Step 1: Call without `verification_code` to get authorization URL
- Step 2: Call with `verification_code` (5 characters from E*TRADE) to complete auth
- Tokens are automatically stored and managed

### Stock Quotes

**`get_stock_quote`** - Get quote for a single stock
- Input: `symbol` (e.g., "AAPL"), optional `include_earnings` (default: false)
- Returns: Price, volume, bid/ask, change, and optionally next earnings date

**`get_batch_quotes`** - Get quotes for multiple stocks
- Input: `symbols` (array, up to 25), optional `include_earnings` (default: false)
- Returns: Quotes for all requested symbols

### Options Data

**`get_option_chains`** - Get options chain data
- Input: `symbol`, optional filters:
  - `expiry_year`, `expiry_month`, `expiry_day` - Filter by expiration date
  - `chain_type` - "CALL", "PUT", or "CALLPUT" (default)
  - `strike_price_near` - Get strikes near a specific price
  - `no_of_strikes` - Limit number of strikes returned
  - `include_weekly` - Include weekly options (default: false)
  - `skip_adjusted` - Skip adjusted options (default: true)
  - `option_category` - "STANDARD" (default), "ALL", or "MINI"
  - `price_type` - "ATNM" (at-the-money, default) or "ALL"
- Returns: Available strikes, expirations, bid/ask, Greeks, open interest, volume

**`get_option_quote`** - Get specific option quotes
- Input: `option_symbols` (array of OSI format symbols, up to 25)
- Returns: Detailed option quote data including Greeks and theoretical values

### Account & Portfolio Management

**`list_accounts`** - List all accounts
- Returns: Array of accounts with IDs, types, and status

**`get_account_balance`** - Get account balance and buying power
- Input: `accountIdKey` (from list_accounts)
- Returns: Total value, cash, margin buying power, market values

**`get_positions`** - Get positions in an account
- Input: `accountIdKey`, optional `symbolFilter` (array), optional `assetType` (EQUITY/OPTION/ALL)
- Returns: Array of positions with quantity, cost basis, market value, P&L, and option details

**`get_option_expirations`** - Get available option expirations
- Input: `symbol`, optional `expirationType` (WEEKLY/MONTHLY/QUARTERLY/ALL)
- Returns: Array of expiration dates with type classification

**`list_orders`** - Get orders in an account
- Input: `accountIdKey`, optional `status` (OPEN/EXECUTED/CANCELED/ALL), optional `fromDate`, `toDate`, `symbolFilter`
- Returns: Array of orders with status, type, symbol, quantity, prices, and time-in-force

### Diagnostics

**`auth_status`** - Check authentication status
- Returns: Authentication status, environment, token expiration, consumer key fingerprint

## Project Structure

```
etrade-mcp/
├── server/
│   ├── auth/
│   │   ├── oauth_manager.py    # OAuth session & token lifecycle
│   │   ├── token_store.py      # Encrypted token storage
│   │   └── setup.py            # Standalone OAuth setup
│   ├── tools/
│   │   ├── oauth_tools.py      # MCP OAuth tool
│   │   ├── stock_quotes.py     # Stock quote tools
│   │   ├── options_quotes.py   # Options chain & quote tools
│   │   └── account_tools.py    # Account & portfolio tools
│   ├── utils/
│   │   └── retry.py            # Automatic retry logic
│   ├── config.py               # Configuration management
│   └── main.py                 # MCP server entry point
├── tests/
│   ├── test_token_store.py     # Token storage tests
│   ├── test_auth.py            # OAuth manager tests
│   └── test_account_tools.py   # Account tools tests
├── .env.example                # Example environment config
├── pyproject.toml              # Project metadata & dependencies
└── README.md
```

## Development Status

**✅ v0.2.0 - Account Management Tools Complete**

### Foundation
- [x] Project setup with modern tooling (uv, pyproject.toml)
- [x] Environment-based configuration management
- [x] Encrypted token storage with lifecycle management
- [x] OAuth 1.0 authentication flow
- [x] Unit tests for auth components

### Stock Market Data
- [x] Single stock quote tool with earnings support
- [x] Batch quotes (up to 25 symbols)
- [x] Real-time and delayed quote handling

### Options Data
- [x] Options chains with comprehensive filtering
- [x] Options quotes by OSI symbol
- [x] Greeks, theoretical values, and open interest
- [x] Support for standard, weekly, and mini options
- [x] Option expirations lookup with type filtering

### Account & Portfolio Management (NEW)
- [x] List all user accounts with account details
- [x] Get account balance and buying power
- [x] Retrieve positions (stocks and options) with cost basis and P&L
- [x] Get option expirations for any symbol
- [x] List orders with filtering by status and date range
- [x] Account tools comprehensive test suite (12 tests)

### MCP Integration
- [x] Two-step OAuth flow via MCP tools
- [x] Automatic retry logic for API rate limits
- [x] Token activation delay handling
- [x] Full integration with Claude Desktop
- [x] Auth status diagnostic tool

### Quality & Reliability
- [x] Comprehensive error handling
- [x] Debug logging to file and stderr
- [x] Automatic token renewal
- [x] Production-ready for E*TRADE live accounts
- [x] 100% test coverage for account tools

## Security Notes

- **Never commit `.env` file** - Contains sensitive credentials
- **Tokens are encrypted** - Stored in `.etrade_tokens.enc`
- **Use sandbox first** - Test thoroughly before production
- **Environment variable for key** - Set `ETRADE_TOKEN_KEY` in production

## Troubleshooting

### OAuth Setup Fails
- Verify consumer key and secret are correct
- Check you're using the right environment (sandbox/production)
- Ensure your E*TRADE account has API access

### "No tokens found" Error
- Run `python -m server.auth.setup` to authenticate
- Check `.etrade_tokens.enc` file exists
- Verify `ETRADE_TOKEN_KEY` is consistent

### Import Errors
- Ensure virtual environment is activated
- Run `uv pip install -e .` to install dependencies

## Recent Improvements

### Token Lifecycle Management (Latest)
- Automatic token renewal on expiration
- Graceful handling of token activation delays
- Persistent session state across restarts

### Enhanced Options Support
- Full E*TRADE API spec compliance
- Support for weekly and mini options
- Flexible filtering by expiration and strike price
- Complete Greeks and theoretical pricing data

### Reliability Enhancements
- Automatic retry logic for API rate limits
- Exponential backoff for token activation
- Comprehensive error handling and logging
- Production-tested with live E*TRADE accounts

## Resources

- [E*TRADE API Documentation](https://developer.etrade.com)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

## License

MIT

## Contributing

Contributions welcome! Please:
1. Follow test-driven development
2. Add tests for new features
3. Update documentation
4. Test with sandbox environment first
