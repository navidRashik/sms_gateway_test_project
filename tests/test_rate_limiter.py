"""
Tests for Redis rate limiter functionality.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

from src.rate_limiter import RateLimiter, GlobalRateLimiter, create_rate_limiter, create_global_rate_limiter


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock(spec=Redis)
    return redis


@pytest.fixture
def rate_limiter(mock_redis):
    """Create RateLimiter instance for testing."""
    return RateLimiter(mock_redis, rate_limit=5, window=1)


@pytest.fixture
def global_rate_limiter(mock_redis):
    """Create GlobalRateLimiter instance for testing."""
    return GlobalRateLimiter(mock_redis, rate_limit=10, window=1)


class TestRateLimiter:
    """Test cases for RateLimiter class."""

    def test_init(self, rate_limiter, mock_redis):
        """Test RateLimiter initialization."""
        assert rate_limiter.redis == mock_redis
        assert rate_limiter.rate_limit == 5
        assert rate_limiter.window == 1

    def test_get_key_format(self, rate_limiter):
        """Test Redis key generation."""
        key = rate_limiter._get_key("provider1")
        assert key == "rate_limit:provider1"

    @pytest.mark.asyncio
    async def test_is_allowed_first_request(self, rate_limiter, mock_redis):
        """Test first request is always allowed."""
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            allowed, count = await rate_limiter.is_allowed("provider1")

            assert allowed is True
            assert count == 1
            mock_redis.incr.assert_called_once()
            mock_redis.expire.assert_called_once_with("rate_limit:provider1", 1)

    @pytest.mark.asyncio
    async def test_is_allowed_within_limit(self, rate_limiter, mock_redis):
        """Test requests within limit are allowed."""
        mock_redis.incr = AsyncMock(return_value=3)

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is True
        assert count == 3

    @pytest.mark.asyncio
    async def test_is_allowed_exceeds_limit(self, rate_limiter, mock_redis):
        """Test requests exceeding limit are denied."""
        mock_redis.incr = AsyncMock(return_value=6)  # Exceeds limit of 5

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is False
        assert count == 6

    @pytest.mark.asyncio
    async def test_get_current_count(self, rate_limiter, mock_redis):
        """Test getting current count without incrementing."""
        mock_redis.get = AsyncMock(return_value="3")

        count = await rate_limiter.get_current_count("provider1")

        assert count == 3
        mock_redis.get.assert_called_once()
        mock_redis.incr.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_current_count_none(self, rate_limiter, mock_redis):
        """Test getting count when no requests made."""
        mock_redis.get = AsyncMock(return_value=None)

        count = await rate_limiter.get_current_count("provider1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_reset_provider_limit(self, rate_limiter, mock_redis):
        """Test resetting provider rate limit."""
        mock_redis.delete = AsyncMock(return_value=True)

        result = await rate_limiter.reset_provider_limit("provider1")

        assert result is True
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_within_limit(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics when within limit."""
        mock_redis.get = AsyncMock(return_value="3")

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            stats = await rate_limiter.get_rate_limit_stats("provider1")

            assert stats["provider_id"] == "provider1"
            assert stats["current_count"] == 3
            assert stats["rate_limit"] == 5
            assert stats["remaining"] == 2
            assert stats["is_limited"] is False
            assert stats["window_seconds"] == 1

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_at_limit(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics when at limit."""
        mock_redis.get = AsyncMock(return_value="5")

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            stats = await rate_limiter.get_rate_limit_stats("provider1")

            assert stats["current_count"] == 5
            assert stats["remaining"] == 0
            assert stats["is_limited"] is True

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_over_limit(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics when over limit."""
        mock_redis.get = AsyncMock(return_value="7")

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            stats = await rate_limiter.get_rate_limit_stats("provider1")

            assert stats["current_count"] == 7
            assert stats["remaining"] == 0
            assert stats["is_limited"] is True

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_no_requests(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics when no requests made."""
        mock_redis.get = AsyncMock(return_value=None)

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            stats = await rate_limiter.get_rate_limit_stats("provider1")

            assert stats["current_count"] == 0
            assert stats["remaining"] == 5
            assert stats["is_limited"] is False

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_redis_error(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics with Redis error."""
        mock_redis.get = AsyncMock(side_effect=RedisError("Redis error"))

        stats = await rate_limiter.get_rate_limit_stats("provider1")

        assert stats["provider_id"] == "provider1"
        assert stats["error"] == "Redis error"
        assert stats["is_limited"] is False  # Default to not limited on error

    @pytest.mark.asyncio
    async def test_get_all_providers_stats(self, rate_limiter, mock_redis):
        """Test getting rate limit statistics for all providers."""
        def mock_get_side_effect(key):
            provider_map = {
                "rate_limit:provider1": "2",
                "rate_limit:provider2": "5",
                "rate_limit:provider3": "1"
            }
            return provider_map.get(key)

        mock_redis.get = AsyncMock(side_effect=mock_get_side_effect)

        with patch('src.rate_limiter.time.time', return_value=1000.0):
            stats = await rate_limiter.get_all_providers_stats()

            assert "providers" in stats
            assert "provider1" in stats["providers"]
            assert "provider2" in stats["providers"]
            assert "provider3" in stats["providers"]
            assert stats["rate_limit_per_provider"] == 5
            assert stats["window_seconds"] == 1
            assert stats["timestamp"] == 1000.0

            # Check individual provider stats
            assert stats["providers"]["provider1"]["current_count"] == 2
            assert stats["providers"]["provider2"]["current_count"] == 5
            assert stats["providers"]["provider3"]["current_count"] == 1

    @pytest.mark.asyncio
    async def test_is_allowed_redis_connection_error(self, rate_limiter, mock_redis):
        """Test is_allowed with Redis connection error."""
        mock_redis.incr = AsyncMock(side_effect=ConnectionError("Connection failed"))

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is True  # Should allow on Redis connection error
        assert count == 0

    @pytest.mark.asyncio
    async def test_is_allowed_redis_timeout_error(self, rate_limiter, mock_redis):
        """Test is_allowed with Redis timeout error."""
        mock_redis.incr = AsyncMock(side_effect=TimeoutError("Timeout"))

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is True  # Should allow on Redis timeout
        assert count == 0

    @pytest.mark.asyncio
    async def test_is_allowed_redis_error(self, rate_limiter, mock_redis):
        """Test is_allowed with general Redis error."""
        mock_redis.incr = AsyncMock(side_effect=RedisError("Redis error"))

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is True  # Should allow on Redis error
        assert count == 0

    @pytest.mark.asyncio
    async def test_is_allowed_unexpected_error(self, rate_limiter, mock_redis):
        """Test is_allowed with unexpected error."""
        mock_redis.incr = AsyncMock(side_effect=Exception("Unexpected error"))

        allowed, count = await rate_limiter.is_allowed("provider1")

        assert allowed is False  # Should deny on unexpected error
        assert count == 6  # rate_limit + 1

    @pytest.mark.asyncio
    async def test_get_current_count_redis_error(self, rate_limiter, mock_redis):
        """Test get_current_count with Redis error."""
        mock_redis.get = AsyncMock(side_effect=RedisError("Redis error"))

        count = await rate_limiter.get_current_count("provider1")

        assert count == 0  # Should return 0 on error

    @pytest.mark.asyncio
    async def test_reset_provider_limit_redis_error(self, rate_limiter, mock_redis):
        """Test reset_provider_limit with Redis error."""
        mock_redis.delete = AsyncMock(side_effect=RedisError("Redis error"))

        result = await rate_limiter.reset_provider_limit("provider1")

        assert result is True  # Should still return True even on error

    @pytest.mark.asyncio
    async def test_multiple_providers_different_limits(self, mock_redis):
        """Test rate limiting with multiple providers having different usage."""
        # Provider 1: within limit (2/5)
        # Provider 2: at limit (5/5)
        # Provider 3: over limit (6/5)

        def mock_incr_side_effect(key):
            if "provider1" in key:
                return 3  # First call returns 3, second call returns 4
            elif "provider2" in key:
                return 5  # At limit
            elif "provider3" in key:
                return 7  # Over limit
            return 1

        mock_redis.incr = AsyncMock(side_effect=mock_incr_side_effect)
        mock_redis.expire = AsyncMock(return_value=True)

        rate_limiter = RateLimiter(mock_redis, rate_limit=5, window=1)

        # Test provider 1 (within limit)
        allowed, count = await rate_limiter.is_allowed("provider1")
        assert allowed is True
        assert count == 3

        # Test provider 2 (at limit)
        allowed, count = await rate_limiter.is_allowed("provider2")
        assert allowed is True
        assert count == 5

        # Test provider 3 (over limit)
        allowed, count = await rate_limiter.is_allowed("provider3")
        assert allowed is False
        assert count == 7

    @pytest.mark.asyncio
    async def test_window_expiry_behavior(self, mock_redis):
        """Test that counters reset after window expiry."""
        # First request in new window
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)

        rate_limiter = RateLimiter(mock_redis, rate_limit=5, window=1)

        allowed, count = await rate_limiter.is_allowed("provider1")
        assert allowed is True
        assert count == 1
        mock_redis.expire.assert_called_once()

        # Reset mock for next call
        mock_redis.reset_mock()
        mock_redis.incr = AsyncMock(return_value=2)

        # Second request in same window
        allowed, count = await rate_limiter.is_allowed("provider1")
        assert allowed is True
        assert count == 2
        mock_redis.expire.assert_not_called()  # Should not expire again

    @pytest.mark.asyncio
    async def test_high_load_scenario(self, mock_redis):
        """Test rate limiter under high concurrent load simulation."""
        rate_limiter = RateLimiter(mock_redis, rate_limit=100, window=1)

        # Simulate 150 concurrent requests
        def mock_incr_load_test(key):
            # Simulate counter going from 50 to 150
            if not hasattr(mock_incr_load_test, 'call_count'):
                mock_incr_load_test.call_count = 0
            mock_incr_load_test.call_count += 1
            return mock_incr_load_test.call_count + 49

        mock_redis.incr = AsyncMock(side_effect=mock_incr_load_test)
        mock_redis.expire = AsyncMock(return_value=True)

        # First 100 requests should be allowed
        allowed, count = await rate_limiter.is_allowed("provider1")
        assert allowed is True
        assert count == 50  # First call returns 50

        # Reset for next call simulation
        mock_redis.reset_mock()
        def mock_incr_second_call(key):
            return 101  # Would be rate limited

        mock_redis.incr = AsyncMock(side_effect=mock_incr_second_call)

        # 101st request should be denied
        allowed, count = await rate_limiter.is_allowed("provider1")
        assert allowed is False
        assert count == 101


class TestGlobalRateLimiter:
    """Test cases for GlobalRateLimiter class."""

    @pytest.mark.asyncio
    async def test_is_allowed_first_request(self, global_rate_limiter, mock_redis):
        """Test first global request is always allowed."""
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)

        allowed, count = await global_rate_limiter.is_allowed()

        assert allowed is True
        assert count == 1

    @pytest.mark.asyncio
    async def test_is_allowed_exceeds_global_limit(self, global_rate_limiter, mock_redis):
        """Test requests exceeding global limit are denied."""
        mock_redis.incr = AsyncMock(return_value=11)  # Exceeds limit of 10

        allowed, count = await global_rate_limiter.is_allowed()

        assert allowed is False
        assert count == 11


class TestFactoryFunctions:
    """Test cases for factory functions."""

    @pytest.mark.asyncio
    async def test_create_rate_limiter(self, mock_redis):
        """Test create_rate_limiter factory function."""
        limiter = await create_rate_limiter(mock_redis)

        assert isinstance(limiter, RateLimiter)
        assert limiter.rate_limit == 50  # Default from settings
        assert limiter.window == 1

    @pytest.mark.asyncio
    async def test_create_global_rate_limiter(self, mock_redis):
        """Test create_global_rate_limiter factory function."""
        limiter = await create_global_rate_limiter(mock_redis)

        assert isinstance(limiter, GlobalRateLimiter)
        assert limiter.rate_limit == 200  # Default from settings
        assert limiter.window == 1


class TestIntegration:
    """Integration tests with actual Redis (if available)."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rate_limiter_integration(self):
        """Test rate limiter with real Redis."""
        # Skip if Redis not available
        try:
            redis_client = Redis.from_url("redis://localhost:6379")
            await redis_client.ping()
        except (ConnectionError, TimeoutError):
            pytest.skip("Redis not available for integration tests")

        rate_limiter = RateLimiter(redis_client, rate_limit=3, window=1)
        provider = "test_provider_integration"

        # Clean up before test
        await redis_client.delete(rate_limiter._get_key(provider))

        # First 3 requests should be allowed
        for i in range(3):
            allowed, count = await rate_limiter.is_allowed(provider)
            assert allowed is True
            assert count == i + 1

        # 4th request should be denied
        allowed, count = await rate_limiter.is_allowed(provider)
        assert allowed is False
        assert count == 4

        # Manually expire the key to simulate window reset
        await redis_client.delete(rate_limiter._get_key(provider))

        # Next request should be allowed again
        allowed, count = await rate_limiter.is_allowed(provider)
        assert allowed is True
        assert count == 1

        # Clean up after test
        await redis_client.delete(rate_limiter._get_key(provider))
        await redis_client.close()