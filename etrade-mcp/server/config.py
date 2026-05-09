"""Configuration management for E*TRADE MCP server."""
from pydantic_settings import BaseSettings
from typing import Literal


class Config(BaseSettings):
    """MCP Server configuration."""

    # E*TRADE API credentials
    consumer_key: str
    consumer_secret: str

    # Environment selection
    environment: Literal["sandbox", "production"] = "sandbox"

    # Token storage
    token_file_path: str = ".etrade_tokens.enc"

    # Logging
    log_level: str = "INFO"
    log_file: str = "etrade_mcp.log"

    @property
    def base_url(self) -> str:
        """Get base URL based on environment."""
        if self.environment == "production":
            return "https://api.etrade.com"
        return "https://apisb.etrade.com"

    class Config:
        env_prefix = "ETRADE_"
        env_file = ".env"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls()
