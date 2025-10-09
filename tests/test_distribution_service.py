"""
Comprehensive tests for SMSDistributionService functionality.

Tests weighted round-robin distribution, health awareness, and rate limiting integration.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis

from src.distribution import SMSDistributionService, ProviderStatus, DistributionStats, create_distribution_service
from src.health_tracker import ProviderHealthTracker
from src.rate_limiter import RateLimiter, GlobalRateLimiter


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock(spec=Redis)
    redis.incr.return_value = 1
    redis.expire.return_value = True
    redis.get.return_value = b"1"
    return redis


@pytest.fixture
def mock_health_tracker():
    """Create mock health tracker."""
    tracker = AsyncMock(spec=ProviderHealthTracker)
    return tracker


@pytest.fixture
def mock_rate_limiter():
    """Create mock rate limiter."""
    limiter = AsyncMock(spec=RateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


@pytest.fixture
def mock_global_rate_limiter():
    """Create mock global rate limiter."""
    limiter = AsyncMock(spec=GlobalRateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


@pytest.fixture
def provider_urls():
    """Sample provider URLs."""
    return {
        "provider1": "http://provider1.com",
        "provider2": "http://provider2.com",
        "provider3": "http://provider3.com"
    }


@pytest.fixture
def distribution_service(mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter, provider_urls):
    """Create SMSDistributionService instance for testing."""
    return SMSDistributionService(
        health_tracker=mock_health_tracker,
        rate_limiter=mock_rate_limiter,
        global_rate_limiter=mock_global_rate_limiter,
        provider_urls=provider_urls
    )


class TestSMSDistributionServiceInitialization:
    """Test cases for SMSDistributionService initialization."""

    def test_init(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter, provider_urls):
        """Test SMSDistributionService initialization."""
        assert distribution_service.health_tracker == mock_health_tracker
        assert distribution_service.rate_limiter == mock_rate_limiter
        assert distribution_service.global_rate_limiter == mock_global_rate_limiter
        assert distribution_service.provider_urls == provider_urls

        # Check provider status initialization
        assert len(distribution_service.provider_status) == 3
        assert "provider1" in distribution_service.provider_status
        assert "provider2" in distribution_service.provider_status
        assert "provider3" in distribution_service.provider_status

        # Check initial stats
        assert distribution_service.distribution_stats.total_requests == 0
        assert distribution_service.distribution_stats.healthy_providers == 0
        assert distribution_service.distribution_stats.unhealthy_providers == 0

        # Check provider status defaults
        for provider_id in provider_urls.keys():
            status = distribution_service.provider_status[provider_id]
            assert status.provider_id == provider_id
            assert status.is_healthy is True  # Default to healthy
            assert status.is_rate_limited is False
            assert status.current_load == 0

    def test_init_empty_providers(self, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test initialization with empty provider URLs."""
        service = SMSDistributionService(
            health_tracker=mock_health_tracker,
            rate_limiter=mock_rate_limiter,
            global_rate_limiter=mock_global_rate_limiter,
            provider_urls={}
        )

        assert len(service.provider_status) == 0
        assert len(service.healthy_providers_queue) == 0


class TestProviderHealthUpdates:
    """Test cases for provider health status updates."""

    @pytest.mark.asyncio
    async def test_update_provider_health_status_all_healthy(self, distribution_service, mock_health_tracker):
        """Test updating health status when all providers are healthy."""
        # Mock health tracker responses
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},
            {"is_healthy": True, "failure_rate": 0.1},
            {"is_healthy": True, "failure_rate": 0.3}
        ]

        await distribution_service._update_provider_health_status()

        # Check that all providers are marked as healthy
        for provider_id in ["provider1", "provider2", "provider3"]:
            assert distribution_service.provider_status[provider_id].is_healthy is True

    @pytest.mark.asyncio
    async def test_update_provider_health_status_mixed_health(self, distribution_service, mock_health_tracker):
        """Test updating health status with mixed provider health."""
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},   # provider1: healthy
            {"is_healthy": False, "failure_rate": 0.8},  # provider2: unhealthy
            {"is_healthy": True, "failure_rate": 0.1}   # provider3: healthy
        ]

        await distribution_service._update_provider_health_status()

        assert distribution_service.provider_status["provider1"].is_healthy is True
        assert distribution_service.provider_status["provider2"].is_healthy is False
        assert distribution_service.provider_status["provider3"].is_healthy is True

    @pytest.mark.asyncio
    async def test_update_provider_health_status_health_tracker_error(self, distribution_service, mock_health_tracker):
        """Test updating health status when health tracker throws error."""
        mock_health_tracker.get_health_status.side_effect = Exception("Health tracker error")

        with pytest.raises(Exception, match="Health tracker error"):
            await distribution_service._update_provider_health_status()

    @pytest.mark.asyncio
    async def test_update_provider_rate_limit_status_all_allowed(self, distribution_service, mock_rate_limiter):
        """Test updating rate limit status when all providers are allowed."""
        mock_rate_limiter.is_allowed.side_effect = [
            (True, 1), (True, 2), (True, 3)
        ]

        await distribution_service._update_provider_rate_limit_status()

        for provider_id in ["provider1", "provider2", "provider3"]:
            status = distribution_service.provider_status[provider_id]
            assert status.is_rate_limited is False
            assert status.current_load > 0

    @pytest.mark.asyncio
    async def test_update_provider_rate_limit_status_some_limited(self, distribution_service, mock_rate_limiter):
        """Test updating rate limit status when some providers are limited."""
        mock_rate_limiter.is_allowed.side_effect = [
            (True, 45),   # provider1: allowed (45 < 50)
            (False, 50),  # provider2: limited (50 >= 50)
            (True, 30)    # provider3: allowed (30 < 50)
        ]

        await distribution_service._update_provider_rate_limit_status()

        assert distribution_service.provider_status["provider1"].is_rate_limited is False
        assert distribution_service.provider_status["provider2"].is_rate_limited is True
        assert distribution_service.provider_status["provider3"].is_rate_limited is False


class TestProviderSelection:
    """Test cases for provider selection logic."""

    @pytest.mark.asyncio
    async def test_get_healthy_providers_all_healthy(self, distribution_service):
        """Test getting healthy providers when all are healthy and not rate limited."""
        # Set up provider statuses
        for provider_id in ["provider1", "provider2", "provider3"]:
            distribution_service.provider_status[provider_id].is_healthy = True
            distribution_service.provider_status[provider_id].is_rate_limited = False

        healthy_providers = distribution_service._get_healthy_providers()

        assert set(healthy_providers) == {"provider1", "provider2", "provider3"}

    @pytest.mark.asyncio
    async def test_get_healthy_providers_mixed_status(self, distribution_service):
        """Test getting healthy providers with mixed health and rate limit status."""
        # Set up mixed provider statuses
        distribution_service.provider_status["provider1"].is_healthy = True
        distribution_service.provider_status["provider1"].is_rate_limited = False

        distribution_service.provider_status["provider2"].is_healthy = True
        distribution_service.provider_status["provider2"].is_rate_limited = True  # Rate limited

        distribution_service.provider_status["provider3"].is_healthy = False  # Unhealthy
        distribution_service.provider_status["provider3"].is_rate_limited = False

        healthy_providers = distribution_service._get_healthy_providers()

        assert healthy_providers == ["provider1"]  # Only provider1 is healthy and not rate limited

    @pytest.mark.asyncio
    async def test_get_healthy_providers_none_available(self, distribution_service):
        """Test getting healthy providers when none are available."""
        # Set up all providers as unhealthy or rate limited
        for provider_id in ["provider1", "provider2", "provider3"]:
            distribution_service.provider_status[provider_id].is_healthy = False
            distribution_service.provider_status[provider_id].is_rate_limited = True

        healthy_providers = distribution_service._get_healthy_providers()

        assert healthy_providers == []

    @pytest.mark.asyncio
    async def test_update_healthy_providers_queue_unchanged(self, distribution_service):
        """Test updating healthy providers queue when providers unchanged."""
        # Set initial healthy providers
        distribution_service.healthy_providers_queue.extend(["provider1", "provider2", "provider3"])
        distribution_service.distribution_stats.round_robin_index = 1

        # Update with same providers
        distribution_service._update_healthy_providers_queue(["provider1", "provider2", "provider3"])

        # Queue should remain unchanged
        assert list(distribution_service.healthy_providers_queue) == ["provider1", "provider2", "provider3"]
        assert distribution_service.distribution_stats.round_robin_index == 1

    @pytest.mark.asyncio
    async def test_update_healthy_providers_queue_changed(self, distribution_service):
        """Test updating healthy providers queue when providers changed."""
        # Set initial queue
        distribution_service.healthy_providers_queue.extend(["provider1", "provider2"])
        distribution_service.distribution_stats.round_robin_index = 1

        # Update with different providers
        distribution_service._update_healthy_providers_queue(["provider2", "provider3"])

        # Queue should be updated and index reset
        assert list(distribution_service.healthy_providers_queue) == ["provider2", "provider3"]
        assert distribution_service.distribution_stats.round_robin_index == 0

    @pytest.mark.asyncio
    async def test_get_next_provider_round_robin(self, distribution_service):
        """Test round-robin provider selection."""
        # Set up queue
        distribution_service.healthy_providers_queue.extend(["provider1", "provider2", "provider3"])
        distribution_service.distribution_stats.round_robin_index = 0

        # First selection
        provider1 = await distribution_service._get_next_provider_round_robin()
        assert provider1 == "provider1"
        assert distribution_service.distribution_stats.round_robin_index == 1

        # Second selection
        provider2 = await distribution_service._get_next_provider_round_robin()
        assert provider2 == "provider2"
        assert distribution_service.distribution_stats.round_robin_index == 2

        # Third selection
        provider3 = await distribution_service._get_next_provider_round_robin()
        assert provider3 == "provider3"
        assert distribution_service.distribution_stats.round_robin_index == 0  # Wrapped around

    @pytest.mark.asyncio
    async def test_get_next_provider_round_robin_empty_queue(self, distribution_service):
        """Test round-robin provider selection with empty queue."""
        distribution_service.healthy_providers_queue.clear()

        provider = await distribution_service._get_next_provider_round_robin()

        assert provider is None


class TestProviderSelectionIntegration:
    """Integration test cases for provider selection."""

    @pytest.mark.asyncio
    async def test_select_provider_all_healthy(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test selecting provider when all providers are healthy."""
        # Mock all providers as healthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},
            {"is_healthy": True, "failure_rate": 0.1},
            {"is_healthy": True, "failure_rate": 0.3}
        ]

        # Mock rate limit checks
        mock_rate_limiter.is_allowed.side_effect = [(True, 1), (True, 2), (True, 3)]
        mock_global_rate_limiter.is_allowed.return_value = (True, 5)

        result = await distribution_service.select_provider()

        assert result is not None
        provider_id, provider_url = result
        assert provider_id in ["provider1", "provider2", "provider3"]
        assert provider_url == distribution_service.provider_urls[provider_id]

        # Check stats were updated
        assert distribution_service.distribution_stats.total_requests == 1
        assert distribution_service.distribution_stats.healthy_providers == 3
        assert distribution_service.distribution_stats.unhealthy_providers == 0

    @pytest.mark.asyncio
    async def test_select_provider_mixed_health(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test selecting provider with mixed provider health."""
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},   # provider1: healthy
            {"is_healthy": False, "failure_rate": 0.8},  # provider2: unhealthy
            {"is_healthy": True, "failure_rate": 0.1}   # provider3: healthy
        ]

        mock_rate_limiter.is_allowed.side_effect = [
            (True, 10),   # provider1: allowed
            (False, 50),  # provider2: rate limited
            (True, 5)     # provider3: allowed
        ]
        mock_global_rate_limiter.is_allowed.return_value = (True, 5)

        result = await distribution_service.select_provider()

        assert result is not None
        provider_id, provider_url = result
        # Should only select from healthy, non-rate-limited providers
        assert provider_id in ["provider1", "provider3"]

        # Check stats
        assert distribution_service.distribution_stats.total_requests == 1
        assert distribution_service.distribution_stats.healthy_providers == 2  # provider1 and provider3
        assert distribution_service.distribution_stats.unhealthy_providers == 1  # provider2

    @pytest.mark.asyncio
    async def test_select_provider_global_rate_limited(self, distribution_service, mock_global_rate_limiter):
        """Test selecting provider when globally rate limited."""
        mock_global_rate_limiter.is_allowed.return_value = (False, 200)  # Global limit exceeded

        result = await distribution_service.select_provider()

        assert result is None  # Should not select any provider when globally rate limited

    @pytest.mark.asyncio
    async def test_select_provider_no_healthy_providers(self, distribution_service, mock_health_tracker):
        """Test selecting provider when no providers are healthy."""
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": False, "failure_rate": 0.8},
            {"is_healthy": False, "failure_rate": 0.9},
            {"is_healthy": False, "failure_rate": 0.7}
        ]

        result = await distribution_service.select_provider()

        assert result is None  # Should not select when no healthy providers

        # Check stats
        assert distribution_service.distribution_stats.total_requests == 1
        assert distribution_service.distribution_stats.healthy_providers == 0
        assert distribution_service.distribution_stats.unhealthy_providers == 3

    @pytest.mark.asyncio
    async def test_select_provider_health_tracker_error(self, distribution_service, mock_health_tracker):
        """Test selecting provider when health tracker throws error."""
        mock_health_tracker.get_health_status.side_effect = Exception("Health tracker error")

        result = await distribution_service.select_provider()

        # Should default to selecting a provider on error (first provider)
        assert result is not None
        provider_id, provider_url = result
        assert provider_id == "provider1"
        assert provider_url == "http://provider1.com"

    @pytest.mark.asyncio
    async def test_select_provider_rate_limiter_error(self, distribution_service, mock_rate_limiter):
        """Test selecting provider when rate limiter throws error."""
        mock_rate_limiter.is_allowed.side_effect = Exception("Rate limiter error")

        result = await distribution_service.select_provider()

        assert result is None  # Should not select on rate limiter error


class TestDistributionStats:
    """Test cases for distribution statistics."""

    def test_get_distribution_stats(self, distribution_service):
        """Test getting distribution statistics."""
        # Set up some test data
        distribution_service.distribution_stats.total_requests = 10
        distribution_service.distribution_stats.healthy_providers = 2
        distribution_service.distribution_stats.unhealthy_providers = 1
        distribution_service.distribution_stats.requests_per_provider = {
            "provider1": 5,
            "provider2": 3,
            "provider3": 2
        }
        distribution_service.distribution_stats.round_robin_index = 1

        distribution_service.provider_usage_count = {"provider1": 5, "provider2": 3, "provider3": 2}
        distribution_service.healthy_providers_queue.extend(["provider1", "provider3"])

        # Set provider statuses
        for provider_id in ["provider1", "provider2", "provider3"]:
            status = distribution_service.provider_status[provider_id]
            status.is_healthy = provider_id != "provider2"
            status.is_rate_limited = provider_id == "provider2"
            status.current_load = 10 if provider_id == "provider1" else 0
            status.last_used = 1000.0

        stats = distribution_service.get_distribution_stats()

        assert stats["total_requests"] == 10
        assert stats["healthy_providers"] == 2
        assert stats["unhealthy_providers"] == 1
        assert stats["requests_per_provider"]["provider1"] == 5
        assert stats["requests_per_provider"]["provider2"] == 3
        assert stats["requests_per_provider"]["provider3"] == 2
        assert stats["round_robin_index"] == 1
        assert stats["healthy_providers_queue"] == ["provider1", "provider3"]
        assert stats["provider_usage_count"]["provider1"] == 5

    @pytest.mark.asyncio
    async def test_reset_stats(self, distribution_service):
        """Test resetting distribution statistics."""
        # Set up some test data
        distribution_service.distribution_stats.total_requests = 10
        distribution_service.distribution_stats.healthy_providers = 2
        distribution_service.distribution_stats.unhealthy_providers = 1
        distribution_service.distribution_stats.requests_per_provider = {
            "provider1": 5, "provider2": 3, "provider3": 2
        }
        distribution_service.provider_usage_count = {"provider1": 5, "provider2": 3}

        await distribution_service.reset_stats()

        # Check that stats were reset
        assert distribution_service.distribution_stats.total_requests == 0
        assert distribution_service.distribution_stats.healthy_providers == 0
        assert distribution_service.distribution_stats.unhealthy_providers == 0
        assert distribution_service.distribution_stats.requests_per_provider == {
            "provider1": 0, "provider2": 0, "provider3": 0
        }
        assert distribution_service.provider_usage_count == {}


class TestDistributionScenarios:
    """Test cases for various distribution scenarios."""

    @pytest.mark.asyncio
    async def test_single_provider_scenario(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test distribution with only one healthy provider."""
        # Only provider1 is healthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},
            {"is_healthy": False, "failure_rate": 0.8},
            {"is_healthy": False, "failure_rate": 0.9},
        ] * 2  # Repeat for multiple selections

        mock_rate_limiter.is_allowed.side_effect = [(True, 10), (False, 50), (False, 50)] * 2
        mock_global_rate_limiter.is_allowed.return_value = (True, 5)

        result = await distribution_service.select_provider()

        assert result is not None
        provider_id, provider_url = result
        assert provider_id == "provider1"  # Should always select the only healthy provider

        # Select again - should still select provider1
        result2 = await distribution_service.select_provider()
        assert result2 is not None
        provider_id2, _ = result2
        assert provider_id2 == "provider1"

    @pytest.mark.asyncio
    async def test_provider_recovery_scenario(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test scenario where an unhealthy provider becomes healthy."""
        # Initial state: provider1 and provider3 healthy, provider2 unhealthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},   # provider1
            {"is_healthy": False, "failure_rate": 0.8},  # provider2
            {"is_healthy": True, "failure_rate": 0.1}   # provider3
        ]

        mock_rate_limiter.is_allowed.side_effect = [(True, 10), (False, 50), (True, 5)]
        mock_global_rate_limiter.is_allowed.return_value = (True, 5)

        # First selection should be from healthy providers (1 or 3)
        result1 = await distribution_service.select_provider()
        assert result1 is not None
        provider_id1, _ = result1
        assert provider_id1 in ["provider1", "provider3"]

        # Provider2 becomes healthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},   # provider1
            {"is_healthy": True, "failure_rate": 0.3},   # provider2 (now healthy)
            {"is_healthy": True, "failure_rate": 0.1}   # provider3
        ]

        mock_rate_limiter.is_allowed.side_effect = [(True, 10), (True, 15), (True, 5)]

        # Next selection should include provider2 in the rotation
        result2 = await distribution_service.select_provider()
        assert result2 is not None
        provider_id2, _ = result2
        assert provider_id2 in ["provider1", "provider2", "provider3"]

    @pytest.mark.asyncio
    async def test_high_load_distribution_scenario(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test distribution under high load conditions."""
        # All providers healthy initially
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},
            {"is_healthy": True, "failure_rate": 0.1},
            {"is_healthy": True, "failure_rate": 0.3}
        ] * 9  # Repeat for multiple selections

        mock_rate_limiter.is_allowed.side_effect = [(True, i) for i in range(1, 4)] * 3
        mock_global_rate_limiter.is_allowed.return_value = (True, 50)

        selected_providers = []

        # Simulate 9 requests (3 rounds of round-robin)
        for i in range(9):
            result = await distribution_service.select_provider()
            assert result is not None
            provider_id, _ = result
            selected_providers.append(provider_id)

        # Should distribute evenly among all 3 providers
        assert selected_providers.count("provider1") == 3
        assert selected_providers.count("provider2") == 3
        assert selected_providers.count("provider3") == 3

        # Check final stats
        assert distribution_service.distribution_stats.total_requests == 9
        assert distribution_service.distribution_stats.requests_per_provider["provider1"] == 3
        assert distribution_service.distribution_stats.requests_per_provider["provider2"] == 3
        assert distribution_service.distribution_stats.requests_per_provider["provider3"] == 3

    @pytest.mark.asyncio
    async def test_partial_outage_scenario(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test distribution during partial provider outage."""
        # Provider2 goes down after some requests
        mock_health_tracker.get_health_status.side_effect = ([
            {"is_healthy": True, "failure_rate": 0.2},   # provider1
            {"is_healthy": True, "failure_rate": 0.1},   # provider2 (healthy initially)
            {"is_healthy": True, "failure_rate": 0.3}   # provider3
        ] * 3) + ([
            {"is_healthy": True, "failure_rate": 0.2},   # provider1
            {"is_healthy": False, "failure_rate": 0.8},  # provider2 (now unhealthy)
            {"is_healthy": True, "failure_rate": 0.3}   # provider3
        ] * 6)

        mock_rate_limiter.is_allowed.side_effect = [(True, 10), (True, 15), (True, 5)]
        mock_global_rate_limiter.is_allowed.return_value = (True, 10)

        # First 3 requests - all providers healthy
        for i in range(3):
            result = await distribution_service.select_provider()
            assert result is not None
            provider_id, _ = result
            assert provider_id in ["provider1", "provider2", "provider3"]

        # Provider2 becomes unhealthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.2},   # provider1
            {"is_healthy": False, "failure_rate": 0.8},  # provider2 (now unhealthy)
            {"is_healthy": True, "failure_rate": 0.3}   # provider3
        ]

        # Next 6 requests - only provider1 and provider3 should be selected
        for i in range(6):
            result = await distribution_service.select_provider()
            assert result is not None
            provider_id, _ = result
            assert provider_id in ["provider1", "provider3"]  # provider2 excluded

        # Final stats should show even distribution between healthy providers
        stats = distribution_service.get_distribution_stats()
        provider1_requests = stats["requests_per_provider"]["provider1"]
        provider3_requests = stats["requests_per_provider"]["provider3"]

        # Should be roughly equal (within 1 request of each other)
        assert abs(provider1_requests - provider3_requests) <= 1



    @pytest.mark.asyncio
    async def test_weighted_round_robin_with_unhealthy_provider(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test weighted round-robin distribution when one provider is unhealthy."""
        # Provider2 is unhealthy
        mock_health_tracker.get_health_status.side_effect = [
            {"is_healthy": True, "failure_rate": 0.1},
            {"is_healthy": False, "failure_rate": 0.9},
            {"is_healthy": True, "failure_rate": 0.2},
        ] * 10  # Repeat for multiple selections

        mock_rate_limiter.is_allowed.return_value = (True, 10)
        mock_global_rate_limiter.is_allowed.return_value = (True, 10)

        selected_providers = []
        for _ in range(10):
            result = await distribution_service.select_provider()
            if result:
                selected_providers.append(result[0])

        # All selections should be from provider1 and provider3
        assert "provider2" not in selected_providers
        assert selected_providers.count("provider1") == 5
        assert selected_providers.count("provider3") == 5

    @pytest.mark.asyncio
    async def test_all_providers_unhealthy_scenario(self, distribution_service, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter):
        """Test that no provider is selected when all are unhealthy."""
        mock_health_tracker.get_health_status.return_value = {"is_healthy": False, "failure_rate": 0.9}
        mock_rate_limiter.is_allowed.return_value = (True, 10)
        mock_global_rate_limiter.is_allowed.return_value = (True, 10)

        result = await distribution_service.select_provider()

        assert result is None

class TestDistributionServiceFactory:
    """Test cases for factory functions."""

    @pytest.mark.asyncio
    async def test_create_distribution_service(self, mock_health_tracker, mock_rate_limiter, mock_global_rate_limiter, provider_urls):
        """Test create_distribution_service factory function."""
        service = await create_distribution_service(
            health_tracker=mock_health_tracker,
            rate_limiter=mock_rate_limiter,
            global_rate_limiter=mock_global_rate_limiter,
            provider_urls=provider_urls
        )

        assert isinstance(service, SMSDistributionService)
        assert service.health_tracker == mock_health_tracker
        assert service.rate_limiter == mock_rate_limiter
        assert service.global_rate_limiter == mock_global_rate_limiter
        assert service.provider_urls == provider_urls


class TestErrorHandling:
    """Test cases for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_select_provider_unexpected_error(self, distribution_service, mock_health_tracker):
        """Test selecting provider with unexpected error."""
        mock_health_tracker.get_health_status.side_effect = Exception("Unexpected error")

        result = await distribution_service.select_provider()

        assert result is None  # Should handle error gracefully