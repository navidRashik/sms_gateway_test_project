"""
Tests for rate limiting middleware.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.middleware import RateLimitingMiddleware, create_rate_limiting_middleware
from src.rate_limiter import RateLimiter, GlobalRateLimiter


@pytest.fixture
def app():
    """Create FastAPI test app."""
    test_app = FastAPI()
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock()
    return redis


@pytest.fixture
def mock_rate_limiter(mock_redis):
    """Create mock rate limiter."""
    limiter = AsyncMock(spec=RateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


@pytest.fixture
def mock_global_rate_limiter(mock_redis):
    """Create mock global rate limiter."""
    limiter = AsyncMock(spec=GlobalRateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


class TestRateLimitingMiddleware:
    """Test rate limiting middleware functionality."""

    @pytest.mark.asyncio
    async def test_middleware_allows_normal_request(self, app, mock_rate_limiter, mock_global_rate_limiter):
        """Test middleware allows normal requests."""
        # Add middleware
        app.add_middleware(
            RateLimitingMiddleware,
            rate_limiter=mock_rate_limiter,
            global_rate_limiter=mock_global_rate_limiter,
            exclude_paths=["/health"],
            include_provider_check=False
        )

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # Mock successful global rate limit check
        mock_global_rate_limiter.is_allowed.return_value = (True, 1)

        response = client.get("/test")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_blocks_global_rate_limit(self, app, mock_global_rate_limiter):
        """Test middleware blocks globally rate limited requests."""
        # Add middleware
        app.add_middleware(
            RateLimitingMiddleware,
            global_rate_limiter=mock_global_rate_limiter,
            include_provider_check=False
        )

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # Mock exceeded global rate limit
        mock_global_rate_limiter.is_allowed.return_value = (False, 250)

        response = client.get("/test")
        assert response.status_code == 429

        data = response.json()
        assert data["error"] == "Global rate limit exceeded"
        assert data["type"] == "global"

    @pytest.mark.asyncio
    async def test_middleware_blocks_provider_rate_limit(self, app, mock_rate_limiter, mock_global_rate_limiter):
        """Test middleware blocks provider rate limited requests."""
        # Add middleware with provider checking
        app.add_middleware(
            RateLimitingMiddleware,
            rate_limiter=mock_rate_limiter,
            global_rate_limiter=mock_global_rate_limiter,
            include_provider_check=True
        )

        @app.get("/api/sms/test")
        async def sms_test_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # Mock all providers rate limited
        async def mock_is_allowed_provider(provider_id):
            return (False, 60)  # All providers limited

        mock_rate_limiter.is_allowed.side_effect = mock_is_allowed_provider

        response = client.get("/api/sms/test")
        assert response.status_code == 429

        data = response.json()
        assert data["error"] == "All SMS providers are rate limited"
        assert data["type"] == "provider"

    @pytest.mark.asyncio
    async def test_middleware_excludes_paths(self, app, mock_global_rate_limiter):
        """Test middleware excludes specified paths."""
        # Add middleware
        app.add_middleware(
            RateLimitingMiddleware,
            global_rate_limiter=mock_global_rate_limiter,
            exclude_paths=["/health", "/docs"],
            include_provider_check=False
        )

        @app.get("/health")
        async def health_endpoint():
            return {"status": "healthy"}

        @app.get("/docs")
        async def docs_endpoint():
            return {"docs": "available"}

        @app.get("/api/test")
        async def api_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # Mock rate limit exceeded for non-excluded paths
        mock_global_rate_limiter.is_allowed.return_value = (False, 250)

        # Excluded paths should work even when rate limited
        health_response = client.get("/health")
        assert health_response.status_code == 200

        docs_response = client.get("/docs")
        assert docs_response.status_code == 200

        # Non-excluded path should be blocked
        api_response = client.get("/api/test")
        assert api_response.status_code == 429

    @pytest.mark.asyncio
    async def test_middleware_allows_when_provider_available(self, app, mock_rate_limiter, mock_global_rate_limiter):
        """Test middleware allows requests when provider available."""
        # Add middleware with provider checking
        app.add_middleware(
            RateLimitingMiddleware,
            rate_limiter=mock_rate_limiter,
            global_rate_limiter=mock_global_rate_limiter,
            include_provider_check=True
        )

        @app.get("/api/sms/test")
        async def sms_test_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # Mock one provider available
        async def mock_is_allowed_provider(provider_id):
            if provider_id == "provider1":
                return (True, 10)  # Provider 1 available
            return (False, 60)  # Others limited

        mock_rate_limiter.is_allowed.side_effect = mock_is_allowed_provider

        response = client.get("/api/sms/test")
        assert response.status_code == 200


class TestRateLimitingMiddlewareFactory:
    """Test rate limiting middleware factory."""

    @pytest.mark.asyncio
    async def test_create_rate_limiting_middleware(self, app):
        """Test creating rate limiting middleware."""
        middleware_class = create_rate_limiting_middleware(
            exclude_paths=["/health"],
            include_provider_check=True
        )

        # Add to app
        app.add_middleware(middleware_class)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        client = TestClient(app)
        response = client.get("/test")
        # Should work without Redis for this basic test
        assert response.status_code == 200


class TestMiddlewareIntegration:
    """Integration tests for middleware."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_middleware_with_redis_integration(self):
        """Test middleware with real Redis."""
        # Skip if Redis not available
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url("redis://localhost:6379")
            await redis_client.ping()
        except:
            pytest.skip("Redis not available for integration tests")

        app = FastAPI()
        app.add_middleware(RateLimitingMiddleware, exclude_paths=["/health"])

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        client = TestClient(app)

        # This would test with real Redis
        # For now, just verify the app starts
        response = client.get("/health")
        assert response.status_code == 200

        await redis_client.close()


class TestUtilityFunctions:
    """Test utility functions in middleware."""

    @pytest.mark.asyncio
    async def test_check_request_rate_limit(self, mock_redis, mock_rate_limiter, mock_global_rate_limiter):
        """Test manual rate limit checking."""
        from src.middleware import check_request_rate_limit

        # Mock request with app state
        mock_request = AsyncMock()
        mock_request.app.state.redis = mock_redis

        with patch('src.middleware.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.middleware.create_global_rate_limiter', return_value=mock_global_rate_limiter):

            result = await check_request_rate_limit(mock_request)

            assert "global" in result
            assert "provider" in result
            assert result["global"]["allowed"] is True

    @pytest.mark.asyncio
    async def test_get_rate_limit_headers(self, mock_rate_limiter, mock_global_rate_limiter):
        """Test generating rate limit headers."""
        from src.middleware import get_rate_limit_headers

        headers = await get_rate_limit_headers(
            mock_rate_limiter,
            mock_global_rate_limiter,
            provider_id="provider1"
        )

        assert "X-RateLimit-Global-Limit" in headers
        assert "X-RateLimit-Global-Remaining" in headers
        assert "X-RateLimit-Provider-Limit" in headers
        assert "X-RateLimit-Provider-Remaining" in headers