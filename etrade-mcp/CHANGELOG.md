# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

#### Account & Portfolio Management Tools
- `list_accounts()` - List all accounts attached to authenticated user
- `get_account_balance()` - Get account balance, cash, buying power, and market values
- `get_positions()` - Get all positions (stocks and options) in an account with filtering
- `get_option_expirations()` - Get available option expiration dates for a symbol
- `list_orders()` - Get open and historical orders with filtering by status and date range
- `auth_status()` - Diagnostic tool to check current authentication status

#### Testing
- Comprehensive test suite for account tools (`tests/test_account_tools.py`) with 12 tests covering:
  - Account listing and balance retrieval
  - Position retrieval with equity and option positions
  - Option expiration date handling
  - Order listing with filtering
  - Authentication status checking
  - Error handling (401 auth failures, 404 not found)

#### Documentation
- Updated README with new account management tools section
- Added tool descriptions with input/output specifications
- Updated project structure documentation
- Updated development status to reflect account management feature completion

### Changed
- Updated `server/main.py` to register and handle new account tools
- Enhanced feature list in README to highlight account management capabilities

### Technical Details
- All account tools follow existing tool registration pattern in ohenak's framework
- Implemented retry logic with exponential backoff for API reliability
- Consistent error handling with structured error responses
- Support for filtering and optional parameters in all retrieval tools
- Proper handling of E*TRADE API response formats (both dict and list based)

## [0.1.0] - 2024-12-04

### Initial Release
- OAuth 1.0 authentication with encrypted token storage
- Stock quotes (single and batch, up to 25 symbols)
- Options chains with full Greeks and filtering
- Options quotes by OSI format symbols
- Automatic retry logic for rate limits
- MCP integration with Claude Desktop
- Comprehensive unit tests for auth components
