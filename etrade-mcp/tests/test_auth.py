"""Tests for OAuth manager."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from server.auth.oauth_manager import OAuthSessionManager
from server.config import Config


@pytest.fixture
def mock_config(tmp_path):
    """Provide mock configuration."""
    config = Mock(spec=Config)
    config.consumer_key = "test_consumer_key"
    config.consumer_secret = "test_consumer_secret"
    config.token_file_path = str(tmp_path / "test_tokens.enc")
    config.base_url = "https://apisb.etrade.com"
    config.environment = "sandbox"
    return config


@pytest.fixture
def oauth_manager(mock_config, monkeypatch):
    """Provide OAuthSessionManager instance."""
    # Set encryption key for token store
    from cryptography.fernet import Fernet
    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv("ETRADE_TOKEN_KEY", test_key)

    return OAuthSessionManager(mock_config)


def test_oauth_manager_initialization(oauth_manager, mock_config):
    """Test OAuthSessionManager initializes correctly."""
    assert oauth_manager.config == mock_config
    assert oauth_manager.token_store is not None
    assert oauth_manager.oauth_service is not None
    assert oauth_manager.oauth_service.name == "etrade"


def test_oauth_manager_service_configuration(oauth_manager):
    """Test OAuth service is configured with correct endpoints."""
    service = oauth_manager.oauth_service

    assert service.request_token_url == "https://api.etrade.com/oauth/request_token"
    assert service.access_token_url == "https://api.etrade.com/oauth/access_token"
    assert "authorize" in service.authorize_url


def test_get_session_with_saved_tokens(oauth_manager):
    """Test get_session returns session when tokens exist."""
    # Save test tokens
    oauth_manager.token_store.save_tokens("test_token", "test_secret")

    # Mock get_session method
    with patch.object(oauth_manager.oauth_service, 'get_session') as mock_get_session:
        mock_session = Mock()
        mock_get_session.return_value = mock_session

        # Get session
        session = oauth_manager.get_session()

        # Verify get_session was called with correct tokens
        mock_get_session.assert_called_once_with(("test_token", "test_secret"))
        assert session == mock_session


def test_get_session_without_tokens_raises_error(oauth_manager):
    """Test get_session raises error when no tokens exist."""
    # Ensure no tokens exist
    assert oauth_manager.token_store.load_tokens() is None

    # Should raise RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        oauth_manager.get_session()

    assert "OAuth tokens not found" in str(exc_info.value)
    assert "setup_oauth" in str(exc_info.value)


@patch('webbrowser.open')
@patch('builtins.input')
def test_perform_oauth_flow_success(mock_input, mock_browser, oauth_manager):
    """Test successful OAuth flow."""
    # Mock user input
    mock_input.return_value = "test_verification_code"

    # Mock OAuth service methods
    request_token = "test_request_token"
    request_token_secret = "test_request_secret"
    access_token = "test_access_token"
    access_token_secret = "test_access_secret"

    with patch.object(oauth_manager.oauth_service, 'get_request_token') as mock_get_req_token, \
         patch.object(oauth_manager.oauth_service, 'get_auth_session') as mock_get_auth_session:

        # Mock request token
        mock_get_req_token.return_value = (request_token, request_token_secret)

        # Mock auth session
        mock_session = Mock()
        mock_session.access_token = access_token
        mock_session.access_token_secret = access_token_secret
        mock_get_auth_session.return_value = mock_session

        # Perform OAuth flow
        result_token, result_secret = oauth_manager.perform_oauth_flow()

        # Verify results
        assert result_token == access_token
        assert result_secret == access_token_secret

        # Verify tokens were saved
        saved_tokens = oauth_manager.token_store.load_tokens()
        assert saved_tokens is not None
        assert saved_tokens["access_token"] == access_token
        assert saved_tokens["access_token_secret"] == access_token_secret

        # Verify browser was opened
        mock_browser.assert_called_once()

        # Verify get_request_token was called
        mock_get_req_token.assert_called_once_with(
            params={"oauth_callback": "oob", "format": "json"}
        )

        # Verify get_auth_session was called
        mock_get_auth_session.assert_called_once_with(
            request_token,
            request_token_secret,
            params={"oauth_verifier": "test_verification_code"}
        )


@patch('webbrowser.open')
@patch('builtins.input')
def test_perform_oauth_flow_saves_tokens(mock_input, mock_browser, oauth_manager):
    """Test OAuth flow saves tokens to token store."""
    mock_input.return_value = "verification_code"

    with patch.object(oauth_manager.oauth_service, 'get_request_token') as mock_req, \
         patch.object(oauth_manager.oauth_service, 'get_auth_session') as mock_auth:

        mock_req.return_value = ("req_token", "req_secret")

        mock_session = Mock()
        mock_session.access_token = "saved_access_token"
        mock_session.access_token_secret = "saved_access_secret"
        mock_auth.return_value = mock_session

        # Perform flow
        oauth_manager.perform_oauth_flow()

        # Verify tokens can be loaded
        loaded_tokens = oauth_manager.token_store.load_tokens()
        assert loaded_tokens is not None
        assert loaded_tokens["access_token"] == "saved_access_token"
        assert loaded_tokens["access_token_secret"] == "saved_access_secret"


def test_oauth_manager_uses_config_credentials(mock_config, monkeypatch):
    """Test OAuth manager uses credentials from config."""
    from cryptography.fernet import Fernet
    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv("ETRADE_TOKEN_KEY", test_key)

    manager = OAuthSessionManager(mock_config)

    assert manager.oauth_service.consumer_key == "test_consumer_key"
    assert manager.oauth_service.consumer_secret == "test_consumer_secret"
