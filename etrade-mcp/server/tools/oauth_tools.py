"""OAuth authentication tools for MCP server."""
from typing import Any
from mcp.types import Tool, TextContent
import logging
import webbrowser

logger = logging.getLogger(__name__)


class OAuthTools:
    """Tools for OAuth authentication."""

    def __init__(self, session_manager):
        self.session_manager = session_manager

    def get_setup_oauth_tool_def(self) -> Tool:
        """Define setup_oauth tool."""
        return Tool(
            name="setup_oauth",
            description=(
                "Perform OAuth 1.0 authentication with E*TRADE. "
                "IMPORTANT: This is a TWO-STEP process:\n"
                "Step 1: Call without verification_code - returns authorization URL for user\n"
                "Step 2: ASK USER to visit URL, authorize, and provide the 5-character code\n"
                "Step 3: Call AGAIN with user's verification_code to complete auth\n\n"
                "DO NOT call step 1 multiple times - wait for user's verification code from step 2."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "verification_code": {
                        "type": "string",
                        "description": "5-character verification code from E*TRADE (user provides this after visiting authorization URL)"
                    }
                },
                "required": []
            }
        )

    async def setup_oauth(self, verification_code: str = None) -> list[TextContent]:
        """
        Perform OAuth authentication flow.

        Args:
            verification_code: Optional verification code from E*TRADE (step 2 of flow)

        Returns:
            TextContent with authentication result or authorization URL
        """
        try:
            logger.info(f"OAuth setup called with verification_code={'present' if verification_code else 'absent'}")

            if not verification_code:
                # Step 1: Get authorization URL
                auth_url = self.session_manager.get_authorization_url()

                # Automatically open browser
                try:
                    webbrowser.open(auth_url)
                    browser_status = "✓ Browser opened automatically"
                    logger.info("Browser opened with authorization URL")
                except Exception as e:
                    browser_status = f"⚠️ Could not open browser automatically: {e}"
                    logger.warning(f"Failed to open browser: {e}")

                return [
                    TextContent(
                        type="text",
                        text=(
                            "🔐 **E*TRADE OAuth Setup - Step 1 of 2**\n\n"
                            f"{browser_status}\n\n"
                            f"Authorization URL:\n{auth_url}\n\n"
                            "📋 **NEXT STEPS:**\n"
                            "1. User should see E*TRADE login page in their browser\n"
                            "2. Login and authorize the application\n"
                            "3. E*TRADE will display a 5-character verification code\n"
                            "4. User provides that code to you\n"
                            "5. You call setup_oauth AGAIN with verification_code parameter\n\n"
                            "⚠️ DO NOT call setup_oauth again without the verification code!\n"
                            "⚠️ ASK the user for the verification code first!"
                        )
                    )
                ]
            else:
                # Step 2: Complete OAuth with verification code
                access_token, access_token_secret = self.session_manager.complete_oauth_flow(verification_code)

                logger.info("OAuth setup completed successfully via MCP tool")

                return [
                    TextContent(
                        type="text",
                        text=(
                            "✓ **OAuth authentication completed successfully!**\n\n"
                            "Access tokens have been saved and encrypted.\n\n"
                            "⏱️ **IMPORTANT:** E*TRADE tokens may take up to 60 seconds to activate.\n"
                            "If API calls fail with authentication errors, wait 30-60 seconds and retry.\n\n"
                            "You can now use market data tools like get_stock_quote."
                        )
                    )
                ]

        except Exception as e:
            logger.error(f"OAuth setup failed: {e}")

            return [
                TextContent(
                    type="text",
                    text=f"✗ **OAuth authentication failed**\n\nError: {str(e)}"
                )
            ]
