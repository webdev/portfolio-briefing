# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an E*TRADE API Python client application that provides examples for interacting with E*TRADE's trading platform APIs. The application is a CLI-based tool for accessing market quotes, account information, portfolio holdings, and order management.

## Setup and Configuration

### Environment Setup
```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment (Windows)
venv\Scripts\activate.bat

# Activate virtual environment (Unix/MacOS)
source venv/bin/activate

# Install dependencies
pip install -r client/requirements.txt
```

### Configuration
- Edit `client/src/config.ini` with E*TRADE consumer key and consumer secret
- The config file includes both sandbox and production base URLs
- Never commit actual credentials to the repository

### Running the Application
```bash
cd client/src
python3 etrade_python_client.py
```

## Architecture

### Module Structure

The application follows a modular architecture with separate modules for different API domains:

- **etrade_python_client.py** - Main entry point, handles OAuth 1.0 authentication and main menu
- **accounts/** - Account list, balance, and portfolio management
- **market/** - Market quotes and pricing data
- **order/** - Order preview, placement, cancellation, and viewing

### Authentication Flow

1. OAuth 1.0 request token obtained from E*TRADE
2. User authorizes via browser (authorization URL opens automatically)
3. User enters verification code from browser
4. Authenticated session created for subsequent API calls

### API Communication Pattern

All modules follow a consistent pattern:
1. Build API endpoint URL using base_url + API path
2. Add required headers (consumerKey) and parameters
3. Make authenticated request using session.get/post/put
4. Parse JSON response and handle errors
5. Display formatted results to user

### Session Management

- OAuth session is created once during authentication
- Session object is passed to module constructors (Accounts, Market, Order)
- All API calls use `header_auth=True` to include OAuth headers

### Error Handling

- Response status codes checked (200 for success, 204 for no content)
- JSON error messages extracted from `Error.message` field
- Fallback generic error messages when parsing fails
- All requests/responses logged to `python_client.log`

### Order Workflow

1. **View Orders** - Displays orders by status (open, executed, cancelled, etc.)
2. **Preview Order** - User selects order parameters or reuses previous order
3. **Cancel Order** - Select from list of open orders to cancel

Order data structure includes: symbol, quantity, price type, order term, order action, security type

## Key Implementation Details

### Config Access
Configuration is loaded via `configparser` and accessed as:
```python
config["DEFAULT"]["CONSUMER_KEY"]
config["DEFAULT"]["SANDBOX_BASE_URL"]
```

### Logging
All modules use rotating file handler (5MB max, 3 backups) writing to `python_client.log`:
```python
logger.debug("Request Header: %s", response.request.headers)
```

### Base URL Handling
Base URL is selected at authentication (sandbox vs production) and passed to all modules. It's prepended to all API endpoint paths.

### Menu Navigation
Application uses numeric menus with input validation. User selections drive module instantiation and method calls.

## Development Notes

### Dependencies
- `rauth==0.7.3` - OAuth 1.0 authentication

### Python Version
- Requires Python 3
- Uses `from __future__ import print_function` for compatibility

### API Response Structure
E*TRADE responses follow pattern: `{ResponseType: {data}}`, e.g.:
- `QuoteResponse.QuoteData[]`
- `AccountListResponse.Accounts.Account[]`
- `PortfolioResponse.AccountPortfolio[]`
- `OrdersResponse.Order[]`
