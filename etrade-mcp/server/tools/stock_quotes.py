"""Stock quote tools for MCP server."""
from typing import Any
from mcp.types import Tool, TextContent
import json
import logging
from server.utils.retry import retry_with_backoff

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
            description="Get real-time or delayed quote for a stock symbol including price, volume, bid/ask, daily change, and optionally earnings date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL, MSFT, TSLA)",
                        "pattern": "^[A-Z]{1,5}$"
                    },
                    "include_earnings": {
                        "type": "boolean",
                        "description": "Include next earnings call date if available (default: false)",
                        "default": False
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
                    },
                    "include_earnings": {
                        "type": "boolean",
                        "description": "Include next earnings call date if available (default: false)",
                        "default": False
                    }
                },
                "required": ["symbols"]
            }
        )

    # Tool implementations
    async def get_quote(self, symbol: str, include_earnings: bool = False) -> list[TextContent]:
        """Get quote for a single stock symbol."""
        try:
            # Use retry logic to handle token activation delays
            async def _fetch_quote():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Build query parameters
                params = {}
                if include_earnings:
                    params["requireEarningsDate"] = "true"

                # Call E*TRADE API
                url = f"{base_url}/v1/market/quote/{symbol}.json"
                response = session.get(url, params=params)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError(f"Authentication failed (401). Token may still be activating.")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_quote, max_attempts=3, initial_delay=5.0)
            quote_info = self._format_quote(data, symbol, include_earnings)

            return [TextContent(
                type="text",
                text=json.dumps(quote_info, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}\n\nIf this is an authentication error, the OAuth token may still be activating. Please wait 30-60 seconds and try again."
            )]

    async def get_batch_quotes(self, symbols: list[str], include_earnings: bool = False) -> list[TextContent]:
        """Get quotes for multiple symbols."""
        try:
            # Use retry logic to handle token activation delays
            async def _fetch_quotes():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Join symbols with comma
                symbols_str = ",".join(symbols)

                # Build query parameters
                params = {}
                if include_earnings:
                    params["requireEarningsDate"] = "true"

                # Call E*TRADE API
                url = f"{base_url}/v1/market/quote/{symbols_str}.json"
                response = session.get(url, params=params)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError(f"Authentication failed (401). Token may still be activating.")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_quotes, max_attempts=3, initial_delay=5.0)
            quotes = self._format_batch_quotes(data, include_earnings)

            return [TextContent(
                type="text",
                text=json.dumps(quotes, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting batch quotes: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}\n\nIf this is an authentication error, the OAuth token may still be activating. Please wait 30-60 seconds and try again."
            )]

    # Helper methods
    def _format_quote(self, data: dict, symbol: str, include_earnings: bool = False) -> dict:
        """Format single quote response."""
        if "QuoteResponse" not in data or "QuoteData" not in data["QuoteResponse"]:
            return {"error": "Invalid quote response", "symbol": symbol}

        quotes = data["QuoteResponse"]["QuoteData"]
        if not quotes:
            return {"error": "No quote data found", "symbol": symbol}

        quote = quotes[0]
        all_data = quote.get("All", {})
        product = quote.get("Product", {})

        result = {
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

        # Add earnings date if requested
        if include_earnings:
            fundamental = quote.get("Fundamental", {})
            result["earnings_date"] = fundamental.get("nextEarningsDate")
            result["eps"] = fundamental.get("eps")
            result["pe_ratio"] = fundamental.get("peRatio")

        return result

    def _format_batch_quotes(self, data: dict, include_earnings: bool = False) -> dict:
        """Format batch quotes response."""
        if "QuoteResponse" not in data or "QuoteData" not in data["QuoteResponse"]:
            return {"error": "Invalid quote response", "quotes": []}

        quotes = data["QuoteResponse"]["QuoteData"]
        formatted_quotes = []

        for quote in quotes:
            symbol = quote.get("Product", {}).get("symbol", "UNKNOWN")
            formatted_quotes.append(self._format_quote({"QuoteResponse": {"QuoteData": [quote]}}, symbol, include_earnings))

        return {
            "count": len(formatted_quotes),
            "quotes": formatted_quotes
        }
