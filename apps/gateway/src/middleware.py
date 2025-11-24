"""
Rate limiting middleware for FastAPI.

This middleware provides comprehensive rate limiting functionality
that can be applied to all endpoints or specific routes.
"""

import time
import logging
from typing import Callable, Dict, Any, Optional

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .rate_limiter import RateLimiter, GlobalRateLimiter, create_rate_limiter, create_global_rate_limiter
from .config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware for FastAPI applications.

    This middleware checks rate limits before processing requests
    and returns 429 status when limits are exceeded.
    """

    def __init__(
        self,
        app,
        rate_limiter: Optional[RateLimiter] = None,
        global_rate_limiter: Optional[GlobalRateLimiter] = None,
        exclude_paths: Optional[list] = None,
        include_provider_check: bool = True
    ):
        """
        Initialize rate limiting middleware.

        Args:
            app: FastAPI application instance
            rate_limiter: RateLimiter instance (created if not provided)
            global_rate_limiter: GlobalRateLimiter instance (created if not provided)
            exclude_paths: List of paths to exclude from rate limiting
            include_provider_check: Whether to check per-provider limits
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]
        self.include_provider_check = include_provider_check
        self.rate_limiter = rate_limiter
        self.global_rate_limiter = global_rate_limiter

    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting."""
        # Skip rate limiting for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Initialize rate limiters if not provided
        if not self.rate_limiter or not self.global_rate_limiter:
            redis_client = request.app.state.redis
            if not self.rate_limiter:
                self.rate_limiter = await create_rate_limiter(redis_client)
            if not self.global_rate_limiter:
                self.global_rate_limiter = await create_global_rate_limiter(redis_client)

        # Check global rate limit
        global_allowed, global_count = await self.global_rate_limiter.is_allowed()

        if not global_allowed:
            logger.warning(f"Global rate limit exceeded: {global_count}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "Global rate limit exceeded",
                    "current_count": global_count,
                    "limit": settings.total_rate_limit,
                    "reset_in_seconds": 1,
                    "type": "global"
                }
            )

        # Check provider-specific rate limit for SMS endpoints
        if self.include_provider_check and request.url.path.startswith("/api/sms/"):
            provider_limit_exceeded = await self._check_provider_limits()

            if provider_limit_exceeded:
                # Get current rate limit info for better error response
                rate_limit_info = await self._get_rate_limit_info()

                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": "All SMS providers are rate limited",
                        "rate_limit_info": rate_limit_info,
                        "reset_in_seconds": 1,
                        "type": "provider"
                    }
                )

        # Process request normally
        response = await call_next(request)
        return response

    async def _check_provider_limits(self) -> bool:
        """
        Check if all providers are rate limited.

        Returns:
            True if all providers are rate limited, False otherwise
        """
        providers = ["provider1", "provider2", "provider3"]

        for provider_id in providers:
            allowed, _ = await self.rate_limiter.is_allowed(provider_id)
            if allowed:
                return False  # At least one provider is available

        return True  # All providers are rate limited

    async def _get_rate_limit_info(self) -> Dict[str, Any]:
        """Get comprehensive rate limit information."""
        info = {
            "global": {
                "limit": settings.total_rate_limit,
                "current": 0,
                "limited": False
            },
            "providers": {}
        }

        # Get global rate limit info
        _, global_count = await self.global_rate_limiter.is_allowed()
        info["global"]["current"] = global_count
        info["global"]["limited"] = global_count >= settings.total_rate_limit

        # Get provider rate limit info
        providers = ["provider1", "provider2", "provider3"]
        for provider_id in providers:
            _, count = await self.rate_limiter.is_allowed(provider_id)
            info["providers"][provider_id] = {
                "limit": settings.provider_rate_limit,
                "current": count,
                "limited": count >= settings.provider_rate_limit
            }

        return info


def create_rate_limiting_middleware(
    exclude_paths: Optional[list] = None,
    include_provider_check: bool = True
) -> Callable:
    """
    Factory function to create rate limiting middleware.

    Args:
        exclude_paths: Paths to exclude from rate limiting
        include_provider_check: Whether to check provider-specific limits

    Returns:
        Rate limiting middleware class
    """

    class CustomRateLimitingMiddleware(RateLimitingMiddleware):
        """Custom rate limiting middleware with pre-configured settings."""

        def __init__(self, app):
            super().__init__(
                app=app,
                exclude_paths=exclude_paths,
                include_provider_check=include_provider_check
            )

    return CustomRateLimitingMiddleware


# Utility functions for manual rate limit checking
async def check_request_rate_limit(
    request: Request,
    provider_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Manually check rate limits for a request.

    Args:
        request: FastAPI request object
        provider_id: Optional specific provider to check

    Returns:
        Dictionary with rate limit information
    """
    redis_client = request.app.state.redis
    rate_limiter = await create_rate_limiter(redis_client)
    global_rate_limiter = await create_global_rate_limiter(redis_client)

    info = {
        "global": {
            "allowed": False,
            "current": 0,
            "limit": settings.total_rate_limit
        },
        "provider": None
    }

    # Check global rate limit
    allowed, count = await global_rate_limiter.is_allowed()
    info["global"]["allowed"] = allowed
    info["global"]["current"] = count

    # Check provider-specific rate limit if specified
    if provider_id:
        allowed, count = await rate_limiter.is_allowed(provider_id)
        info["provider"] = {
            "id": provider_id,
            "allowed": allowed,
            "current": count,
            "limit": settings.provider_rate_limit
        }

    return info


async def get_rate_limit_headers(
    rate_limiter: RateLimiter,
    global_rate_limiter: GlobalRateLimiter,
    provider_id: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate rate limit headers for HTTP response.

    Args:
        rate_limiter: RateLimiter instance
        global_rate_limiter: GlobalRateLimiter instance
        provider_id: Optional provider ID for specific headers

    Returns:
        Dictionary of HTTP headers
    """
    headers = {}

    # Global rate limit headers
    _, global_count = await global_rate_limiter.is_allowed()
    headers["X-RateLimit-Global-Limit"] = str(settings.total_rate_limit)
    headers["X-RateLimit-Global-Remaining"] = str(max(0, settings.total_rate_limit - global_count))
    headers["X-RateLimit-Global-Reset"] = str(int(time.time()) + 1)

    # Provider-specific headers if provider specified
    if provider_id:
        _, provider_count = await rate_limiter.is_allowed(provider_id)
        headers["X-RateLimit-Provider-Limit"] = str(settings.provider_rate_limit)
        headers["X-RateLimit-Provider-Remaining"] = str(max(0, settings.provider_rate_limit - provider_count))
        headers["X-RateLimit-Provider-Reset"] = str(int(time.time()) + 1)

    return headers