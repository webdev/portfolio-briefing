"""Tests for account tools."""
import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from server.tools.account_tools import AccountTools


@pytest.fixture
def mock_session_manager():
    """Provide mock session manager."""
    manager = Mock()
    manager.config = Mock()
    manager.config.base_url = "https://apisb.etrade.com"
    manager.config.consumer_key = "test_key_123456789"
    manager.config.environment = "sandbox"
    manager.token_manager = Mock()
    return manager


@pytest.fixture
def account_tools(mock_session_manager):
    """Provide AccountTools instance."""
    return AccountTools(mock_session_manager)


class TestListAccounts:
    """Tests for list_accounts tool."""

    @pytest.mark.asyncio
    async def test_list_accounts_success(self, account_tools, mock_session_manager):
        """Test successful account listing."""
        # Mock response
        mock_response = {
            "AccountListResponse": {
                "accounts": [
                    {
                        "accountId": "123456",
                        "accountIdKey": "abc123def456",
                        "accountType": "INDIVIDUAL",
                        "institutionType": "BROKERAGE",
                        "accountStatus": "ACTIVE",
                        "accountDescription": "Brokerage Account"
                    }
                ]
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        # Call tool
        result = await account_tools.list_accounts()

        # Verify
        assert len(result) == 1
        assert result[0].type == "text"
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["accountIdKey"] == "abc123def456"
        assert data[0]["accountType"] == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_list_accounts_auth_expired(self, account_tools, mock_session_manager):
        """Test account listing with expired auth."""
        mock_session = Mock()
        mock_session.get.return_value = Mock(status_code=401)
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.list_accounts()

        assert len(result) == 1
        assert "Authentication failed" in result[0].text


class TestGetAccountBalance:
    """Tests for get_account_balance tool."""

    @pytest.mark.asyncio
    async def test_get_account_balance_success(self, account_tools, mock_session_manager):
        """Test successful balance retrieval."""
        mock_response = {
            "BalanceResponse": {
                "accountIdKey": "abc123def456",
                "accountType": "INDIVIDUAL",
                "totalAccountValue": "487231.45",
                "cashBalance": "24180.00",
                "marginBuyingPower": "96720.00",
                "settledCash": "24180.00",
                "unsettledCash": "0.00",
                "longMarketValue": "463051.45",
                "shortMarketValue": "0.00",
                "asOf": "2026-05-07T16:00:00-04:00"
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_account_balance("abc123def456")

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["accountIdKey"] == "abc123def456"
        assert data["totalAccountValue"] == 487231.45
        assert data["cashBalance"] == 24180.0

    @pytest.mark.asyncio
    async def test_get_account_balance_not_found(self, account_tools, mock_session_manager):
        """Test balance retrieval with invalid account."""
        mock_session = Mock()
        mock_session.get.return_value = Mock(status_code=404)
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_account_balance("invalid_account")

        assert len(result) == 1
        assert "Account not found" in result[0].text


class TestGetPositions:
    """Tests for get_positions tool."""

    @pytest.mark.asyncio
    async def test_get_positions_equity_and_options(self, account_tools, mock_session_manager):
        """Test getting mixed equity and option positions."""
        mock_response = {
            "PortfolioResponse": {
                "portfolio": [
                    {
                        "symbol": "AAPL",
                        "assetType": "EQUITY",
                        "quantity": "200",
                        "averageCost": "158.20",
                        "marketValue": "36240.00",
                        "lastPrice": "181.20",
                        "unrealizedPL": "4600.00",
                        "unrealizedPLPct": "14.54",
                        "dayChange": "132.00",
                        "dayChangePct": "0.37",
                        "longShort": "LONG",
                        "dateAcquired": "2024-08-12"
                    },
                    {
                        "symbol": "AAPL  260619P00170000",
                        "assetType": "OPTION",
                        "underlyingSymbol": "AAPL",
                        "optionType": "PUT",
                        "strikePrice": "170.00",
                        "expirationDate": "2026-06-19",
                        "daysToExpiration": "43",
                        "quantity": "-2",
                        "averageCost": "4.85",
                        "marketValue": "-640.00",
                        "lastPrice": "3.20",
                        "unrealizedPL": "330.00",
                        "longShort": "SHORT",
                        "dateAcquired": "2026-04-22"
                    }
                ]
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_positions("abc123def456")

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert len(data) == 2

        # Check equity position
        equity = data[0]
        assert equity["symbol"] == "AAPL"
        assert equity["assetType"] == "EQUITY"
        assert equity["quantity"] == 200.0

        # Check option position
        option = data[1]
        assert option["assetType"] == "OPTION"
        assert option["underlyingSymbol"] == "AAPL"
        assert option["optionType"] == "PUT"
        assert option["strike"] == 170.0

    @pytest.mark.asyncio
    async def test_get_positions_with_filters(self, account_tools, mock_session_manager):
        """Test getting positions with symbol and type filters."""
        mock_response = {
            "PortfolioResponse": {
                "portfolio": [
                    {
                        "symbol": "AAPL",
                        "assetType": "EQUITY",
                        "quantity": "200",
                        "averageCost": "158.20",
                        "marketValue": "36240.00",
                        "lastPrice": "181.20",
                        "unrealizedPL": "4600.00",
                        "unrealizedPLPct": "14.54",
                        "dayChange": "132.00",
                        "dayChangePct": "0.37",
                        "longShort": "LONG",
                        "dateAcquired": "2024-08-12"
                    }
                ]
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_positions(
            "abc123def456",
            symbolFilter=["AAPL"],
            assetType="EQUITY"
        )

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["symbol"] == "AAPL"


class TestGetOptionExpirations:
    """Tests for get_option_expirations tool."""

    @pytest.mark.asyncio
    async def test_get_option_expirations_dict_format(self, account_tools, mock_session_manager):
        """Test getting option expirations in dict format."""
        mock_response = {
            "OptionExpireResponse": {
                "expirationDates": {
                    "WEEKLY": ["2026-05-09", "2026-05-16"],
                    "MONTHLY": ["2026-06-19", "2026-07-17"]
                }
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_option_expirations("AAPL")

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert len(data) >= 4

        # Check structure
        expirations = [d["expiration"] for d in data]
        assert "2026-05-09" in expirations
        assert "2026-06-19" in expirations

    @pytest.mark.asyncio
    async def test_get_option_expirations_with_filter(self, account_tools, mock_session_manager):
        """Test getting option expirations with type filter."""
        mock_response = {
            "OptionExpireResponse": {
                "expirationDates": {
                    "WEEKLY": ["2026-05-09", "2026-05-16"],
                    "MONTHLY": ["2026-06-19"]
                }
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.get_option_expirations("AAPL", expirationType="WEEKLY")

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert all(d["expirationType"] == "WEEKLY" for d in data)


class TestListOrders:
    """Tests for list_orders tool."""

    @pytest.mark.asyncio
    async def test_list_orders_success(self, account_tools, mock_session_manager):
        """Test successful order listing."""
        mock_response = {
            "OrdersResponse": {
                "orders": [
                    {
                        "orderId": "987654",
                        "status": "OPEN",
                        "orderType": "LIMIT",
                        "symbol": "AAPL  260619P00170000",
                        "underlyingSymbol": "AAPL",
                        "side": "BUY_TO_CLOSE",
                        "quantity": "2",
                        "limitPrice": "1.50",
                        "stopPrice": None,
                        "timeInForce": "GTC",
                        "placedTime": "2026-05-06T09:30:15-04:00"
                    }
                ]
            }
        }

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.list_orders("abc123def456")

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["orderId"] == "987654"
        assert data[0]["status"] == "OPEN"
        assert data[0]["symbol"] == "AAPL  260619P00170000"

    @pytest.mark.asyncio
    async def test_list_orders_with_filters(self, account_tools, mock_session_manager):
        """Test listing orders with status and symbol filters."""
        mock_response = {"OrdersResponse": {"orders": []}}

        mock_session = Mock()
        mock_session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=mock_response)
        )
        mock_session_manager.get_session.return_value = mock_session

        result = await account_tools.list_orders(
            "abc123def456",
            status="EXECUTED",
            fromDate="2026-05-01",
            toDate="2026-05-07"
        )

        assert len(result) == 1

        # Verify API was called with correct parameters
        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert "status" in call_args[1]["params"]
        assert "fromDate" in call_args[1]["params"]


class TestAuthStatus:
    """Tests for auth_status tool."""

    @pytest.mark.asyncio
    async def test_auth_status_authenticated(self, account_tools, mock_session_manager):
        """Test auth_status when authenticated."""
        mock_session = Mock()
        mock_session_manager.get_session.return_value = mock_session
        mock_session_manager.token_manager.load_tokens.return_value = {
            "expires_at": "2026-05-08T00:00:00-04:00"
        }

        result = await account_tools.auth_status()

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["authenticated"] is True
        assert data["environment"] == "sandbox"
        # Fingerprint should be in the format "tes...789" (first 3 and last 3 chars with ...)
        assert "..." in data["consumerKeyFingerprint"]

    @pytest.mark.asyncio
    async def test_auth_status_not_authenticated(self, account_tools, mock_session_manager):
        """Test auth_status when not authenticated."""
        mock_session_manager.get_session.side_effect = RuntimeError("No tokens")

        result = await account_tools.auth_status()

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["authenticated"] is False
        assert "error" in data
