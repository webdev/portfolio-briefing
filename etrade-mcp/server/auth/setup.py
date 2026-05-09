"""OAuth setup script for E*TRADE MCP server."""
import sys
from server.config import Config
from server.auth.oauth_manager import OAuthSessionManager


def main():
    """Run OAuth setup flow."""
    print("=" * 60)
    print("E*TRADE MCP Server - OAuth Setup")
    print("=" * 60)

    try:
        # Load configuration
        config = Config.from_env()
    except Exception as e:
        print(f"\n[ERROR] Error loading configuration: {e}")
        print("\nMake sure you have created a .env file with:")
        print("  ETRADE_CONSUMER_KEY=your_key")
        print("  ETRADE_CONSUMER_SECRET=your_secret")
        print("  ETRADE_ENVIRONMENT=sandbox  # or production")
        sys.exit(1)

    print(f"\nEnvironment: {config.environment}")
    print(f"Base URL: {config.base_url}")

    # Confirm with user
    response = input("\nProceed with OAuth setup? (yes/no): ").strip().lower()
    if response not in ["yes", "y"]:
        print("Setup cancelled.")
        sys.exit(0)

    # Perform OAuth flow
    oauth_manager = OAuthSessionManager(config)

    try:
        access_token, access_token_secret = oauth_manager.perform_oauth_flow()
        print("\n" + "=" * 60)
        print("[SUCCESS] OAuth setup completed successfully!")
        print("=" * 60)
        print(f"[SUCCESS] Tokens saved to: {oauth_manager.token_store.token_file}")
        print("\nYou can now use the MCP server with Claude Desktop.")
        print("\nNext steps:")
        print("1. Configure Claude Desktop (see README.md)")
        print("2. Restart Claude Desktop")
        print("3. Start using E*TRADE tools!")

    except Exception as e:
        print("\n" + "=" * 60)
        print(f"[ERROR] OAuth setup failed: {e}")
        print("=" * 60)
        print("\nPlease check:")
        print("- Your consumer key and secret are correct")
        print("- You entered the verification code correctly")
        print("- Your E*TRADE account has API access enabled")
        sys.exit(1)


if __name__ == "__main__":
    main()
