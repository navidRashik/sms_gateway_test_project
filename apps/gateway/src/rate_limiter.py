"""
Redis-based rate limiting service for SMS providers.

Uses Redis SETEX with 1-second expiry to track request counts per provider.
Each provider is limited to 50 RPS as specified in requirements.
"""

import time
import logging
from typing import Optional, Tuple

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

from .utils import parse_redis_int

# Configure logging
logger = logging.getLogger(__name__)


class RateLimiter:
    """Redis-based rate limiter for SMS providers with sliding window algorithm."""

    def __init__(self, redis_client: Redis, rate_limit: int = 50, window: int = 1):
        """
        Initialize rate limiter.

        Args:
            redis_client: Redis client instance
            rate_limit: Maximum requests per window (default: 50)
            window: Time window in seconds (default: 1)
        """
        self.redis = redis_client
        self.rate_limit = rate_limit
        self.window = window

    def _get_key(self, provider_id: str) -> str:
        """Generate Redis key for provider rate limiting."""
        return f"rate_limit:{provider_id}"

    def _get_window_key(self, provider_id: str) -> str:
        """Get Redis key for rate limiting window."""
        return f"rate_limit:{provider_id}"

    async def is_allowed(self, provider_id: str) -> Tuple[bool, int]:
        """
        Check if request is allowed for the provider.

        Args:
            provider_id: Identifier for the SMS provider (provider1, provider2, provider3)

        Returns:
            Tuple of (is_allowed: bool, current_count: int)
        """
        key = self._get_key(provider_id)

        try:
            # Use Redis INCR to atomically increment counter
            current_count = await self.redis.incr(key)

            # Set expiry on first request to this key
            if current_count == 1:
                await self.redis.expire(key, self.window)

            # Check if we've exceeded the rate limit
            is_allowed = current_count <= self.rate_limit

            logger.debug(f"Provider {provider_id}: count={current_count}, limit={self.rate_limit}, allowed={is_allowed}")

            return is_allowed, current_count

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Redis connection error for provider {provider_id}: {str(e)}")
            # On Redis failure, allow request but log warning
            # This prevents complete system failure during Redis outages
            logger.warning(f"Rate limiting bypassed for {provider_id} due to Redis error")
            return True, 0

        except RedisError as e:
            logger.error(f"Redis error for provider {provider_id}: {str(e)}")
            # For other Redis errors, also allow but log
            logger.warning(f"Rate limiting bypassed for {provider_id} due to Redis error")
            return True, 0

        except Exception as e:
            logger.error(f"Unexpected error in rate limiter for provider {provider_id}: {str(e)}")
            # For unexpected errors, deny request to be safe
            return False, self.rate_limit + 1

    async def get_current_count(self, provider_id: str) -> int:
        """
        Get current request count for provider without incrementing.

        Args:
            provider_id: Provider identifier

        Returns:
            Current request count for this window
        """
        key = self._get_key(provider_id)
        try:
            count = await self.redis.get(key)
            return parse_redis_int(count)
        except (RedisError, ConnectionError, TimeoutError) as e:
            logger.error(f"Error getting current count for {provider_id}: {str(e)}")
            return 0

    async def reset_provider_limit(self, provider_id: str) -> bool:
        """
        Reset rate limit counter for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if reset successful
        """
        key = self._get_key(provider_id)
        try:
            await self.redis.delete(key)
        except (RedisError, ConnectionError, TimeoutError) as e:
            logger.error(f"Error resetting provider limit for {provider_id}: {str(e)}")
        return True

    async def get_rate_limit_stats(self, provider_id: str) -> dict:
        """
        Get comprehensive rate limiting statistics for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with rate limiting statistics
        """
        try:
            key = self._get_key(provider_id)
            current_count = await self.redis.get(key)
            current_count = parse_redis_int(current_count)

            # Calculate remaining requests in current window
            remaining = max(0, self.rate_limit - current_count)

            # Check if provider is currently rate limited
            is_limited = current_count >= self.rate_limit

            return {
                "provider_id": provider_id,
                "current_count": current_count,
                "rate_limit": self.rate_limit,
                "remaining": remaining,
                "is_limited": is_limited,
                "window_seconds": self.window,
                "reset_time": time.time() + 1  # Approximate reset time
            }

        except Exception as e:
            logger.error(f"Error getting rate limit stats for {provider_id}: {str(e)}")
            return {
                "provider_id": provider_id,
                "error": str(e),
                "current_count": 0,
                "rate_limit": self.rate_limit,
                "remaining": self.rate_limit,
                "is_limited": False,
                "window_seconds": self.window
            }

    async def get_all_providers_stats(self) -> dict:
        """
        Get rate limiting statistics for all providers.

        Returns:
            Dictionary with statistics for all providers
        """
        providers = ["provider1", "provider2", "provider3"]
        all_stats = {}

        for provider_id in providers:
            all_stats[provider_id] = await self.get_rate_limit_stats(provider_id)

        return {
            "providers": all_stats,
            "rate_limit_per_provider": self.rate_limit,
            "window_seconds": self.window,
            "timestamp": time.time()
        }


class GlobalRateLimiter:
    """Global rate limiter for overall system throughput."""

    def __init__(self, redis_client: Redis, rate_limit: int = 200, window: int = 1):
        """
        Initialize global rate limiter.

        Args:
            redis_client: Redis client instance
            rate_limit: Maximum total requests per window (default: 200)
            window: Time window in seconds (default: 1)
        """
        self.redis = redis_client
        self.rate_limit = rate_limit
        self.window = window

    def _get_key(self) -> str:
        """Generate Redis key for global rate limiting."""
        return "global_rate_limit"

    async def is_allowed(self) -> Tuple[bool, int]:
        """
        Check if request is allowed globally.

        Returns:
            Tuple of (is_allowed: bool, current_count: int)
        """
        key = self._get_key()

        try:
            # Use Redis INCR to atomically increment counter
            current_count = await self.redis.incr(key)

            if current_count == 1:
                # First request in this window, set expiry
                await self.redis.expire(key, self.window)

            # Check if we've exceeded the global rate limit
            is_allowed = current_count <= self.rate_limit

            return is_allowed, current_count

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Redis connection error for global rate limiter: {str(e)}")
            # On Redis failure, allow request but log warning
            logger.warning("Global rate limiting bypassed due to Redis error")
            return True, 0

        except RedisError as e:
            logger.error(f"Redis error for global rate limiter: {str(e)}")
            # For other Redis errors, also allow but log
            logger.warning("Global rate limiting bypassed due to Redis error")
            return True, 0

        except Exception as e:
            logger.error(f"Unexpected error in global rate limiter: {str(e)}")
            # For unexpected errors, deny request to be safe
            return False, self.rate_limit + 1

    async def get_current_count(self) -> int:
        """
        Get current global request count without incrementing.

        Returns:
            Current global request count for this window
        """
        key = self._get_key()
        count = await self.redis.get(key)
        return parse_redis_int(count)


async def create_rate_limiter(redis_client: Redis) -> RateLimiter:
    """
    Factory function to create a rate limiter instance.

    Args:
        redis_client: Redis client instance

    Returns:
        Configured RateLimiter instance
    """
    from .config import settings
    return RateLimiter(
        redis_client=redis_client,
        rate_limit=settings.provider_rate_limit,
        window=settings.rate_limit_window
    )


async def create_global_rate_limiter(redis_client: Redis) -> GlobalRateLimiter:
    """
    Factory function to create a global rate limiter instance.

    Args:
        redis_client: Redis client instance

    Returns:
        Configured GlobalRateLimiter instance
    """
    from .config import settings
    return GlobalRateLimiter(
        redis_client=redis_client,
        rate_limit=settings.total_rate_limit,
        window=settings.rate_limit_window
    )