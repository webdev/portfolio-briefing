"""Env-selected market backend re-export.

Importers that previously did `from adapters.etrade_market import ...` now
import from here; the active backend follows PORTFOLIO_BRIEFING_BROKER
(default 'etrade'). Keeps the Schwab instance off yfinance for chain prices.
"""
import os

if os.getenv("PORTFOLIO_BRIEFING_BROKER", "etrade").strip().lower() == "schwab":
    from adapters.schwab_market import (  # noqa: F401
        get_option_chain,
        get_option_expirations,
        find_put_strike_near,
        OptionChainRow,
    )
else:
    from adapters.etrade_market import (  # noqa: F401
        get_option_chain,
        get_option_expirations,
        find_put_strike_near,
        OptionChainRow,
    )
