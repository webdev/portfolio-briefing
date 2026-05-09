"""Secure token storage for OAuth credentials."""
import json
import os
from pathlib import Path
from typing import Optional, Tuple, Dict
from datetime import datetime, timezone, timedelta
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

    def _get_eastern_midnight_utc(self) -> datetime:
        """
        Calculate next midnight Eastern Time in UTC.

        E*TRADE tokens expire at midnight Eastern Time.
        Eastern Time is UTC-5 (EST) or UTC-4 (EDT) depending on daylight saving time.
        """
        now_utc = datetime.now(timezone.utc)

        # Use a simple heuristic for EST/EDT:
        # EDT (UTC-4): Second Sunday in March to first Sunday in November
        # EST (UTC-5): Rest of the year
        # For simplicity, we'll use a conservative estimate
        # This could be improved with pytz or zoneinfo in Python 3.9+

        year = now_utc.year
        month = now_utc.month

        # Approximate DST (March-November = EDT, otherwise EST)
        if 3 <= month <= 10:
            eastern_offset = timedelta(hours=-4)  # EDT
        else:
            eastern_offset = timedelta(hours=-5)  # EST

        # Convert current UTC to Eastern
        now_eastern = now_utc + eastern_offset

        # Next midnight Eastern (00:00:00 the next day)
        next_midnight_eastern = (now_eastern + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Convert back to UTC for storage
        expires_at_utc = next_midnight_eastern - eastern_offset

        return expires_at_utc

    def save_tokens(self, access_token: str, access_token_secret: str) -> None:
        """
        Save OAuth tokens to encrypted file with timestamps.

        Tracks:
        - created_at: When token was first created
        - last_used_at: Last time token was used (for 2-hour inactivity check)
        - expires_at: Midnight Eastern Time on the day of creation
        """
        now_utc = datetime.now(timezone.utc)
        expires_at_utc = self._get_eastern_midnight_utc()

        data = {
            "access_token": access_token,
            "access_token_secret": access_token_secret,
            "created_at": now_utc.isoformat(),
            "last_used_at": now_utc.isoformat(),
            "expires_at": expires_at_utc.isoformat()
        }

        # Encrypt and save
        encrypted_data = self.cipher.encrypt(json.dumps(data).encode())
        self.token_file.write_bytes(encrypted_data)

        logger.info(f"Tokens saved to {self.token_file} (expires at {expires_at_utc.isoformat()})")

    def load_tokens(self) -> Optional[Dict[str, str]]:
        """
        Load OAuth tokens from encrypted file with metadata.

        Returns:
            Dict with keys: access_token, access_token_secret, created_at, last_used_at, expires_at
            Or None if tokens don't exist or can't be loaded
        """
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
                # For backward compatibility with old token files without timestamps
                if "created_at" not in data:
                    logger.warning("Old token format detected, timestamps unavailable")
                    data["created_at"] = datetime.now(timezone.utc).isoformat()
                    data["last_used_at"] = datetime.now(timezone.utc).isoformat()
                    data["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()

                return data

            return None

        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
            return None

    def update_last_used(self) -> None:
        """Update the last_used_at timestamp to prevent inactivity timeout."""
        data = self.load_tokens()
        if not data:
            return

        data["last_used_at"] = datetime.now(timezone.utc).isoformat()

        # Encrypt and save updated data
        encrypted_data = self.cipher.encrypt(json.dumps(data).encode())
        self.token_file.write_bytes(encrypted_data)

        logger.debug(f"Token last_used_at updated to {data['last_used_at']}")

    def clear_tokens(self) -> None:
        """Delete stored tokens."""
        if self.token_file.exists():
            self.token_file.unlink()
            logger.info("Tokens cleared")
