"""
Fetch YTD options P&L from E*TRADE via pyetrade.

Aggregates:
- Premium collected (sum of SELL_TO_OPEN credits)
- Premium paid (sum of BUY_TO_OPEN debits)
- Realized losses / gains from closed option trades
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def _load_etrade_credentials() -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Load E*TRADE credentials via the in-repo etrade_auth module.
    Returns (consumer_key, consumer_secret, oauth_token, oauth_secret) or (None, None, None, None).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from etrade_auth import _consumer_credentials, load_tokens  # type: ignore
    except Exception:
        return None, None, None, None

    try:
        ck, cs = _consumer_credentials()
    except Exception:
        return None, None, None, None

    tokens = load_tokens() or {}
    return (
        ck,
        cs,
        tokens.get("oauth_token"),
        # Tolerate either canonical key — old wheelhouz files used "oauth_token_secret"
        tokens.get("oauth_secret") or tokens.get("oauth_token_secret"),
    )


def fetch_ytd_options_pnl(
    consumer_key: str,
    consumer_secret: str,
    oauth_token: str,
    oauth_secret: str,
    sandbox: bool = True,
    account_id_keys: list = None,
) -> dict:
    """
    Fetch YTD options P&L from E*TRADE using pyetrade Accounts.list_transactions.

    Args:
        consumer_key: E*TRADE OAuth consumer key
        consumer_secret: E*TRADE OAuth consumer secret
        oauth_token: E*TRADE OAuth token
        oauth_secret: E*TRADE OAuth token secret
        sandbox: use sandbox (True) or production (False)
        account_id_keys: list of account ID keys to fetch from (or None = all)

    Returns:
        dict with keys:
        - "premium_collected": sum of credits from SELL_TO_OPEN
        - "premium_paid": sum of debits from BUY_TO_OPEN
        - "realized_losses": sum of negative P&L from closures
        - "realized_gains": sum of positive P&L from closures
        - "net_realized": gains - losses
        - "transactions_count": total transaction count processed
        - "error": error message if fetch failed
    """
    try:
        import pyetrade
    except ImportError:
        return {
            "error": "pyetrade not installed",
            "premium_collected": 0,
            "premium_paid": 0,
            "realized_losses": 0,
            "realized_gains": 0,
            "net_realized": 0,
            "transactions_count": 0,
        }

    result = {
        "premium_collected": 0.0,
        "premium_paid": 0.0,
        "realized_losses": 0.0,
        "realized_gains": 0.0,
        "net_realized": 0.0,
        "transactions_count": 0,
        "error": None,
    }

    try:
        # Create session
        session = pyetrade.ETradeSession(
            client_key=consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_secret,
            sandbox=sandbox,
        )

        # Create accounts client
        accounts_client = pyetrade.ETradeAccounts(session)

        # Fetch accounts
        try:
            accounts_resp = accounts_client.get_account_list()
            accounts = accounts_resp.get("Accounts", {}).get("Account", [])
            if not isinstance(accounts, list):
                accounts = [accounts]
        except Exception as e:
            return {
                **result,
                "error": f"Failed to fetch accounts: {e}",
            }

        # Filter accounts if specific keys provided
        if account_id_keys:
            accounts = [a for a in accounts if a.get("accountIdKey") in account_id_keys]

        if not accounts:
            return {
                **result,
                "error": "No accounts found",
            }

        # Fetch YTD transactions for each account
        ytd_start = datetime(datetime.now().year, 1, 1).strftime("%Y-%m-%d")
        ytd_end = datetime.now().strftime("%Y-%m-%d")

        for account in accounts:
            account_id_key = account.get("accountIdKey")
            if not account_id_key:
                continue

            try:
                # Fetch transactions with pagination
                count = 0
                page_size = 200
                marker = None

                while count < 5000:  # Safety limit
                    try:
                        tx_resp = accounts_client.get_account_transactions(
                            account_id_key,
                            from_date=ytd_start,
                            to_date=ytd_end,
                            marker=marker,
                            count=page_size,
                        )

                        transactions = tx_resp.get("Transaction", [])
                        if not isinstance(transactions, list):
                            transactions = [transactions] if transactions else []

                        # Process transactions
                        for tx in transactions:
                            result["transactions_count"] += 1

                            tx_type = tx.get("transactionType", "").upper()
                            tx_sub_type = tx.get("transactionSubType", "").upper()
                            amount = float(tx.get("amount", 0))

                            # Options transactions
                            if "OPTION" in tx_type:
                                if "SELL_TO_OPEN" in tx_sub_type and amount > 0:
                                    # Credit for selling to open
                                    result["premium_collected"] += amount
                                elif "BUY_TO_OPEN" in tx_sub_type and amount > 0:
                                    # Debit for buying to open
                                    result["premium_paid"] += amount
                                elif "BUY_TO_CLOSE" in tx_sub_type or "SELL_TO_CLOSE" in tx_sub_type:
                                    # Closure with P&L (amount is net realized P&L)
                                    if amount > 0:
                                        result["realized_gains"] += amount
                                    else:
                                        result["realized_losses"] += abs(amount)

                        # Check for more pages
                        marker = tx_resp.get("marker")
                        if not marker:
                            break
                        count += len(transactions)

                    except Exception as e:
                        print(f"    [warn] error fetching transactions for {account_id_key}: {e}", file=sys.stderr)
                        break

            except Exception as e:
                print(f"    [warn] error processing account {account_id_key}: {e}", file=sys.stderr)

        # Compute net realized
        result["net_realized"] = result["realized_gains"] - result["realized_losses"]

        # Round to cents
        result["premium_collected"] = round(result["premium_collected"], 2)
        result["premium_paid"] = round(result["premium_paid"], 2)
        result["realized_losses"] = round(result["realized_losses"], 2)
        result["realized_gains"] = round(result["realized_gains"], 2)
        result["net_realized"] = round(result["net_realized"], 2)

    except Exception as e:
        return {
            **result,
            "error": str(e),
        }

    return result


def fetch_ytd_options_pnl_auto() -> dict:
    """
    Fetch YTD P&L using auto-loaded credentials from the in-repo etrade_auth module.
    Returns empty result on credential or API error.
    """
    ck, cs, ot, os = _load_etrade_credentials()

    if not (ck and cs and ot and os):
        return {
            "premium_collected": 0.0,
            "premium_paid": 0.0,
            "realized_losses": 0.0,
            "realized_gains": 0.0,
            "net_realized": 0.0,
            "transactions_count": 0,
            "error": "Missing E*TRADE credentials",
        }

    return fetch_ytd_options_pnl(
        consumer_key=ck,
        consumer_secret=cs,
        oauth_token=ot,
        oauth_secret=os,
        sandbox=False,  # Production by default
    )
