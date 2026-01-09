"""
Utility functions for the arbitrage bot.
"""

import asyncio
import functools
import time
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

T = TypeVar("T")


def format_usdc(amount: float, decimals: int = 2) -> str:
    """Format amount as USDC string."""
    return f"${amount:,.{decimals}f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """Format value as percentage string."""
    return f"{value * 100:.{decimals}f}%"


def format_timestamp(
    dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """Format datetime as string."""
    if dt is None:
        dt = datetime.now()
    return dt.strftime(fmt)


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts: Maximum retry attempts
        delay: Initial delay between retries
        backoff: Backoff multiplier
        exceptions: Tuple of exceptions to catch

    Example:
        @retry_async(max_attempts=3)
        async def fetch_data():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/"
                            f"{max_attempts}): {e}. Retrying in {current_delay}s..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )

            raise last_exception

        return wrapper

    return decorator


class RateLimiter:
    """
    Async rate limiter using token bucket algorithm.

    Example:
        limiter = RateLimiter(rate=10, per=1.0)  # 10 requests per second

        async def make_request():
            async with limiter:
                await do_request()
    """

    def __init__(self, rate: int, per: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            rate: Number of tokens (requests) allowed
            per: Time period in seconds
        """
        self.rate = rate
        self.per = per
        self.tokens = rate
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now

            # Add tokens based on elapsed time
            self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / self.per))

            if self.tokens < 1:
                # Wait for token to become available
                wait_time = (1 - self.tokens) * (self.per / self.rate)
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def rate_limit(rate: int, per: float = 1.0):
    """
    Decorator for rate limiting async functions.

    Args:
        rate: Requests allowed per period
        per: Time period in seconds
    """
    limiter = RateLimiter(rate, per)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            async with limiter:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


class Timer:
    """Simple timer context manager."""

    def __init__(self, name: str = "Operation"):
        self.name = name
        self.start_time = None
        self.elapsed = 0.0

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = (time.perf_counter() - self.start_time) * 1000
        logger.debug(f"{self.name} took {self.elapsed:.2f}ms")

    async def __aenter__(self):
        self.start_time = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = (time.perf_counter() - self.start_time) * 1000
        logger.debug(f"{self.name} took {self.elapsed:.2f}ms")


def chunk_list(lst: list, chunk_size: int) -> list:
    """Split a list into chunks."""
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator
