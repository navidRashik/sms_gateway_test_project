"""
Tests for rate limiting middleware functionality.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from src.middleware import (
    RateLimitingMiddleware,
    create_rate_limiting_middleware,
    check_request_rate_limit,
    get_rate_limit_headers
)
from src.rate_limiter import RateLimiter, GlobalRateLimiter


@pytest.fixture
def app():
    """Create FastAPI test app."""
    app = FastAPI()
    return app


@pytest.fixture
def test_client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_rate_limiter():
    """Create mock rate limiter."""
    limiter = AsyncMock(spec=RateLimiter)
    limiter.is_allowed = AsyncMock(return_value=(True, 1))
    limiter.get_current_count = AsyncMock(return_value=1)
    return limiter


@pytest.fixture
def mock_global_rate_limiter():
    """Create mock global rate limiter."""
    limiter = AsyncMock(spec=GlobalRateLimiter)
    limiter.is_allowed = AsyncMock(return_value=(True, 1))
    limiter.get_current_count = AsyncMock(return_value=1)
    return limiter


@pytest.fixture
def middleware(mock_rate_limiter, mock_global_rate_limiter):
    """Create middleware instance for testing."""
    return RateLimitingMiddleware(
        app=FastAPI(),
        rate_limiter=mock_rate_limiter,
        global_rate_limiter=mock_global_rate_limiter
    )


class TestRateLimitingMiddleware:
    """Test cases for RateLimitingMiddleware class."""

    def test_init(self, middleware, mock_rate_limiter, mock_global_rate_limiter):
        """Test middleware initialization."""
        assert middleware.rate_limiter == mock_rate_limiter
        assert middleware.global_rate_limiter == mock_global_rate_limiter
        assert middleware.exclude_paths == ["/health", "/docs", "/openapi.json"]
        assert middleware.include_provider_check is True

    def test_init_with_custom_exclude_paths(self):
        """Test middleware initialization with custom exclude paths."""
        custom_paths = ["/custom", "/health"]
        middleware = RateLimitingMiddleware(
            app=FastAPI(),
            exclude_paths=custom_paths
        )
        assert middleware.exclude_paths == custom_paths

    @pytest.mark.asyncio
    async def test_dispatch_excluded_path(self, middleware):
        """Test that excluded paths bypass rate limiting."""
        # Create mock request for excluded path
        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/health"
        mock_request.app.state.redis = AsyncMock()

        # Mock call_next and expected response instance
        mock_response = Response("OK")
        mock_call_next = AsyncMock(return_value=mock_response)
    
        response = await middleware.dispatch(mock_request, mock_call_next)
    
        # Should not check rate limits for excluded paths
        assert response is mock_response
        middleware.global_rate_limiter.is_allowed.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_global_rate_limit_exceeded(self, middleware):
        """Test response when global rate limit is exceeded."""
        # Setup global rate limiter to deny request
        middleware.global_rate_limiter.is_allowed = AsyncMock(return_value=(False, 201))

        # Create mock request for SMS endpoint
        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/sms/send"
        mock_request.app.state.redis = AsyncMock()

        mock_call_next = AsyncMock()

        response = await middleware.dispatch(mock_request, mock_call_next)

        assert response.status_code == 429
        assert "Global rate limit exceeded" in response.body.decode()
        middleware.rate_limiter.is_allowed.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_provider_rate_limit_exceeded(self, middleware):
        """Test response when all providers are rate limited."""
        # Setup rate limiters
        middleware.global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))
        # Use a side-effect function so repeated calls don't exhaust a finite iterator
        middleware.rate_limiter.is_allowed = AsyncMock(side_effect=lambda provider_id: (False, 51))

        # Create mock request for SMS endpoint
        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/sms/send"
        mock_request.app.state.redis = AsyncMock()

        mock_call_next = AsyncMock()

        response = await middleware.dispatch(mock_request, mock_call_next)

        assert response.status_code == 429
        assert "All SMS providers are rate limited" in response.body.decode()

    @pytest.mark.asyncio
    async def test_dispatch_successful_request(self, middleware):
        """Test successful request processing."""
        # Setup rate limiters to allow request
        middleware.global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))
        middleware.rate_limiter.is_allowed = AsyncMock(side_effect=[(True, 1), (True, 2), (True, 3)])

        # Create mock request for non-SMS endpoint
        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/status"
        mock_request.app.state.redis = AsyncMock()

        mock_response = Response("OK")
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await middleware.dispatch(mock_request, mock_call_next)

        assert response == mock_response
        mock_call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_provider_limits_all_allowed(self, middleware):
        """Test _check_provider_limits when all providers are available."""
        middleware.rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))

        result = await middleware._check_provider_limits()

        assert result is False  # False means at least one provider is available

    @pytest.mark.asyncio
    async def test_check_provider_limits_all_limited(self, middleware):
        """Test _check_provider_limits when all providers are rate limited."""
        middleware.rate_limiter.is_allowed = AsyncMock(return_value=(False, 51))

        result = await middleware._check_provider_limits()

        assert result is True  # True means all providers are limited

    @pytest.mark.asyncio
    async def test_get_rate_limit_info(self, middleware):
        """Test _get_rate_limit_info method."""
        # Setup mocks
        middleware.global_rate_limiter.is_allowed = AsyncMock(return_value=(False, 201))
        middleware.rate_limiter.is_allowed = AsyncMock(side_effect=[
            (False, 51), (True, 25), (False, 51)
        ])

        info = await middleware._get_rate_limit_info()

        assert "global" in info
        assert "providers" in info
        assert info["global"]["limited"] is True
        assert info["providers"]["provider1"]["limited"] is True
        assert info["providers"]["provider2"]["limited"] is False
        assert info["providers"]["provider3"]["limited"] is True


class TestCreateRateLimitingMiddleware:
    """Test cases for create_rate_limiting_middleware factory function."""

    def test_create_rate_limiting_middleware(self):
        """Test factory function creates proper middleware."""
        middleware_class = create_rate_limiting_middleware(
            exclude_paths=["/custom"],
            include_provider_check=False
        )

        # Create instance
        app = FastAPI()
        middleware_instance = middleware_class(app)

        assert isinstance(middleware_instance, RateLimitingMiddleware)
        assert middleware_instance.exclude_paths == ["/custom"]
        assert middleware_instance.include_provider_check is False


class TestUtilityFunctions:
    """Test cases for utility functions."""

    @pytest.mark.asyncio
    async def test_check_request_rate_limit(self, mock_rate_limiter, mock_global_rate_limiter):
        """Test check_request_rate_limit utility function."""
        mock_request = AsyncMock(spec=Request)
        mock_request.app.state.redis = AsyncMock()

        with patch('src.middleware.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.middleware.create_global_rate_limiter', return_value=mock_global_rate_limiter):

            # Setup mocks
            mock_global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 5))
            mock_rate_limiter.is_allowed = AsyncMock(return_value=(True, 10))

            result = await check_request_rate_limit(mock_request, provider_id="provider1")

            assert result["global"]["allowed"] is True
            assert result["global"]["current"] == 5
            assert result["provider"]["id"] == "provider1"
            assert result["provider"]["allowed"] is True
            assert result["provider"]["current"] == 10

    @pytest.mark.asyncio
    async def test_check_request_rate_limit_no_provider(self, mock_rate_limiter, mock_global_rate_limiter):
        """Test check_request_rate_limit without specific provider."""
        mock_request = AsyncMock(spec=Request)
        mock_request.app.state.redis = AsyncMock()

        with patch('src.middleware.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.middleware.create_global_rate_limiter', return_value=mock_global_rate_limiter):

            mock_global_rate_limiter.is_allowed = AsyncMock(return_value=(False, 201))

            result = await check_request_rate_limit(mock_request)

            assert result["global"]["allowed"] is False
            assert result["global"]["current"] == 201
            assert result["provider"] is None

    @pytest.mark.asyncio
    async def test_get_rate_limit_headers(self, mock_rate_limiter, mock_global_rate_limiter):
        """Test get_rate_limit_headers utility function."""
        # Setup mocks
        mock_global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 5))
        mock_rate_limiter.is_allowed = AsyncMock(return_value=(True, 10))

        headers = await get_rate_limit_headers(
            mock_rate_limiter,
            mock_global_rate_limiter,
            provider_id="provider1"
        )

        assert "X-RateLimit-Global-Limit" in headers
        assert "X-RateLimit-Global-Remaining" in headers
        assert "X-RateLimit-Global-Reset" in headers
        assert "X-RateLimit-Provider-Limit" in headers
        assert "X-RateLimit-Provider-Remaining" in headers
        assert "X-RateLimit-Provider-Reset" in headers

        assert headers["X-RateLimit-Global-Limit"] == "200"
        assert headers["X-RateLimit-Global-Remaining"] == "195"
        assert headers["X-RateLimit-Provider-Limit"] == "50"
        assert headers["X-RateLimit-Provider-Remaining"] == "40"

    @pytest.mark.asyncio
    async def test_get_rate_limit_headers_no_provider(self, mock_rate_limiter, mock_global_rate_limiter):
        """Test get_rate_limit_headers without provider ID."""
        mock_global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))

        headers = await get_rate_limit_headers(mock_rate_limiter, mock_global_rate_limiter)

        assert "X-RateLimit-Global-Limit" in headers
        assert "X-RateLimit-Global-Remaining" in headers
        assert "X-RateLimit-Global-Reset" in headers

        # Provider-specific headers should not be present
        assert "X-RateLimit-Provider-Limit" not in headers
        assert "X-RateLimit-Provider-Remaining" not in headers
        assert "X-RateLimit-Provider-Reset" not in headers


class TestMiddlewareIntegration:
    """Integration tests for middleware with FastAPI app."""

    @pytest.mark.asyncio
    async def test_middleware_initialization_without_limiters(self):
        """Test middleware initializes rate limiters when not provided."""
        app = FastAPI()
        middleware = RateLimitingMiddleware(app)

        # App should not have redis initially
        assert not hasattr(app.state, 'redis')

        # Create mock request that provides redis
        mock_request = AsyncMock(spec=Request)
        mock_redis = AsyncMock()
        app.state.redis = mock_redis
        # Ensure the mock request references the same FastAPI app so middleware initialization
        # uses the provided mock_redis instance (tests expect create_rate_limiter called with it).
        mock_request.app = app

        with patch('src.middleware.create_rate_limiter') as mock_create_rate_limiter, \
             patch('src.middleware.create_global_rate_limiter') as mock_create_global_rate_limiter:

            mock_rate_limiter = AsyncMock()
            mock_rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))
            mock_global_rate_limiter = AsyncMock()
            mock_global_rate_limiter.is_allowed = AsyncMock(return_value=(True, 1))
            mock_create_rate_limiter.return_value = mock_rate_limiter
            mock_create_global_rate_limiter.return_value = mock_global_rate_limiter

            # This will trigger initialization
            await middleware.dispatch(mock_request, AsyncMock(return_value=Response("OK")))

            mock_create_rate_limiter.assert_called_once_with(mock_redis)
            mock_create_global_rate_limiter.assert_called_once_with(mock_redis)