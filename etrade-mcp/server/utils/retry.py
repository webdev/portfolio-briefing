"""Retry utilities for handling E*TRADE API calls."""
import asyncio
import logging
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar('T')


async def retry_with_backoff(
    func: Callable[..., T],
    max_attempts: int = 3,
    initial_delay: float = 2.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retry_on_auth_error: bool = True
) -> T:
    """
    Retry a function with exponential backoff.

    Useful for handling E*TRADE token activation delays (can take up to 60 seconds).

    Args:
        func: Function to retry
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        max_delay: Maximum delay between retries
        backoff_factor: Multiplier for delay on each retry
        retry_on_auth_error: Whether to retry on authentication errors

    Returns:
        Result from successful function call

    Raises:
        Last exception if all retries fail
    """
    attempt = 0
    delay = initial_delay
    last_exception = None

    while attempt < max_attempts:
        try:
            return await func() if asyncio.iscoroutinefunction(func) else func()

        except Exception as e:
            last_exception = e
            attempt += 1

            # Check if we should retry this error
            error_str = str(e).lower()
            is_auth_error = any(keyword in error_str for keyword in [
                'unauthorized', 'oauth', 'token', 'authentication', '401'
            ])

            if not retry_on_auth_error and is_auth_error:
                logger.error(f"Authentication error, not retrying: {e}")
                raise

            if attempt >= max_attempts:
                logger.error(f"Max retry attempts ({max_attempts}) reached for function {func.__name__}")
                raise

            # Log retry attempt
            logger.warning(
                f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                f"Retrying in {delay:.1f}s..."
            )

            # Wait before retry
            await asyncio.sleep(delay)

            # Increase delay for next retry (exponential backoff)
            delay = min(delay * backoff_factor, max_delay)

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic failed unexpectedly")
