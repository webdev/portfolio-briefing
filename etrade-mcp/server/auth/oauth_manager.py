"""OAuth session manager for E*TRADE API."""
import webbrowser
from typing import Optional, Tuple
from datetime import datetime, timezone, timedelta
from rauth import OAuth1Service
from server.auth.token_store import TokenStore
from server.config import Config
import logging

logger = logging.getLogger(__name__)

# E*TRADE token lifecycle constants
TOKEN_INACTIVITY_TIMEOUT = timedelta(hours=2)  # Token becomes inactive after 2 hours
TOKEN_RENEWAL_BUFFER = timedelta(minutes=15)   # Renew if within 15 min of inactivity


class OAuthSessionManager:
    """Manages OAuth 1.0 sessions for E*TRADE API."""

    def __init__(self, config: Config):
        self.config = config
        self.token_store = TokenStore(config.token_file_path)
        self._session: Optional[OAuth1Service] = None

        # Store request token for multi-step OAuth flow
        self._request_token: Optional[str] = None
        self._request_token_secret: Optional[str] = None

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
        Get authenticated OAuth session with automatic token lifecycle management.

        Handles:
        1. Token expiry (midnight Eastern Time)
        2. Token inactivity (2 hours)
        3. Automatic renewal when needed
        4. Last-used timestamp updates
        """
        # Try to load existing tokens
        token_data = self.token_store.load_tokens()

        if not token_data:
            logger.warning("No valid tokens found. Please run OAuth setup first.")
            raise RuntimeError(
                "OAuth tokens not found. Call setup_oauth tool first."
            )

        access_token = token_data["access_token"]
        access_token_secret = token_data["access_token_secret"]
        last_used_str = token_data["last_used_at"]
        expires_at_str = token_data["expires_at"]

        now_utc = datetime.now(timezone.utc)
        last_used = datetime.fromisoformat(last_used_str)
        expires_at = datetime.fromisoformat(expires_at_str)

        # Check if token is expired (past midnight Eastern Time)
        if now_utc >= expires_at:
            logger.warning("Token has expired (past midnight ET). Need new authorization.")
            self.token_store.clear_tokens()
            raise RuntimeError(
                "OAuth token expired. Please call setup_oauth tool to re-authenticate."
            )

        # Check if token is inactive (2+ hours since last use)
        time_since_last_use = now_utc - last_used
        if time_since_last_use >= TOKEN_INACTIVITY_TIMEOUT:
            logger.info("Token inactive for 2+ hours. Attempting renewal...")
            try:
                self._renew_access_token(access_token, access_token_secret)
                # Reload tokens after renewal
                token_data = self.token_store.load_tokens()
                access_token = token_data["access_token"]
                access_token_secret = token_data["access_token_secret"]
            except Exception as e:
                logger.error(f"Token renewal failed: {e}")
                self.token_store.clear_tokens()
                raise RuntimeError(
                    f"Token renewal failed: {e}. Please call setup_oauth tool to re-authenticate."
                )

        # Check if token is approaching inactivity (within buffer window)
        elif time_since_last_use >= (TOKEN_INACTIVITY_TIMEOUT - TOKEN_RENEWAL_BUFFER):
            logger.info(f"Token approaching inactivity ({time_since_last_use}). Renewing preemptively...")
            try:
                self._renew_access_token(access_token, access_token_secret)
                token_data = self.token_store.load_tokens()
                access_token = token_data["access_token"]
                access_token_secret = token_data["access_token_secret"]
            except Exception as e:
                logger.warning(f"Preemptive renewal failed: {e}. Continuing with current token.")

        # Update last_used timestamp
        self.token_store.update_last_used()

        # Create and cache session
        self._session = self.oauth_service.get_session((access_token, access_token_secret))
        logger.info(f"OAuth session ready (last used: {time_since_last_use} ago)")
        return self._session

    def get_authorization_url(self) -> str:
        """
        Step 1: Get authorization URL for user to visit.
        Returns the URL where user should authorize the application.
        """
        logger.info("Getting authorization URL")

        # Get request token
        self._request_token, self._request_token_secret = self.oauth_service.get_request_token(
            params={"oauth_callback": "oob", "format": "json"}
        )

        # Build authorization URL
        authorize_url = self.oauth_service.authorize_url.format(
            self.oauth_service.consumer_key, self._request_token
        )

        logger.info("Authorization URL generated")
        return authorize_url

    def complete_oauth_flow(self, verification_code: str) -> Tuple[str, str]:
        """
        Step 2: Complete OAuth flow with verification code.

        Args:
            verification_code: The code provided by E*TRADE after user authorization

        Returns:
            Tuple of (access_token, access_token_secret)
        """
        if not self._request_token or not self._request_token_secret:
            raise RuntimeError("Must call get_authorization_url() first")

        logger.info("Completing OAuth flow with verification code")

        # Exchange verification code for access token
        session = self.oauth_service.get_auth_session(
            self._request_token,
            self._request_token_secret,
            params={"oauth_verifier": verification_code}
        )

        # Extract access token and secret
        access_token = session.access_token
        access_token_secret = session.access_token_secret

        # Save tokens
        self.token_store.save_tokens(access_token, access_token_secret)

        # Update session
        self._session = session

        # Clear request tokens
        self._request_token = None
        self._request_token_secret = None

        logger.info("OAuth flow completed successfully")
        return access_token, access_token_secret

    def perform_oauth_flow(self) -> Tuple[str, str]:
        """
        Perform interactive OAuth 1.0 flow (legacy method for CLI use).
        Returns (access_token, access_token_secret)
        """
        logger.info("Starting OAuth flow")

        # Step 1: Get authorization URL
        authorize_url = self.get_authorization_url()

        print(f"\nOpening browser for E*TRADE authorization...")
        print(f"If browser doesn't open, visit: {authorize_url}\n")

        webbrowser.open(authorize_url)
        verification_code = input("Enter verification code from browser: ").strip()

        # Step 2: Complete with verification code
        return self.complete_oauth_flow(verification_code)

    def _renew_access_token(self, access_token: str, access_token_secret: str) -> None:
        """
        Renew access token using E*TRADE's renew endpoint.

        Per E*TRADE spec:
        - Tokens become inactive after 2 hours of no requests
        - Renewal extends the token validity
        - Uses GET /oauth/renew_access_token endpoint
        """
        logger.info("Renewing access token...")

        # Create session with current tokens
        session = self.oauth_service.get_session((access_token, access_token_secret))

        # Call renewal endpoint
        renew_url = f"{self.config.base_url}/oauth/renew_access_token"

        try:
            response = session.get(renew_url)

            if response.status_code == 200:
                logger.info("Token renewed successfully")
                # Token is renewed, but same token/secret are used
                # Just update the last_used timestamp
                self.token_store.update_last_used()
            else:
                error_msg = f"Token renewal failed: HTTP {response.status_code} - {response.text}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

        except Exception as e:
            logger.error(f"Token renewal request failed: {e}")
            raise
