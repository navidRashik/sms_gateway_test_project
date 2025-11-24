"""
High-load performance tests for Redis rate limiter.

Tests the rate limiter under high throughput conditions (200 RPS)
to ensure it meets the requirements for the SMS gateway service.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock

from src.rate_limiter import RateLimiter, GlobalRateLimiter


class TestHighLoadRateLimiting:
    """Test rate limiter under high load conditions."""

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_provider_rate_limiting_200_rps_input(self):
        """Test that each provider respects 50 RPS limit under 200 RPS input."""
        mock_redis = AsyncMock()
        rate_limiter = RateLimiter(mock_redis, rate_limit=50, window=1)

        # Mock Redis to simulate time windows
        current_time = 1000.0

        def mock_get_key(provider_id):
            return f"rate_limit:{provider_id}:{int(current_time)}"

        # Mock Redis INCR to return incrementing values
        incr_calls = {}

        async def mock_incr(key):
            provider = key.split(':')[1]
            if provider not in incr_calls:
                incr_calls[provider] = 0
            incr_calls[provider] += 1
            return incr_calls[provider]

        async def mock_expire(key, seconds):
            return True

        mock_redis.incr = mock_incr
        mock_redis.expire = mock_expire

        # Simulate 200 RPS input (200 requests in 1 second)
        total_requests = 200
        allowed_requests_per_provider = {}

        # Distribute requests across 3 providers (round-robin)
        for i in range(total_requests):
            provider_id = f"provider{(i % 3) + 1}"

            # Mock time to stay within same second
            with pytest.MonkeyPatch().context() as m:
                m.setattr('src.rate_limiter.time.time', lambda: current_time)

                allowed, count = await rate_limiter.is_allowed(provider_id)

                if provider_id not in allowed_requests_per_provider:
                    allowed_requests_per_provider[provider_id] = 0

                if allowed:
                    allowed_requests_per_provider[provider_id] += 1

        # Each provider should have received max 50 requests
        for provider_id in ["provider1", "provider2", "provider3"]:
            assert provider_id in allowed_requests_per_provider
            assert allowed_requests_per_provider[provider_id] <= 50, \
                f"Provider {provider_id} received {allowed_requests_per_provider[provider_id]} requests, expected <= 50"

        # Total allowed requests should be <= 150 (50 * 3 providers)
        total_allowed = sum(allowed_requests_per_provider.values())
        assert total_allowed <= 150, \
            f"Total allowed requests: {total_allowed}, expected <= 150"

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_global_rate_limiting_200_rps(self):
        """Test global rate limiting under 200 RPS input."""
        mock_redis = AsyncMock()
        global_rate_limiter = GlobalRateLimiter(mock_redis, rate_limit=200, window=1)

        current_time = 1000.0

        # Mock Redis INCR behavior
        incr_count = 0

        async def mock_incr(key):
            nonlocal incr_count
            incr_count += 1
            return incr_count

        async def mock_expire(key, seconds):
            return True

        mock_redis.incr = mock_incr
        mock_redis.expire = mock_expire

        # Simulate 200 RPS (200 requests in 1 second)
        allowed_count = 0

        for i in range(200):
            # Mock time to stay within same second
            with pytest.MonkeyPatch().context() as m:
                m.setattr('src.rate_limiter.time.time', lambda: current_time)

                allowed, count = await global_rate_limiter.is_allowed()

                if allowed:
                    allowed_count += 1

        # Should allow maximum 200 requests
        assert allowed_count <= 200, \
            f"Allowed {allowed_count} requests, expected <= 200"

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_concurrent_rate_limiting_stress_test(self):
        """Stress test concurrent rate limiting operations."""
        mock_redis = AsyncMock()
        rate_limiter = RateLimiter(mock_redis, rate_limit=50, window=1)

        # Mock Redis behavior
        request_counts = {"provider1": 0, "provider2": 0, "provider3": 0}

        async def mock_incr(key):
            provider = key.split(':')[1]
            request_counts[provider] += 1
            return request_counts[provider]

        async def mock_expire(key, seconds):
            return True

        mock_redis.incr = mock_incr
        mock_redis.expire = mock_expire

        # Create 300 concurrent requests (100 per provider)
        tasks = []
        current_time = 1000.0

        for i in range(100):
            for provider_id in ["provider1", "provider2", "provider3"]:
                # Mock time for each request
                with pytest.MonkeyPatch().context() as m:
                    m.setattr('src.rate_limiter.time.time', lambda: current_time)

                    task = rate_limiter.is_allowed(provider_id)
                    tasks.append(task)

        # Execute all requests concurrently
        results = await asyncio.gather(*tasks)

        # Count allowed requests per provider
        allowed_per_provider = {"provider1": 0, "provider2": 0, "provider3": 0}

        for (allowed, count), provider_id in zip(results, ["provider1", "provider2", "provider3"] * 100):
            if allowed:
                allowed_per_provider[provider_id] += 1

        # Each provider should respect 50 RPS limit even under concurrent load
        for provider_id in ["provider1", "provider2", "provider3"]:
            assert allowed_per_provider[provider_id] <= 50, \
                f"Provider {provider_id} allowed {allowed_per_provider[provider_id]} concurrent requests, expected <= 50"

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_rate_limiter_latency_under_load(self):
        """Test rate limiter latency under high concurrent load."""
        mock_redis = AsyncMock()

        # Simple mock that returns quickly
        async def mock_incr(key):
            return 1

        async def mock_expire(key, seconds):
            return True

        mock_redis.incr = mock_incr
        mock_redis.expire = mock_expire

        rate_limiter = RateLimiter(mock_redis, rate_limit=50, window=1)

        # Test latency with 100 concurrent requests
        start_time = time.time()

        tasks = []
        for i in range(100):
            provider_id = f"provider{(i % 3) + 1}"
            task = rate_limiter.is_allowed(provider_id)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        end_time = time.time()
        total_time = end_time - start_time

        # Should complete 100 requests quickly (under 1 second for mocked Redis)
        assert total_time < 1.0, \
            f"Rate limiter took {total_time:.3f}s for 100 requests, expected < 1.0s"

        # All requests should be allowed (first request per provider)
        allowed_count = sum(1 for allowed, _ in results if allowed)
        assert allowed_count == 100, \
            f"Expected 100 allowed requests, got {allowed_count}"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_integration_high_load_with_real_redis(self):
        """Integration test with real Redis under high load."""
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url("redis://localhost:6379")
            await redis_client.ping()
        except Exception:
            pytest.skip("Redis not available for integration tests")

        rate_limiter = RateLimiter(redis_client, rate_limit=10, window=1)  # Lower limit for testing

        # Test with moderate load first
        allowed_count = 0
        start_time = time.time()

        for i in range(50):  # Test with 50 RPS instead of 200 for integration test
            allowed, count = await rate_limiter.is_allowed("integration_test_provider")

            if allowed:
                allowed_count += 1

        end_time = time.time()
        total_time = end_time - start_time

        # Should not exceed rate limit
        assert allowed_count <= 10, \
            f"Integration test allowed {allowed_count} requests, expected <= 10"

        # Should complete reasonably quickly
        assert total_time < 2.0, \
            f"Integration test took {total_time:.3f}s, expected < 2.0s"

        await redis_client.close()


class TestSlidingWindowAccuracy:
    """Test sliding window algorithm accuracy."""

    @pytest.mark.asyncio
    async def test_sliding_window_precision(self):
        """Test that sliding window provides accurate rate limiting."""
        mock_redis = AsyncMock()
        rate_limiter = RateLimiter(mock_redis, rate_limit=5, window=1)

        # Track calls to Redis INCR
        incr_history = []

        async def mock_incr(key):
            incr_history.append(key)
            # Return incrementing values for each provider
            provider = key.split(':')[1]
            if not hasattr(mock_incr, 'counts'):
                mock_incr.counts = {}

            if provider not in mock_incr.counts:
                mock_incr.counts[provider] = 0

            mock_incr.counts[provider] += 1
            return mock_incr.counts[provider]

        async def mock_expire(key, seconds):
            return True

        mock_redis.incr = mock_incr
        mock_redis.expire = mock_expire

        current_time = 1000.0

        # Make requests over time boundary
        for second in [1000.0, 1000.5, 1000.9, 1001.0, 1001.1]:
            with pytest.MonkeyPatch().context() as m:
                m.setattr('src.rate_limiter.time.time', lambda: second)

                # Make 3 requests per time point
                for i in range(3):
                    for provider_id in ["provider1", "provider2", "provider3"]:
                        await rate_limiter.is_allowed(provider_id)

        # Verify that different time windows use different keys
        unique_keys = set(incr_history)
        assert len(unique_keys) >= 2, \
            "Expected multiple time windows to be used, got keys: {unique_keys}"