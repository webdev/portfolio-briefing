"""Account and portfolio tools for MCP server."""
from typing import Any, Optional
from mcp.types import Tool, TextContent
import json
import logging
from datetime import datetime
from server.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class AccountTools:
    """Tools for retrieving account data, positions, orders, and option expirations."""

    def __init__(self, session_manager):
        self.session_manager = session_manager

    # Tool definitions
    def get_list_accounts_tool_def(self) -> Tool:
        """Define list_accounts tool."""
        return Tool(
            name="list_accounts",
            description="List all accounts attached to the authenticated user. Returns account IDs, types, and status.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    def get_account_balance_tool_def(self) -> Tool:
        """Define get_account_balance tool."""
        return Tool(
            name="get_account_balance",
            description="Get account balance, cash, buying power, and market values for a specific account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accountIdKey": {
                        "type": "string",
                        "description": "Account ID key (from list_accounts)"
                    }
                },
                "required": ["accountIdKey"]
            }
        )

    def get_positions_tool_def(self) -> Tool:
        """Define get_positions tool."""
        return Tool(
            name="get_positions",
            description="Get all positions (stocks and options) in an account. Returns asset type, quantity, cost basis, market value, P&L.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accountIdKey": {
                        "type": "string",
                        "description": "Account ID key (from list_accounts)"
                    },
                    "symbolFilter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter positions by symbols (optional)"
                    },
                    "assetType": {
                        "type": "string",
                        "enum": ["EQUITY", "OPTION", "ALL"],
                        "description": "Filter by asset type (default: ALL)"
                    }
                },
                "required": ["accountIdKey"]
            }
        )

    def get_option_expirations_tool_def(self) -> Tool:
        """Define get_option_expirations tool."""
        return Tool(
            name="get_option_expirations",
            description="Get available option expiration dates for a stock symbol.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock symbol (e.g., AAPL, TSLA)",
                        "pattern": "^[A-Z]{1,5}$"
                    },
                    "expirationType": {
                        "type": "string",
                        "enum": ["WEEKLY", "MONTHLY", "QUARTERLY", "ALL"],
                        "description": "Filter by expiration type (default: ALL)"
                    }
                },
                "required": ["symbol"]
            }
        )

    def get_list_orders_tool_def(self) -> Tool:
        """Define list_orders tool."""
        return Tool(
            name="list_orders",
            description="Get open or recent orders for an account. Default returns OPEN orders from the last 30 days.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accountIdKey": {
                        "type": "string",
                        "description": "Account ID key (from list_accounts)"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["OPEN", "EXECUTED", "CANCELED", "ALL"],
                        "description": "Filter by order status (default: OPEN)"
                    },
                    "fromDate": {
                        "type": "string",
                        "description": "Start date for order history (YYYY-MM-DD format)"
                    },
                    "toDate": {
                        "type": "string",
                        "description": "End date for order history (YYYY-MM-DD format)"
                    },
                    "symbolFilter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter orders by symbols (optional)"
                    }
                },
                "required": ["accountIdKey"]
            }
        )

    def get_auth_status_tool_def(self) -> Tool:
        """Define auth_status tool."""
        return Tool(
            name="auth_status",
            description="Check current authentication status without forcing re-authentication.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )

    # Tool implementations
    async def list_accounts(self) -> list[TextContent]:
        """List all accounts for the authenticated user."""
        try:
            async def _fetch_accounts():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Call E*TRADE API
                url = f"{base_url}/v1/accounts/list.json"
                response = session.get(url)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError("Authentication failed (401). Token may be expired or invalid.")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_accounts, max_attempts=3, initial_delay=5.0)
            accounts = self._format_accounts(data)

            return [TextContent(
                type="text",
                text=json.dumps(accounts, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error listing accounts: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def get_account_balance(self, accountIdKey: str) -> list[TextContent]:
        """Get account balance for a specific account."""
        try:
            async def _fetch_balance():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Call E*TRADE API
                url = f"{base_url}/v1/accounts/{accountIdKey}/balance.json"
                response = session.get(url)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError("Authentication failed (401). Token may be expired or invalid.")

                if response.status_code == 404:
                    raise RuntimeError(f"Account not found: {accountIdKey}")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_balance, max_attempts=3, initial_delay=5.0)
            balance = self._format_account_balance(data)

            return [TextContent(
                type="text",
                text=json.dumps(balance, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting account balance: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def get_positions(self, accountIdKey: str, symbolFilter: Optional[list[str]] = None, assetType: str = "ALL") -> list[TextContent]:
        """Get positions for a specific account."""
        try:
            async def _fetch_positions():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Call E*TRADE API
                url = f"{base_url}/v1/accounts/{accountIdKey}/portfolio.json"
                response = session.get(url)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError("Authentication failed (401). Token may be expired or invalid.")

                if response.status_code == 404:
                    raise RuntimeError(f"Account not found: {accountIdKey}")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_positions, max_attempts=3, initial_delay=5.0)
            positions = self._format_positions(data, symbolFilter, assetType)

            return [TextContent(
                type="text",
                text=json.dumps(positions, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def get_option_expirations(self, symbol: str, expirationType: str = "ALL") -> list[TextContent]:
        """Get available option expiration dates for a symbol."""
        try:
            async def _fetch_expirations():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Call E*TRADE API
                url = f"{base_url}/v1/market/optionexpiredate.json"
                params = {"company": symbol}
                response = session.get(url, params=params)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError("Authentication failed (401). Token may be expired or invalid.")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_expirations, max_attempts=3, initial_delay=5.0)
            expirations = self._format_option_expirations(data, expirationType)

            return [TextContent(
                type="text",
                text=json.dumps(expirations, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error getting option expirations for {symbol}: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def list_orders(self, accountIdKey: str, status: str = "OPEN", fromDate: Optional[str] = None, toDate: Optional[str] = None, symbolFilter: Optional[list[str]] = None) -> list[TextContent]:
        """Get orders for a specific account."""
        try:
            async def _fetch_orders():
                session = self.session_manager.get_session()
                base_url = self.session_manager.config.base_url

                # Build query parameters
                params = {}
                if status != "ALL":
                    params["status"] = status
                if fromDate:
                    params["fromDate"] = fromDate
                if toDate:
                    params["toDate"] = toDate

                # Call E*TRADE API
                url = f"{base_url}/v1/accounts/{accountIdKey}/orders.json"
                response = session.get(url, params=params)

                logger.debug(f"GET {url} - Status: {response.status_code}")

                if response.status_code == 401:
                    raise RuntimeError("Authentication failed (401). Token may be expired or invalid.")

                if response.status_code == 404:
                    raise RuntimeError(f"Account not found: {accountIdKey}")

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                return response.json()

            data = await retry_with_backoff(_fetch_orders, max_attempts=3, initial_delay=5.0)
            orders = self._format_orders(data, symbolFilter)

            return [TextContent(
                type="text",
                text=json.dumps(orders, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error listing orders: {e}")
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]

    async def auth_status(self) -> list[TextContent]:
        """Get current authentication status."""
        try:
            session = self.session_manager.get_session()

            # If we get here, authentication is valid
            config = self.session_manager.config
            token_manager = self.session_manager.token_manager

            # Get token expiration
            tokens = token_manager.load_tokens()
            expires_at = tokens.get('expires_at') if tokens else None

            # Create fingerprint of consumer key (just first and last 3 chars)
            consumer_key = config.consumer_key
            fingerprint = f"{consumer_key[:3]}...{consumer_key[-3:]}" if len(consumer_key) > 6 else "***"

            status = {
                "authenticated": True,
                "environment": config.environment,
                "consumerKeyFingerprint": fingerprint,
                "tokenExpiresAt": expires_at
            }

            return [TextContent(
                type="text",
                text=json.dumps(status, indent=2)
            )]

        except Exception as e:
            logger.error(f"Error checking auth status: {e}")
            return [TextContent(
                type="text",
                text=json.dumps({
                    "authenticated": False,
                    "error": str(e)
                }, indent=2)
            )]

    # Helper methods for formatting responses
    def _format_accounts(self, data: dict) -> list[dict]:
        """Format accounts response."""
        accounts = []
        if 'AccountListResponse' in data and 'accounts' in data['AccountListResponse']:
            for account in data['AccountListResponse']['accounts']:
                accounts.append({
                    "accountId": account.get('accountId'),
                    "accountIdKey": account.get('accountIdKey'),
                    "accountType": account.get('accountType'),
                    "institutionType": account.get('institutionType'),
                    "accountStatus": account.get('accountStatus'),
                    "accountDescription": account.get('accountDescription')
                })
        return accounts

    def _format_account_balance(self, data: dict) -> dict:
        """Format account balance response."""
        balance_data = {}
        if 'BalanceResponse' in data:
            bal = data['BalanceResponse']
            balance_data = {
                "accountIdKey": bal.get('accountIdKey'),
                "accountType": bal.get('accountType'),
                "totalAccountValue": float(bal.get('totalAccountValue', 0)),
                "cashBalance": float(bal.get('cashBalance', 0)),
                "marginBuyingPower": float(bal.get('marginBuyingPower', 0)),
                "settledCash": float(bal.get('settledCash', 0)),
                "unsettledCash": float(bal.get('unsettledCash', 0)),
                "longMarketValue": float(bal.get('longMarketValue', 0)),
                "shortMarketValue": float(bal.get('shortMarketValue', 0)),
                "asOf": bal.get('asOf')
            }
        return balance_data

    def _format_positions(self, data: dict, symbolFilter: Optional[list[str]] = None, assetType: str = "ALL") -> list[dict]:
        """Format positions response."""
        positions = []
        if 'PortfolioResponse' in data and 'portfolio' in data['PortfolioResponse']:
            for position in data['PortfolioResponse']['portfolio']:
                symbol = position.get('symbol', '')

                # Apply symbol filter if provided
                if symbolFilter and symbol not in symbolFilter:
                    continue

                pos_type = position.get('assetType', 'EQUITY')

                # Apply asset type filter
                if assetType != "ALL" and pos_type != assetType:
                    continue

                # Base position data
                pos_data = {
                    "symbol": symbol,
                    "assetType": pos_type,
                    "quantity": float(position.get('quantity', 0)),
                    "averageCost": float(position.get('averageCost', 0)),
                    "marketValue": float(position.get('marketValue', 0)),
                    "lastPrice": float(position.get('lastPrice', 0)),
                    "unrealizedPL": float(position.get('unrealizedPL', 0)),
                    "unrealizedPLPct": float(position.get('unrealizedPLPct', 0)),
                    "dayChange": float(position.get('dayChange', 0)),
                    "dayChangePct": float(position.get('dayChangePct', 0)),
                    "longShort": position.get('longShort'),
                    "dateAcquired": position.get('dateAcquired')
                }

                # Add option-specific fields if it's an option
                if pos_type == "OPTION":
                    pos_data.update({
                        "underlyingSymbol": position.get('underlyingSymbol'),
                        "optionType": position.get('optionType'),
                        "strike": float(position.get('strikePrice', 0)) if position.get('strikePrice') else None,
                        "expiration": position.get('expirationDate'),
                        "daysToExpiration": int(position.get('daysToExpiration', 0)) if position.get('daysToExpiration') else None
                    })

                positions.append(pos_data)

        return positions

    def _format_option_expirations(self, data: dict, expirationType: str = "ALL") -> list[dict]:
        """Format option expirations response."""
        expirations = []
        if 'OptionExpireResponse' in data and 'expirationDates' in data['OptionExpireResponse']:
            exp_data = data['OptionExpireResponse']['expirationDates']

            # Handle both formats
            if isinstance(exp_data, dict):
                # Format: {"day": [...], "weekly": [...], "monthly": [...]}
                for exp_type, dates in exp_data.items():
                    if expirationType == "ALL" or exp_type.upper().startswith(expirationType.rstrip('S')):
                        for date_str in (dates if isinstance(dates, list) else [dates]):
                            expirations.append({
                                "expiration": date_str,
                                "expirationType": exp_type.upper()
                            })
            elif isinstance(exp_data, list):
                # Format: [{"date": "...", "type": "..."}, ...]
                for item in exp_data:
                    exp_date = item.get('date') or item.get('expiration')
                    exp_type = item.get('type') or item.get('expirationType', 'MONTHLY')

                    if expirationType == "ALL" or exp_type.upper() == expirationType:
                        expirations.append({
                            "expiration": exp_date,
                            "expirationType": exp_type.upper()
                        })

        return expirations

    def _format_orders(self, data: dict, symbolFilter: Optional[list[str]] = None) -> list[dict]:
        """Format orders response."""
        orders = []
        if 'OrdersResponse' in data and 'orders' in data['OrdersResponse']:
            for order in data['OrdersResponse']['orders']:
                symbol = order.get('symbol', '')

                # Apply symbol filter if provided
                if symbolFilter and symbol not in symbolFilter:
                    continue

                order_data = {
                    "orderId": order.get('orderId'),
                    "status": order.get('status'),
                    "orderType": order.get('orderType'),
                    "symbol": symbol,
                    "underlyingSymbol": order.get('underlyingSymbol'),
                    "side": order.get('side'),
                    "quantity": float(order.get('quantity', 0)),
                    "limitPrice": float(order.get('limitPrice')) if order.get('limitPrice') else None,
                    "stopPrice": float(order.get('stopPrice')) if order.get('stopPrice') else None,
                    "timeInForce": order.get('timeInForce'),
                    "placedAt": order.get('placedTime')
                }

                orders.append(order_data)

        return orders
