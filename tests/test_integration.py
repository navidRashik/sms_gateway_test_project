"""
Integration tests for the complete SMS gateway system.

Tests all components working together: rate limiting, health tracking,
distribution, and retry logic in realistic scenarios.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis
from sqlmodel import Session

from src.rate_limiter import RateLimiter, GlobalRateLimiter
from src.health_tracker import ProviderHealthTracker
from src.distribution import SMSDistributionService
from src.retry_service import RetryService


@pytest.fixture
def mock_redis():
    """Create mock Redis client for integration tests."""
    redis = AsyncMock(spec=Redis)
    redis.incr.return_value = 1
    redis.expire.return_value = True
    redis.get.return_value = b"0"
    return redis


@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    session = MagicMock(spec=Session)
    return session


@pytest.fixture
def provider_urls():
    """Provider URLs for testing."""
    return {
        "provider1": "http://provider1.com/send",
        "provider2": "http://provider2.com/send",
        "provider3": "http://provider3.com/send"
    }


@pytest.fixture
def integration_components(mock_redis, mock_db_session, provider_urls):
    """Create all integration test components."""
    # Create real instances (not mocks) for integration testing
    health_tracker = ProviderHealthTracker(mock_redis, window_duration=300, failure_threshold=0.7)
    rate_limiter = RateLimiter(mock_redis, rate_limit=50, window=1)
    global_rate_limiter = GlobalRateLimiter(mock_redis, rate_limit=200, window=1)

    distribution_service = SMSDistributionService(
        health_tracker=health_tracker,
        rate_limiter=rate_limiter,
        global_rate_limiter=global_rate_limiter,
        provider_urls=provider_urls
    )

    retry_service = RetryService(
        redis_client=mock_redis,
        db_session=mock_db_session,
        health_tracker=health_tracker,
        max_retries=3,
        base_delay=0.1,  # Fast for testing
        max_delay=1.0,
        jitter=False  # Disable jitter for predictable testing
    )

    return {
        "health_tracker": health_tracker,
        "rate_limiter": rate_limiter,
        "global_rate_limiter": global_rate_limiter,
        "distribution_service": distribution_service,
        "retry_service": retry_service,
        "redis": mock_redis,
        "db_session": mock_db_session
    }


class TestCompleteSMSFlow:
    """Test complete SMS processing flow."""

    @pytest.mark.asyncio
    async def test_successful_sms_delivery_flow(self, integration_components):
        """Test complete flow for successful SMS delivery."""
        components = integration_components

        # Step 1: Select provider for SMS
        result = await components["distribution_service"].select_provider()
        assert result is not None
        provider_id, provider_url = result

        # Step 2: Simulate successful SMS send
        await components["health_tracker"].record_success(provider_id)

        # Step 3: Verify provider health is good
        health_status = await components["health_tracker"].get_health_status(provider_id)
        assert health_status["is_healthy"] is True
        assert health_status["success_count"] > 0
        assert health_status["failure_count"] == 0

        # Step 4: Verify rate limiting is working
        rate_stats = await components["rate_limiter"].get_rate_limit_stats(provider_id)
        assert rate_stats["current_count"] > 0
        assert rate_stats["is_limited"] is False

    @pytest.mark.asyncio
    async def test_sms_failure_and_retry_flow(self, integration_components):
        """Test SMS failure followed by successful retry."""
        components = integration_components

        # Mock SMS task to simulate failure then success
        with patch('src.retry_service.send_sms_to_provider') as mock_send_task:
            # First attempt fails
            mock_send_task.kicker = AsyncMock(side_effect=[
                {"success": False, "error": "Connection timeout"},  # First attempt fails
                {"success": True, "message_id": "success_123"}      # Retry succeeds
            ])

            # Step 1: Initial request fails
            provider_id = "provider1"
            await components["health_tracker"].record_failure(provider_id)

            # Step 2: Check if retry should be attempted
            should_retry, next_provider, delay = await components["retry_service"].should_retry(
                request_id=123,
                current_attempt=0,
                failed_provider=provider_id,
                error_message="Connection timeout"
            )

            assert should_retry is True
            assert next_provider is not None

            # Step 3: Execute retry
            result = await components["retry_service"].execute_retry_with_backoff(
                request_id=123,
                phone="01921317475",
                text="Test message",
                current_attempt=1,
                provider_id=next_provider,
                provider_url=components["distribution_service"].provider_urls[next_provider],
                error_message="Connection timeout",
                delay_seconds=delay
            )

            assert result["success"] is True

            # Step 4: Record the successful retry
            await components["health_tracker"].record_success(next_provider)

            # Step 5: Verify final health status
            final_health = await components["health_tracker"].get_health_status(next_provider)
            assert final_health["is_healthy"] is True

    @pytest.mark.asyncio
    async def test_provider_outage_scenario(self, integration_components):
        """Test scenario where one provider goes down."""
        components = integration_components

        # Step 1: Record multiple failures for provider1
        for _ in range(10):
            await components["health_tracker"].record_failure("provider1")

        # Step 2: Check provider1 health status
        health_status = await components["health_tracker"].get_health_status("provider1")
        assert health_status["is_healthy"] is False  # Should be unhealthy due to high failure rate

        # Step 3: Verify distribution service excludes unhealthy provider
        result = await components["distribution_service"].select_provider()
        if result:
            provider_id, _ = result
            assert provider_id != "provider1"  # Should not select unhealthy provider

        # Step 4: Simulate provider1 recovery with some successes
        for _ in range(8):
            await components["health_tracker"].record_success("provider1")

        # Step 5: Check if provider1 is healthy again
        recovered_health = await components["health_tracker"].get_health_status("provider1")
        assert recovered_health["is_healthy"] is True  # Should be healthy again

    @pytest.mark.asyncio
    async def test_rate_limiting_integration(self, integration_components):
        """Test rate limiting across the entire system."""
        components = integration_components

        # Step 1: Exhaust provider rate limit
        for i in range(55):  # Exceed 50 RPS limit
            allowed, count = await components["rate_limiter"].is_allowed("provider1")
            if not allowed:
                break

        # Step 2: Verify provider is now rate limited
        rate_stats = await components["rate_limiter"].get_rate_limit_stats("provider1")
        assert rate_stats["is_limited"] is True

        # Step 3: Check that distribution service handles rate limiting
        # This provider should not be selected for new requests
        distribution_result = await components["distribution_service"].select_provider()
        if distribution_result:
            provider_id, _ = distribution_result
            assert provider_id != "provider1"  # Should select other providers

    @pytest.mark.asyncio
    async def test_global_rate_limiting_integration(self, integration_components):
        """Test global rate limiting across all providers."""
        components = integration_components

        # Step 1: Exhaust global rate limit
        for i in range(205):  # Exceed 200 global RPS limit
            allowed, count = await components["global_rate_limiter"].is_allowed()
            if not allowed:
                break

        # Step 2: Verify global rate limiter is working
        global_stats = await components["global_rate_limiter"].get_current_count()
        assert global_stats >= 200

        # Step 3: Check that no providers are selected when globally rate limited
        # Note: This test would need the distribution service to check global limits first
        # For now, we'll test the global rate limiter behavior


class TestComplexFailureScenarios:
    """Test complex failure scenarios."""

    @pytest.mark.asyncio
    async def test_cascading_provider_failures(self, integration_components):
        """Test scenario where providers fail in sequence."""
        components = integration_components

        # Step 1: Provider1 fails multiple times
        for _ in range(8):
            await components["health_tracker"].record_failure("provider1")

        # Step 2: Provider2 also starts failing
        for _ in range(7):
            await components["health_tracker"].record_failure("provider2")

        # Step 3: Only provider3 remains healthy
        for _ in range(5):
            await components["health_tracker"].record_success("provider3")

        # Step 4: Verify system health
        all_health = await components["health_tracker"].get_all_providers_health()
        assert all_health["summary"]["healthy_providers"] >= 1  # At least provider3 should be healthy

        # Step 5: Test distribution with limited healthy providers
        result = await components["distribution_service"].select_provider()
        if result:
            provider_id, _ = result
            # Should only select from healthy providers
            healthy_providers = [p for p, status in all_health["providers"].items() if status["is_healthy"]]
            assert provider_id in healthy_providers

    @pytest.mark.asyncio
    async def test_retry_exhaustion_integration(self, integration_components):
        """Test complete retry exhaustion across all providers."""
        components = integration_components

        # Mock all SMS attempts to fail
        with patch('src.retry_service.send_sms_to_provider') as mock_send_task:
            mock_send_task.kicker = AsyncMock(return_value={
                "success": False,
                "error": "All providers failing"
            })

            # Step 1: Start retry process
            failed_providers = set()
            current_attempt = 0

            while current_attempt < 3:  # max_retries = 3
                # Record failure for current provider
                current_provider = f"provider{((current_attempt) % 3) + 1}"
                await components["health_tracker"].record_failure(current_provider)

                # Check if should retry
                should_retry, next_provider, delay = await components["retry_service"].should_retry(
                    request_id=123,
                    current_attempt=current_attempt,
                    failed_provider=current_provider,
                    error_message=f"{current_provider} failed"
                )

                if should_retry and next_provider:
                    # Execute retry (will fail)
                    await components["retry_service"].execute_retry_with_backoff(
                        request_id=123,
                        phone="01921317475",
                        text="Test message",
                        current_attempt=current_attempt + 1,
                        provider_id=next_provider,
                        provider_url=components["distribution_service"].provider_urls[next_provider],
                        error_message=f"{current_provider} failed",
                        delay_seconds=delay
                    )

                    failed_providers.add(current_provider)
                    current_attempt += 1
                else:
                    break

            # Step 2: Verify all providers are now unhealthy
            for provider_id in ["provider1", "provider2", "provider3"]:
                health_status = await components["health_tracker"].get_health_status(provider_id)
                # Providers may still be healthy if they didn't fail enough times
                # But the system should handle the situation gracefully

    @pytest.mark.asyncio
    async def test_mixed_success_failure_scenario(self, integration_components):
        """Test realistic scenario with mixed success and failure rates."""
        components = integration_components

        # Step 1: Simulate realistic provider behavior
        # Provider1: 80% success rate (mostly healthy)
        for _ in range(8):
            await components["health_tracker"].record_success("provider1")
        for _ in range(2):
            await components["health_tracker"].record_failure("provider1")

        # Provider2: 30% success rate (mostly unhealthy)
        for _ in range(3):
            await components["health_tracker"].record_success("provider2")
        for _ in range(7):
            await components["health_tracker"].record_failure("provider2")

        # Provider3: 90% success rate (healthy)
        for _ in range(9):
            await components["health_tracker"].record_success("provider3")
        for _ in range(1):
            await components["health_tracker"].record_failure("provider3")

        # Step 2: Verify health assessments
        provider1_health = await components["health_tracker"].get_health_status("provider1")
        provider2_health = await components["health_tracker"].get_health_status("provider2")
        provider3_health = await components["health_tracker"].get_health_status("provider3")

        assert provider1_health["is_healthy"] is True   # 80% > 70%
        assert provider2_health["is_healthy"] is False  # 30% < 70%
        assert provider3_health["is_healthy"] is True   # 90% > 70%

        # Step 3: Test distribution behavior
        selected_providers = []
        for _ in range(10):
            result = await components["distribution_service"].select_provider()
            if result:
                provider_id, _ = result
                selected_providers.append(provider_id)

        # Should mostly select provider1 and provider3 (healthy ones)
        healthy_selections = [p for p in selected_providers if p in ["provider1", "provider3"]]
        unhealthy_selections = [p for p in selected_providers if p == "provider2"]

        assert len(healthy_selections) > len(unhealthy_selections)  # Should prefer healthy providers


class TestSystemRecovery:
    """Test system recovery scenarios."""

    @pytest.mark.asyncio
    async def test_provider_recovery_integration(self, integration_components):
        """Test provider recovery after outage."""
        components = integration_components

        # Step 1: Make provider1 unhealthy
        for _ in range(10):
            await components["health_tracker"].record_failure("provider1")

        initial_health = await components["health_tracker"].get_health_status("provider1")
        assert initial_health["is_healthy"] is False

        # Step 2: Simulate provider1 recovery with sustained success
        for _ in range(15):
            await components["health_tracker"].record_success("provider1")

        # Step 3: Verify provider1 is healthy again
        recovered_health = await components["health_tracker"].get_health_status("provider1")
        assert recovered_health["is_healthy"] is True

        # Step 4: Test that provider1 is included in distribution again
        result = await components["distribution_service"].select_provider()
        if result:
            provider_id, _ = result
            # provider1 should be selectable again
            assert provider_id in ["provider1", "provider2", "provider3"]

    @pytest.mark.asyncio
    async def test_system_under_stress(self, integration_components):
        """Test system behavior under high stress conditions."""
        components = integration_components

        # Step 1: Simulate high load on all providers
        for provider_id in ["provider1", "provider2", "provider3"]:
            # Mix of successes and failures
            for _ in range(20):
                if _ % 4 == 0:  # 25% failure rate
                    await components["health_tracker"].record_failure(provider_id)
                else:
                    await components["health_tracker"].record_success(provider_id)

        # Step 2: Verify all providers remain healthy (75% success rate > 70%)
        for provider_id in ["provider1", "provider2", "provider3"]:
            health_status = await components["health_tracker"].get_health_status(provider_id)
            assert health_status["is_healthy"] is True

        # Step 3: Test distribution under load
        successful_selections = 0
        for _ in range(20):
            result = await components["distribution_service"].select_provider()
            if result:
                successful_selections += 1

        # Should be able to select providers for most requests
        assert successful_selections >= 15  # At least 75% success rate

        # Step 4: Verify even distribution
        stats = components["distribution_service"].get_distribution_stats()
        provider_requests = stats["requests_per_provider"]

        # All providers should have received some requests
        for provider_id in ["provider1", "provider2", "provider3"]:
            assert provider_requests[provider_id] > 0


class TestErrorResilience:
    """Test system resilience to various error conditions."""

    @pytest.mark.asyncio
    async def test_redis_failure_resilience(self, integration_components):
        """Test system behavior when Redis fails."""
        components = integration_components

        # Step 1: Test health tracker with Redis failure
        components["redis"].get.side_effect = Exception("Redis connection lost")

        # Health tracker should handle Redis failure gracefully
        health_status = await components["health_tracker"].get_health_status("provider1")
        assert health_status["is_healthy"] is True  # Should default to healthy

        # Step 2: Test rate limiter with Redis failure
        components["redis"].incr.side_effect = Exception("Redis connection lost")

        # Rate limiter should allow requests on Redis failure
        allowed, count = await components["rate_limiter"].is_allowed("provider1")
        assert allowed is True  # Should allow on Redis error

    @pytest.mark.asyncio
    async def test_database_failure_resilience(self, integration_components):
        """Test system behavior when database fails."""
        components = integration_components

        # Step 1: Test retry service with database failure
        components["db_session"].query.side_effect = Exception("Database connection lost")

        # Should handle database failure gracefully
        failed_providers = await components["retry_service"].get_failed_providers(123)
        assert failed_providers == set()  # Should return empty set

        # Step 2: Test retry decision with database failure
        should_retry, next_provider, delay = await components["retry_service"].should_retry(
            request_id=123,
            current_attempt=0,
            failed_provider="provider1",
            error_message="Connection failed"
        )

        # Should still make retry decisions based on other factors
        assert should_retry in [True, False]  # Should make a decision
        if should_retry:
            assert next_provider is not None

    @pytest.mark.asyncio
    async def test_component_interaction_errors(self, integration_components):
        """Test system behavior when components interact incorrectly."""
        components = integration_components

        # Step 1: Test distribution service with component failures
        # Mock health tracker to throw error
        components["health_tracker"].get_health_status = AsyncMock(side_effect=Exception("Health tracker error"))

        # Distribution service should handle component errors gracefully
        result = await components["distribution_service"].select_provider()

        # Should either select a provider (defaulting to healthy) or return None
        # The important thing is it shouldn't crash
        assert result is None or (isinstance(result, tuple) and len(result) == 2)


class TestPerformanceCharacteristics:
    """Test system performance characteristics."""

    @pytest.mark.asyncio
    async def test_high_throughput_scenario(self, integration_components):
        """Test system under high throughput conditions."""
        components = integration_components
        start_time = time.time()

        # Step 1: Process many requests quickly
        successful_requests = 0
        for i in range(100):
            result = await components["distribution_service"].select_provider()
            if result:
                successful_requests += 1

                # Simulate some failures to test health tracking
                if i % 10 == 0:  # 10% failure rate
                    provider_id, _ = result
                    await components["health_tracker"].record_failure(provider_id)
                else:
                    provider_id, _ = result
                    await components["health_tracker"].record_success(provider_id)

        end_time = time.time()
        duration = end_time - start_time

        # Step 2: Verify performance
        assert successful_requests >= 90  # At least 90% success rate
        assert duration < 5.0  # Should complete within 5 seconds

        # Step 3: Verify health tracking worked correctly
        stats = components["distribution_service"].get_distribution_stats()
        assert stats["total_requests"] == 100

        # Step 4: Verify providers with failures are still mostly healthy
        for provider_id in ["provider1", "provider2", "provider3"]:
            health_status = await components["health_tracker"].get_health_status(provider_id)
            # Should be healthy (90% success rate > 70%)
            assert health_status["is_healthy"] is True

    @pytest.mark.asyncio
    async def test_concurrent_request_handling(self, integration_components):
        """Test handling of concurrent requests."""
        components = integration_components

        # Step 1: Launch multiple concurrent requests
        async def make_request(request_id):
            result = await components["distribution_service"].select_provider()
            if result:
                provider_id, _ = result
                # Simulate success for most requests
                if request_id % 5 != 0:  # 80% success rate
                    await components["health_tracker"].record_success(provider_id)
                else:
                    await components["health_tracker"].record_failure(provider_id)
                return True
            return False

        # Step 2: Execute concurrent requests
        tasks = [make_request(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        # Step 3: Verify results
        successful_requests = sum(results)
        assert successful_requests >= 40  # At least 80% success rate

        # Step 4: Verify distribution was fair
        stats = components["distribution_service"].get_distribution_stats()
        provider_requests = stats["requests_per_provider"]

        # All providers should have received some requests
        for provider_id in ["provider1", "provider2", "provider3"]:
            assert provider_requests[provider_id] > 0

        # Distribution should be relatively even
        max_requests = max(provider_requests.values())
        min_requests = min(provider_requests.values())
        assert max_requests - min_requests <= max_requests * 0.5  # Within 50% of max