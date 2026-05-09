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
from server.tools.oauth_tools import OAuthTools
from server.tools.account_tools import AccountTools


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
    try:
        config = Config.from_env()
    except Exception as e:
        print(f"[ERROR] Failed to load configuration: {e}", file=sys.stderr)
        print("\nMake sure you have created a .env file with:", file=sys.stderr)
        print("  ETRADE_CONSUMER_KEY=your_key", file=sys.stderr)
        print("  ETRADE_CONSUMER_SECRET=your_secret", file=sys.stderr)
        print("  ETRADE_ENVIRONMENT=sandbox", file=sys.stderr)
        sys.exit(1)

    setup_logging(config)

    logger = logging.getLogger(__name__)
    logger.info("Starting E*TRADE MCP Server")
    logger.info(f"Environment: {config.environment}")

    # Initialize OAuth session manager (don't authenticate yet - defer to tool calls)
    try:
        session_manager = OAuthSessionManager(config)
        logger.info("OAuth session manager initialized (authentication deferred)")
    except Exception as e:
        logger.error(f"Failed to initialize OAuth manager: {e}")
        sys.exit(1)

    # Initialize tool handlers
    stock_tools = StockQuoteTools(session_manager)
    options_tools = OptionsQuoteTools(session_manager)
    oauth_tools = OAuthTools(session_manager)
    account_tools = AccountTools(session_manager)

    # Create MCP server
    server = Server("etrade-mcp")

    # Register list_tools handler
    @server.list_tools()
    async def list_tools():
        """List available tools."""
        logger.info("list_tools called")
        return [
            oauth_tools.get_setup_oauth_tool_def(),
            stock_tools.get_quote_tool_def(),
            stock_tools.get_batch_quotes_tool_def(),
            options_tools.get_option_chains_tool_def(),
            options_tools.get_option_quote_tool_def(),
            account_tools.get_list_accounts_tool_def(),
            account_tools.get_account_balance_tool_def(),
            account_tools.get_positions_tool_def(),
            account_tools.get_option_expirations_tool_def(),
            account_tools.get_list_orders_tool_def(),
            account_tools.get_auth_status_tool_def(),
        ]

    # Register call_tool handler
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        """Handle tool calls."""
        logger.info(f"Tool called: {name} with args: {arguments}")

        try:
            # Handle OAuth setup tool
            if name == "setup_oauth":
                verification_code = arguments.get("verification_code")
                return await oauth_tools.setup_oauth(verification_code)

            # Check if OAuth is needed for other tools
            try:
                session_manager.get_session()
            except RuntimeError as e:
                # OAuth not set up yet - return helpful message to LLM user
                error_msg = (
                    "E*TRADE OAuth authentication required. "
                    "Please call the 'setup_oauth' tool first. "
                    "This will open a browser for authorization."
                )
                logger.warning(f"OAuth required for tool {name}")
                raise RuntimeError(error_msg)

            if name == "get_stock_quote":
                return await stock_tools.get_quote(
                    symbol=arguments["symbol"],
                    include_earnings=arguments.get("include_earnings", False)
                )

            elif name == "get_batch_quotes":
                return await stock_tools.get_batch_quotes(
                    symbols=arguments["symbols"],
                    include_earnings=arguments.get("include_earnings", False)
                )

            elif name == "get_option_chains":
                return await options_tools.get_option_chains(
                    symbol=arguments["symbol"],
                    expiry_year=arguments.get("expiry_year"),
                    expiry_month=arguments.get("expiry_month"),
                    expiry_day=arguments.get("expiry_day"),
                    chain_type=arguments.get("chain_type"),
                    strike_price_near=arguments.get("strike_price_near"),
                    no_of_strikes=arguments.get("no_of_strikes"),
                    include_weekly=arguments.get("include_weekly"),
                    skip_adjusted=arguments.get("skip_adjusted"),
                    option_category=arguments.get("option_category"),
                    price_type=arguments.get("price_type")
                )

            elif name == "get_option_quote":
                return await options_tools.get_option_quote(arguments["option_symbols"])

            elif name == "list_accounts":
                return await account_tools.list_accounts()

            elif name == "get_account_balance":
                return await account_tools.get_account_balance(arguments["accountIdKey"])

            elif name == "get_positions":
                return await account_tools.get_positions(
                    accountIdKey=arguments["accountIdKey"],
                    symbolFilter=arguments.get("symbolFilter"),
                    assetType=arguments.get("assetType", "ALL")
                )

            elif name == "get_option_expirations":
                return await account_tools.get_option_expirations(
                    symbol=arguments["symbol"],
                    expirationType=arguments.get("expirationType", "ALL")
                )

            elif name == "list_orders":
                return await account_tools.list_orders(
                    accountIdKey=arguments["accountIdKey"],
                    status=arguments.get("status", "OPEN"),
                    fromDate=arguments.get("fromDate"),
                    toDate=arguments.get("toDate"),
                    symbolFilter=arguments.get("symbolFilter")
                )

            elif name == "auth_status":
                return await account_tools.auth_status()

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
