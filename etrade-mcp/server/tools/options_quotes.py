"""Options data tools for MCP server."""
from typing import Any
from mcp.types import Tool, TextContent
import json
import logging
from server.utils.retry import retry_with_backoff

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
            description="Get options chain data for a stock symbol, including available expiration dates and strike prices. Specify expiry date components to reduce data volume.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Underlying stock symbol (e.g., AAPL, TSLA)",
                        "pattern": "^[A-Z]{1,5}$"
                    },
                    "expiry_year": {
                        "type": "integer",
                        "description": "Expiry year (e.g., 2025)"
                    },
                    "expiry_month": {
                        "type": "integer",
                        "description": "Expiry month (1-12)",
                        "minimum": 1,
                        "maximum": 12
                    },
                    "expiry_day": {
                        "type": "integer",
                        "description": "Expiry day (1-31). Recommended to reduce data volume.",
                        "minimum": 1,
                        "maximum": 31
                    },
                    "chain_type": {
                        "type": "string",
                        "enum": ["CALL", "PUT", "CALLPUT"],
                        "description": "Type of option chain (default: CALLPUT)"
                    },
                    "strike_price_near": {
                        "type": "number",
                        "description": "Fetch strikes near this price"
                    },
                    "no_of_strikes": {
                        "type": "integer",
                        "description": "Number of strikes to fetch"
                    },
                    "include_weekly": {
                        "type": "boolean",
                        "description": "Include weekly options (default: false)"
                    },
                    "skip_adjusted": {
                        "type": "boolean",
                        "description": "Skip adjusted options (default: true)"
                    },
                    "option_category": {
                        "type": "string",
                        "enum": ["STANDARD", "ALL", "MINI"],
                        "description": "Option category (default: STANDARD)"
                    },
                    "price_type": {
                        "type": "string",
                        "enum": ["ATNM", "ALL"],
                        "description": "Price type - ATNM (at-the-money) or ALL (default: ATNM)"
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
        expiry_year: int | None = None,
        expiry_month: int | None = None,
        expiry_day: int | None = None,
        chain_type: str | None = None,
        strike_price_near: float | None = None,
        no_of_strikes: int | None = None,
        include_weekly: bool | None = None,
        skip_adjusted: bool | None = None,
        option_category: str | None = None,
        price_type: str | None = None
    ) -> list[TextContent]:
        """Get options chain for a symbol."""
        try:
            # Use retry logic to handle token activation delays
            async def _fetch_chains():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Build query parameters according to E*TRADE API spec
                params = {"symbol": symbol}

                # Expiry date components (year, month, day are separate integer parameters)
                if expiry_year is not None:
                    params["expiryYear"] = expiry_year
                if expiry_month is not None:
                    params["expiryMonth"] = expiry_month
                if expiry_day is not None:
                    params["expiryDay"] = expiry_day

                # Chain type (CALL, PUT, or CALLPUT)
                if chain_type:
                    params["chainType"] = chain_type

                # Strike price filters
                if strike_price_near is not None:
                    params["strikePriceNear"] = strike_price_near
                if no_of_strikes is not None:
                    params["noOfStrikes"] = no_of_strikes

                # Optional filters
                if include_weekly is not None:
                    params["includeWeekly"] = "true" if include_weekly else "false"
                if skip_adjusted is not None:
                    params["skipAdjusted"] = "true" if skip_adjusted else "false"
                if option_category:
                    params["optionCategory"] = option_category
                if price_type:
                    params["priceType"] = price_type

                logger.info(f"Fetching option chains with params: {params}")

                # Call E*TRADE API - must use .json extension to get JSON response
                url = f"{base_url}/v1/market/optionchains.json"
                response = session.get(url, params=params)

                logger.info(f"GET {url} - Status: {response.status_code}")
                logger.debug(f"Response headers: {response.headers}")
                logger.debug(f"Response text (first 500 chars): {response.text[:500]}")

                if response.status_code == 401:
                    raise RuntimeError(f"Authentication failed (401). Token may still be activating.")

                if response.status_code != 200:
                    logger.error(f"Non-200 status code. Full response text: {response.text}")
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                # Log the raw response for debugging
                if not response.text:
                    raise RuntimeError("Empty response received from API")

                try:
                    return response.json()
                except Exception as e:
                    logger.error(f"Failed to parse JSON. Response text: {response.text}")
                    raise

            data = await retry_with_backoff(_fetch_chains, max_attempts=3, initial_delay=5.0)
            chains_info = self._format_option_chains(data, symbol)

            return [TextContent(
                type="text",
                text=json.dumps(chains_info, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting option chains for {symbol}: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}\n\nIf this is an authentication error, the OAuth token may still be activating. Please wait 30-60 seconds and try again."
            )]

    async def get_option_quote(self, option_symbols: list[str]) -> list[TextContent]:
        """Get quotes for specific option contracts."""
        try:
            # Use retry logic to handle token activation delays
            async def _fetch_quotes():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Join symbols with comma
                symbols_str = ",".join(option_symbols)

                # Call E*TRADE API (same endpoint as stocks)
                url = f"{base_url}/v1/market/quote/{symbols_str}.json"
                response = session.get(url)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError(f"Authentication failed (401). Token may still be activating.")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_quotes, max_attempts=3, initial_delay=5.0)
            quotes = self._format_option_quotes(data)

            return [TextContent(
                type="text",
                text=json.dumps(quotes, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting option quotes: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}\n\nIf this is an authentication error, the OAuth token may still be activating. Please wait 30-60 seconds and try again."
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
            "option_pair_count": len(response_data.get("OptionPair", [])),
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
