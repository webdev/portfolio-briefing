# E*TRADE MCP Server - Focused Implementation Plan

## Executive Summary

This document outlines a focused plan to implement a Model Context Protocol (MCP) server for E*TRADE market data access. The initial scope focuses on three core capabilities:

1. **OAuth 1.0 Authentication** - Persistent session management for E*TRADE API
2. **Stock Data Retrieval** - Real-time and delayed stock quotes
3. **Options Data Retrieval** - Option chains and options quotes

This focused approach enables rapid development and testing while establishing the foundation for future expansion.

---

## 1. Project Overview

### 1.1 Objectives
- Create a working MCP server for E*TRADE market data retrieval
- Implement persistent OAuth 1.0 session management
- Provide stock and options quote data to LLMs
- Build a solid foundation for future feature expansion

### 1.2 Scope (Initial Release)
**In Scope:**
- OAuth 1.0 authentication with token persistence
- Stock quotes (single and batch)
- Options chains data
- Options quotes
- Basic error handling and logging

**Out of Scope (Future):**
- Account management
- Portfolio tracking
- Order preview/placement
- Advanced analytics

### 1.3 Technology Stack
- **Language**: Python 3.10+
- **MCP SDK**: `mcp` (v1.2.0+)
- **Package Manager**: `uv` for dependency management
- **Authentication**: OAuth 1.0 (rauth library)
- **Transport**: stdio (for Claude Desktop)
- **Config**: pydantic-settings for configuration management

---

## 2. Project Structure

```
etrade-mcp/
├── client/                          # Existing E*TRADE client (reference only)
│   └── src/
├── server/                          # New MCP server implementation
│   ├── __init__.py
│   ├── main.py                     # MCP server entry point
│   ├── config.py                   # Configuration management
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── oauth_manager.py       # OAuth session management
│   │   ├── token_store.py         # Secure token persistence
│   │   └── setup.py               # Initial OAuth setup script
│   └── tools/
│       ├── __init__.py
│       ├── stock_quotes.py        # Stock quote tools
│       └── options_quotes.py      # Options data tools
├── tests/
│   ├── __init__.py
│   ├── test_auth.py
│   ├── test_stock_quotes.py
│   └── test_options_quotes.py
├── .env.example                    # Environment variable template
├── pyproject.toml                  # Project dependencies
└── README.md                       # Setup and usage guide
```

---

## 3. Component Details

## 3.1 OAuth Authentication Implementation

### 3.1.1 OAuth Manager

**Purpose**: Manage OAuth 1.0 sessions with E*TRADE, handle token persistence and refresh.

**File**: `server/auth/oauth_manager.py`

```python
"""OAuth session manager for E*TRADE API."""
import webbrowser
from typing import Optional, Tuple
from rauth import OAuth1Service
from server.auth.token_store import TokenStore
from server.config import Config
import logging

logger = logging.getLogger(__name__)


class OAuthSessionManager:
    """Manages OAuth 1.0 sessions for E*TRADE API."""

    def __init__(self, config: Config):
        self.config = config
        self.token_store = TokenStore(config.token_file_path)
        self._session: Optional[OAuth1Service] = None

        # Initialize OAuth service
        self.oauth_service = OAuth1Service(
            name="etrade",
            consumer_key=config.consumer_key,
            consumer_secret=config.consumer_secret,
            request_token_url="https://api.etrade.com/oauth/request_token",
            access_token_url="https://api.etrade.com/oauth/access_token",
            authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
            base_url="https://api.etrade.com"
        )

    def get_session(self) -> OAuth1Service:
        """
        Get authenticated OAuth session.
        Returns cached session if available, otherwise initiates OAuth flow.
        """
        # Try to load existing tokens
        tokens = self.token_store.load_tokens()

        if tokens:
            logger.info("Loaded existing OAuth tokens")
            access_token, access_token_secret = tokens
            self._session = self.oauth_service.get_session((access_token, access_token_secret))
            return self._session

        # No tokens found, need to authenticate
        logger.warning("No valid tokens found. Please run OAuth setup first.")
        raise RuntimeError(
            "OAuth tokens not found. Run: python -m server.auth.setup"
        )

    def perform_oauth_flow(self) -> Tuple[str, str]:
        """
        Perform interactive OAuth 1.0 flow.
        Returns (access_token, access_token_secret)
        """
        logger.info("Starting OAuth flow")

        # Step 1: Get request token
        request_token, request_token_secret = self.oauth_service.get_request_token(
            params={"oauth_callback": "oob", "format": "json"}
        )

        # Step 2: Get user authorization
        authorize_url = self.oauth_service.authorize_url.format(
            self.oauth_service.consumer_key, request_token
        )

        print(f"\nOpening browser for E*TRADE authorization...")
        print(f"If browser doesn't open, visit: {authorize_url}\n")

        webbrowser.open(authorize_url)
        verification_code = input("Enter verification code from browser: ").strip()

        # Step 3: Exchange for access token
        session = self.oauth_service.get_auth_session(
            request_token,
            request_token_secret,
            params={"oauth_verifier": verification_code}
        )

        # Extract access token and secret
        access_token = session.access_token
        access_token_secret = session.access_token_secret

        # Save tokens
        self.token_store.save_tokens(access_token, access_token_secret)

        logger.info("OAuth flow completed successfully")
        return access_token, access_token_secret
```

### 3.1.2 Token Store

**Purpose**: Securely store and retrieve OAuth tokens.

**File**: `server/auth/token_store.py`

```python
"""Secure token storage for OAuth credentials."""
import json
import os
from pathlib import Path
from typing import Optional, Tuple
from cryptography.fernet import Fernet
import logging

logger = logging.getLogger(__name__)


class TokenStore:
    """Stores OAuth tokens securely."""

    def __init__(self, token_file_path: str = ".etrade_tokens.enc"):
        self.token_file = Path(token_file_path)
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)

    def _get_or_create_key(self) -> bytes:
        """Get encryption key from environment or create new one."""
        key_str = os.environ.get("ETRADE_TOKEN_KEY")

        if key_str:
            return key_str.encode()

        # Generate new key for development
        key = Fernet.generate_key()
        logger.warning(
            "No ETRADE_TOKEN_KEY found in environment. "
            "Generated temporary key. Set ETRADE_TOKEN_KEY in production."
        )
        logger.warning(f"Temporary key (save to .env): ETRADE_TOKEN_KEY={key.decode()}")
        return key

    def save_tokens(self, access_token: str, access_token_secret: str) -> None:
        """Save OAuth tokens to encrypted file."""
        data = {
            "access_token": access_token,
            "access_token_secret": access_token_secret
        }

        # Encrypt and save
        encrypted_data = self.cipher.encrypt(json.dumps(data).encode())
        self.token_file.write_bytes(encrypted_data)

        logger.info(f"Tokens saved to {self.token_file}")

    def load_tokens(self) -> Optional[Tuple[str, str]]:
        """Load OAuth tokens from encrypted file."""
        if not self.token_file.exists():
            return None

        try:
            # Read and decrypt
            encrypted_data = self.token_file.read_bytes()
            decrypted_data = self.cipher.decrypt(encrypted_data)
            data = json.loads(decrypted_data.decode())

            access_token = data.get("access_token")
            access_token_secret = data.get("access_token_secret")

            if access_token and access_token_secret:
                return (access_token, access_token_secret)

            return None

        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
            return None

    def clear_tokens(self) -> None:
        """Delete stored tokens."""
        if self.token_file.exists():
            self.token_file.unlink()
            logger.info("Tokens cleared")
```

### 3.1.3 OAuth Setup Script

**Purpose**: One-time OAuth setup for users.

**File**: `server/auth/setup.py`

```python
"""OAuth setup script for E*TRADE MCP server."""
import sys
from server.config import Config
from server.auth.oauth_manager import OAuthSessionManager


def main():
    """Run OAuth setup flow."""
    print("=" * 60)
    print("E*TRADE MCP Server - OAuth Setup")
    print("=" * 60)

    # Load configuration
    config = Config.from_env()

    print(f"\nEnvironment: {config.environment}")
    print(f"Base URL: {config.base_url}")

    # Confirm with user
    response = input("\nProceed with OAuth setup? (yes/no): ").strip().lower()
    if response not in ["yes", "y"]:
        print("Setup cancelled.")
        sys.exit(0)

    # Perform OAuth flow
    oauth_manager = OAuthSessionManager(config)

    try:
        access_token, access_token_secret = oauth_manager.perform_oauth_flow()
        print("\n✓ OAuth setup completed successfully!")
        print(f"✓ Tokens saved to: {oauth_manager.token_store.token_file}")
        print("\nYou can now use the MCP server with Claude Desktop.")

    except Exception as e:
        print(f"\n✗ OAuth setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## 3.2 Stock Data Retrieval

### 3.2.1 Stock Quote Tools

**File**: `server/tools/stock_quotes.py`

```python
"""Stock quote tools for MCP server."""
from typing import Any
from mcp.types import Tool, TextContent
import json
import logging

logger = logging.getLogger(__name__)


class StockQuoteTools:
    """Tools for retrieving stock quotes."""

    def __init__(self, session_manager):
        self.session_manager = session_manager

    # Tool definition
    def get_quote_tool_def(self) -> Tool:
        """Define get_stock_quote tool."""
        return Tool(
            name="get_stock_quote",
            description="Get real-time or delayed quote for a stock symbol including price, volume, bid/ask, and daily change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL, MSFT, TSLA)",
                        "pattern": "^[A-Z]{1,5}$"
                    }
                },
                "required": ["symbol"]
            }
        )

    def get_batch_quotes_tool_def(self) -> Tool:
        """Define get_batch_quotes tool."""
        return Tool(
            name="get_batch_quotes",
            description="Get quotes for multiple stock symbols in a single request (up to 25 symbols).",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^[A-Z]{1,5}$"},
                        "description": "Array of stock symbols",
                        "minItems": 1,
                        "maxItems": 25
                    }
                },
                "required": ["symbols"]
            }
        )

    # Tool implementations
    async def get_quote(self, symbol: str) -> list[TextContent]:
        """Get quote for a single stock symbol."""
        try:
            session = self.session_manager.get_session()
            base_url = self.session_manager.config.base_url

            # Call E*TRADE API
            url = f"{base_url}/v1/market/quote/{symbol}.json"
            response = session.get(url)

            logger.debug(f"GET {url} - Status: {response.status_code}")

            if response.status_code != 200:
                return [TextContent(
                    type="text",
                    text=f"Error fetching quote for {symbol}: HTTP {response.status_code}"
                )]

            data = response.json()
            quote_info = self._format_quote(data, symbol)

            return [TextContent(
                type="text",
                text=json.dumps(quote_info, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def get_batch_quotes(self, symbols: list[str]) -> list[TextContent]:
        """Get quotes for multiple symbols."""
        try:
            session = self.session_manager.get_session()
            base_url = self.session_manager.config.base_url

            # Join symbols with comma
            symbols_str = ",".join(symbols)

            # Call E*TRADE API
            url = f"{base_url}/v1/market/quote/{symbols_str}.json"
            response = session.get(url)

            logger.debug(f"GET {url} - Status: {response.status_code}")

            if response.status_code != 200:
                return [TextContent(
                    type="text",
                    text=f"Error fetching quotes: HTTP {response.status_code}"
                )]

            data = response.json()
            quotes = self._format_batch_quotes(data)

            return [TextContent(
                type="text",
                text=json.dumps(quotes, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting batch quotes: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    # Helper methods
    def _format_quote(self, data: dict, symbol: str) -> dict:
        """Format single quote response."""
        if "QuoteResponse" not in data or "QuoteData" not in data["QuoteResponse"]:
            return {"error": "Invalid quote response", "symbol": symbol}

        quotes = data["QuoteResponse"]["QuoteData"]
        if not quotes:
            return {"error": "No quote data found", "symbol": symbol}

        quote = quotes[0]
        all_data = quote.get("All", {})
        product = quote.get("Product", {})

        return {
            "symbol": product.get("symbol", symbol),
            "company_name": product.get("companyName", ""),
            "security_type": product.get("securityType", ""),
            "last_price": all_data.get("lastTrade"),
            "change": all_data.get("changeClose"),
            "change_percent": all_data.get("changeClosePercentage"),
            "bid": all_data.get("bid"),
            "bid_size": all_data.get("bidSize"),
            "ask": all_data.get("ask"),
            "ask_size": all_data.get("askSize"),
            "volume": all_data.get("totalVolume"),
            "day_high": all_data.get("high"),
            "day_low": all_data.get("low"),
            "open": all_data.get("open"),
            "previous_close": all_data.get("previousClose"),
            "timestamp": quote.get("dateTime")
        }

    def _format_batch_quotes(self, data: dict) -> dict:
        """Format batch quotes response."""
        if "QuoteResponse" not in data or "QuoteData" not in data["QuoteResponse"]:
            return {"error": "Invalid quote response", "quotes": []}

        quotes = data["QuoteResponse"]["QuoteData"]
        formatted_quotes = []

        for quote in quotes:
            symbol = quote.get("Product", {}).get("symbol", "UNKNOWN")
            formatted_quotes.append(self._format_quote({"QuoteResponse": {"QuoteData": [quote]}}, symbol))

        return {
            "count": len(formatted_quotes),
            "quotes": formatted_quotes
        }
```

---

## 3.3 Options Data Retrieval

### 3.3.1 Options Quote Tools

**File**: `server/tools/options_quotes.py`

```python
"""Options data tools for MCP server."""
from typing import Any
from mcp.types import Tool, TextContent
import json
import logging

logger = logging.getLogger(__name__)


class OptionsQuoteTools:
    """Tools for retrieving options chains and quotes."""

    def __init__(self, session_manager):
        self.session_manager = session_manager

    # Tool definitions
    def get_option_chains_tool_def(self) -> Tool:
        """Define get_option_chains tool."""
        return Tool(
            name="get_option_chains",
            description="Get options chain data for a stock symbol, including available expiration dates and strike prices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Underlying stock symbol",
                        "pattern": "^[A-Z]{1,5}$"
                    },
                    "expiry_month": {
                        "type": "string",
                        "description": "Optional expiration month (e.g., '2025-01'). If not specified, returns nearest expirations.",
                        "pattern": "^\\d{4}-\\d{2}$"
                    },
                    "option_type": {
                        "type": "string",
                        "enum": ["CALL", "PUT", "ALL"],
                        "description": "Type of options to retrieve (default: ALL)"
                    }
                },
                "required": ["symbol"]
            }
        )

    def get_option_quote_tool_def(self) -> Tool:
        """Define get_option_quote tool."""
        return Tool(
            name="get_option_quote",
            description="Get detailed quote for specific option contracts by option symbol.",
            inputSchema={
                "type": "object",
                "properties": {
                    "option_symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of option symbols (OSI format)",
                        "minItems": 1,
                        "maxItems": 25
                    }
                },
                "required": ["option_symbols"]
            }
        )

    # Tool implementations
    async def get_option_chains(
        self,
        symbol: str,
        expiry_month: str | None = None,
        option_type: str = "ALL"
    ) -> list[TextContent]:
        """Get options chain for a symbol."""
        try:
            session = self.session_manager.get_session()
            base_url = self.session_manager.config.base_url

            # Build query parameters
            params = {"symbol": symbol}

            if expiry_month:
                params["expiryMonth"] = expiry_month

            if option_type != "ALL":
                params["chainType"] = option_type

            # Call E*TRADE API
            url = f"{base_url}/v1/market/optionchains"
            response = session.get(url, params=params)

            logger.debug(f"GET {url} - Status: {response.status_code}")

            if response.status_code != 200:
                return [TextContent(
                    type="text",
                    text=f"Error fetching option chains for {symbol}: HTTP {response.status_code}"
                )]

            data = response.json()
            chains_info = self._format_option_chains(data, symbol)

            return [TextContent(
                type="text",
                text=json.dumps(chains_info, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting option chains for {symbol}: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def get_option_quote(self, option_symbols: list[str]) -> list[TextContent]:
        """Get quotes for specific option contracts."""
        try:
            session = self.session_manager.get_session()
            base_url = self.session_manager.config.base_url

            # Join symbols with comma
            symbols_str = ",".join(option_symbols)

            # Call E*TRADE API (same endpoint as stocks)
            url = f"{base_url}/v1/market/quote/{symbols_str}.json"
            response = session.get(url)

            logger.debug(f"GET {url} - Status: {response.status_code}")

            if response.status_code != 200:
                return [TextContent(
                    type="text",
                    text=f"Error fetching option quotes: HTTP {response.status_code}"
                )]

            data = response.json()
            quotes = self._format_option_quotes(data)

            return [TextContent(
                type="text",
                text=json.dumps(quotes, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting option quotes: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    # Helper methods
    def _format_option_chains(self, data: dict, symbol: str) -> dict:
        """Format option chains response."""
        if "OptionChainResponse" not in data:
            return {"error": "Invalid option chains response", "symbol": symbol}

        response_data = data["OptionChainResponse"]

        formatted = {
            "symbol": symbol,
            "quote_type": response_data.get("quoteType"),
            "option_pair_count": response_data.get("OptionPair", []),
            "expirations": []
        }

        # Extract expiration dates and strikes
        option_pairs = response_data.get("OptionPair", [])

        for pair in option_pairs:
            expiration_info = {
                "expiry_date": pair.get("Call", {}).get("expiryDate") or pair.get("Put", {}).get("expiryDate"),
                "days_to_expiration": pair.get("Call", {}).get("daysToExpiration") or pair.get("Put", {}).get("daysToExpiration"),
                "call": None,
                "put": None
            }

            # Format call data
            if "Call" in pair:
                call = pair["Call"]
                expiration_info["call"] = {
                    "symbol": call.get("symbol"),
                    "strike": call.get("strikePrice"),
                    "bid": call.get("bid"),
                    "ask": call.get("ask"),
                    "last": call.get("lastPrice"),
                    "volume": call.get("volume"),
                    "open_interest": call.get("openInterest"),
                    "implied_volatility": call.get("impliedVolatility")
                }

            # Format put data
            if "Put" in pair:
                put = pair["Put"]
                expiration_info["put"] = {
                    "symbol": put.get("symbol"),
                    "strike": put.get("strikePrice"),
                    "bid": put.get("bid"),
                    "ask": put.get("ask"),
                    "last": put.get("lastPrice"),
                    "volume": put.get("volume"),
                    "open_interest": put.get("openInterest"),
                    "implied_volatility": put.get("impliedVolatility")
                }

            formatted["expirations"].append(expiration_info)

        return formatted

    def _format_option_quotes(self, data: dict) -> dict:
        """Format option quotes response."""
        if "QuoteResponse" not in data or "QuoteData" not in data["QuoteResponse"]:
            return {"error": "Invalid quote response", "quotes": []}

        quotes = data["QuoteResponse"]["QuoteData"]
        formatted_quotes = []

        for quote in quotes:
            product = quote.get("Product", {})
            all_data = quote.get("All", {})

            formatted_quotes.append({
                "symbol": product.get("symbol"),
                "option_type": product.get("callPut"),
                "strike_price": product.get("strikePrice"),
                "expiry_date": product.get("expiryDate"),
                "underlying_symbol": product.get("underlyingSymbol"),
                "last_price": all_data.get("lastTrade"),
                "bid": all_data.get("bid"),
                "ask": all_data.get("ask"),
                "volume": all_data.get("totalVolume"),
                "open_interest": all_data.get("openInterest"),
                "implied_volatility": all_data.get("impliedVolatility"),
                "delta": all_data.get("delta"),
                "gamma": all_data.get("gamma"),
                "theta": all_data.get("theta"),
                "vega": all_data.get("vega"),
                "timestamp": quote.get("dateTime")
            })

        return {
            "count": len(formatted_quotes),
            "quotes": formatted_quotes
        }
```

---

## 3.4 Configuration

**File**: `server/config.py`

```python
"""Configuration management for E*TRADE MCP server."""
from pydantic_settings import BaseSettings
from typing import Literal


class Config(BaseSettings):
    """MCP Server configuration."""

    # E*TRADE API credentials
    consumer_key: str
    consumer_secret: str

    # Environment selection
    environment: Literal["sandbox", "production"] = "sandbox"

    # Token storage
    token_file_path: str = ".etrade_tokens.enc"

    # Logging
    log_level: str = "INFO"
    log_file: str = "etrade_mcp.log"

    @property
    def base_url(self) -> str:
        """Get base URL based on environment."""
        if self.environment == "production":
            return "https://api.etrade.com"
        return "https://apisb.etrade.com"

    class Config:
        env_prefix = "ETRADE_"
        env_file = ".env"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls()
```

---

## 3.5 Main MCP Server

**File**: `server/main.py`

```python
"""E*TRADE MCP Server - Main entry point."""
import asyncio
import logging
import sys
from mcp.server import Server
from mcp.server.stdio import stdio_server

from server.config import Config
from server.auth.oauth_manager import OAuthSessionManager
from server.tools.stock_quotes import StockQuoteTools
from server.tools.options_quotes import OptionsQuoteTools


# Configure logging for MCP (use stderr, not stdout!)
def setup_logging(config: Config):
    """Setup logging to file and stderr."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(config.log_file),
            logging.StreamHandler(sys.stderr)  # Important: use stderr, not stdout!
        ]
    )


async def main():
    """Main server entry point."""
    # Load configuration
    config = Config.from_env()
    setup_logging(config)

    logger = logging.getLogger(__name__)
    logger.info("Starting E*TRADE MCP Server")
    logger.info(f"Environment: {config.environment}")

    # Initialize OAuth session manager
    try:
        session_manager = OAuthSessionManager(config)
    except Exception as e:
        logger.error(f"Failed to initialize OAuth manager: {e}")
        sys.exit(1)

    # Initialize tool handlers
    stock_tools = StockQuoteTools(session_manager)
    options_tools = OptionsQuoteTools(session_manager)

    # Create MCP server
    server = Server("etrade-mcp")

    # Register list_tools handler
    @server.list_tools()
    async def list_tools():
        """List available tools."""
        return [
            stock_tools.get_quote_tool_def(),
            stock_tools.get_batch_quotes_tool_def(),
            options_tools.get_option_chains_tool_def(),
            options_tools.get_option_quote_tool_def(),
        ]

    # Register call_tool handler
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        """Handle tool calls."""
        logger.info(f"Tool called: {name} with args: {arguments}")

        try:
            if name == "get_stock_quote":
                return await stock_tools.get_quote(arguments["symbol"])

            elif name == "get_batch_quotes":
                return await stock_tools.get_batch_quotes(arguments["symbols"])

            elif name == "get_option_chains":
                return await options_tools.get_option_chains(
                    symbol=arguments["symbol"],
                    expiry_month=arguments.get("expiry_month"),
                    option_type=arguments.get("option_type", "ALL")
                )

            elif name == "get_option_quote":
                return await options_tools.get_option_quote(arguments["option_symbols"])

            else:
                raise ValueError(f"Unknown tool: {name}")

        except Exception as e:
            logger.error(f"Error executing tool {name}: {e}")
            raise

    # Run server with stdio transport
    logger.info("Server ready, starting stdio transport")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Dependencies

**File**: `pyproject.toml`

```toml
[project]
name = "etrade-mcp"
version = "0.1.0"
description = "E*TRADE MCP Server for stock and options data"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.2.0",
    "rauth>=0.7.3",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "cryptography>=41.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
]

[project.scripts]
etrade-mcp = "server.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## 5. Setup & Usage

### 5.1 Installation

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup project
cd etrade-mcp
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
uv pip install -e .
```

### 5.2 Configuration

Create `.env` file:

```bash
# .env
ETRADE_CONSUMER_KEY=your_consumer_key_here
ETRADE_CONSUMER_SECRET=your_consumer_secret_here
ETRADE_ENVIRONMENT=sandbox
ETRADE_LOG_LEVEL=INFO
```

### 5.3 OAuth Setup (One-Time)

```bash
# Run OAuth setup
python -m server.auth.setup

# Follow prompts:
# 1. Browser will open for E*TRADE authorization
# 2. Login and accept agreement
# 3. Enter verification code
# 4. Tokens will be saved encrypted
```

### 5.4 Claude Desktop Configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "etrade": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/etrade-mcp",
        "run",
        "python",
        "-m",
        "server.main"
      ]
    }
  }
}
```

### 5.5 Testing

```bash
# Test OAuth setup
python -m server.auth.setup

# Run tests (when implemented)
pytest tests/
```

---

## 6. Implementation Timeline

### Week 1: Foundation
- **Days 1-2**: Project setup, dependencies, configuration
- **Days 3-4**: OAuth manager and token store implementation
- **Day 5**: OAuth setup script and testing

### Week 2: Stock Data
- **Days 1-2**: Stock quote tools implementation
- **Days 3-4**: Testing with E*TRADE sandbox
- **Day 5**: Claude Desktop integration testing

### Week 3: Options Data
- **Days 1-3**: Options chains and quotes implementation
- **Days 4-5**: Testing and refinement

### Week 4: Polish & Documentation
- **Days 1-2**: Error handling improvements
- **Days 3-4**: Documentation and examples
- **Day 5**: Final testing and release

---

## 7. Example Usage

### Example 1: Stock Quote

```
User: "What's the current price of Apple stock?"

Claude: [Calls get_stock_quote with symbol="AAPL"]

Response:
{
  "symbol": "AAPL",
  "company_name": "Apple Inc",
  "last_price": 178.45,
  "change": 2.34,
  "change_percent": 1.33,
  "bid": 178.42,
  "ask": 178.47,
  "volume": 52430000,
  "day_high": 179.20,
  "day_low": 176.80
}

Apple stock (AAPL) is currently trading at $178.45, up $2.34 (+1.33%) today...
```

### Example 2: Options Chain

```
User: "Show me the option chain for Tesla"

Claude: [Calls get_option_chains with symbol="TSLA"]

I found 45 option pairs for TSLA. Here are the nearest expirations:

Jan 19, 2025 (7 days):
- $250 Strike: Call $8.50 / Put $3.20
- $255 Strike: Call $5.30 / Put $5.80
...
```

### Example 3: Batch Quotes

```
User: "Compare prices for AAPL, MSFT, and GOOGL"

Claude: [Calls get_batch_quotes with symbols=["AAPL", "MSFT", "GOOGL"]]

Here's the comparison:
- AAPL: $178.45 (+1.33%)
- MSFT: $425.12 (+0.87%)
- GOOGL: $142.35 (-0.45%)
```

---

## 8. Security Considerations

1. **Token Storage**: Tokens encrypted with Fernet symmetric encryption
2. **Environment Variables**: Sensitive data in .env (not committed)
3. **Logging**: Never log OAuth tokens or secrets
4. **Sandbox First**: Always test with sandbox before production
5. **Read-Only**: This implementation is read-only (no trading)

---

## 9. Future Enhancements

After initial release:
- Account balance and portfolio tools
- Caching for frequently requested quotes
- WebSocket for real-time quotes
- Advanced options analytics (Greeks, IV surfaces)
- Historical data access
- Alert/notification system

---

## 10. Success Criteria

- ✅ OAuth flow completes successfully
- ✅ Stock quotes retrieved accurately
- ✅ Options chains data available
- ✅ Integration with Claude Desktop works
- ✅ Error handling is robust
- ✅ Documentation is clear and complete

---

## Conclusion

This focused implementation plan provides a clear path to building a functional E*TRADE MCP server for market data retrieval. By concentrating on OAuth, stock quotes, and options data, we establish a solid foundation while delivering immediate value to users who want to analyze market data using LLMs.

The modular architecture makes it easy to extend with additional features in future iterations.
