"""
Comprehensive tests for ProviderHealthTracker functionality.

Tests sliding window calculations, failure rate tracking, and health status determination.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

from src.health_tracker import ProviderHealthTracker, create_health_tracker


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock(spec=Redis)
    redis.incr.return_value = 1
    redis.expire.return_value = True
    redis.get.return_value = b"1"
    redis.delete.return_value = True
    return redis


@pytest.fixture
def health_tracker(mock_redis):
    """Create ProviderHealthTracker instance for testing."""
    return ProviderHealthTracker(
        redis_client=mock_redis,
        window_duration=300,  # 5 minutes
        failure_threshold=0.7  # 70%
    )


class TestProviderHealthTracker:
    """Test cases for ProviderHealthTracker class."""

    def test_init(self, health_tracker, mock_redis):
        """Test ProviderHealthTracker initialization."""
        assert health_tracker.redis == mock_redis
        assert health_tracker.window_duration == 300
        assert health_tracker.failure_threshold == 0.7

    def test_get_window_key_format(self, health_tracker):
        """Test Redis key generation for health metrics."""
        with patch('src.health_tracker.time.time', return_value=1000.0):
            success_key = health_tracker._get_window_key("provider1", "success")
            failure_key = health_tracker._get_window_key("provider1", "failure")

            expected_window = int(1000.0 // 300) * 300  # 900
            assert success_key == f"health:provider1:success:{expected_window}"
            assert failure_key == f"health:provider1:failure:{expected_window}"

    def test_get_current_window_keys(self, health_tracker):
        """Test getting current and previous window keys."""
        with patch('src.health_tracker.time.time', return_value=1000.0):
            current_success, current_failure, prev_success, prev_failure = health_tracker._get_current_window_keys("provider1")

            current_window = 900  # int(1000 // 300) * 300
            prev_window = current_window - 300

            assert current_success == f"health:provider1:success:{current_window}"
            assert current_failure == f"health:provider1:failure:{current_window}"
            assert prev_success == f"health:provider1:success:{prev_window}"
            assert prev_failure == f"health:provider1:failure:{prev_window}"

    def test_calculate_sliding_window_metrics_all_current_window(self, health_tracker):
        """Test sliding window calculation with all metrics in current window."""
        total_success, total_failure, failure_rate = health_tracker._calculate_sliding_window_metrics(
            current_success=10, current_failure=5, prev_success=0, prev_failure=0
        )

        assert total_success == 10
        assert total_failure == 5
        assert failure_rate == 5 / 15  # 0.333

    def test_calculate_sliding_window_metrics_with_previous_window(self, health_tracker):
        """Test sliding window calculation with metrics in both windows."""
        # Simulate being 50% through the current window (150 seconds into 300-second window)
        with patch('src.health_tracker.time.time', return_value=1050.0):
            total_success, total_failure, failure_rate = health_tracker._calculate_sliding_window_metrics(
                current_success=8, current_failure=2, prev_success=10, prev_failure=10
            )

            # Should weight previous window at 50% (1.0 - 0.5)
            expected_prev_success = 10 * 0.5  # 5
            expected_prev_failure = 10 * 0.5  # 5

            assert total_success == 8 + expected_prev_success
            assert total_failure == 2 + expected_prev_failure
            assert failure_rate == (7 / 17)  # (2+5) / (8+2+5+5)

    def test_calculate_sliding_window_metrics_no_requests(self, health_tracker):
        """Test sliding window calculation with no requests."""
        total_success, total_failure, failure_rate = health_tracker._calculate_sliding_window_metrics(
            current_success=0, current_failure=0, prev_success=0, prev_failure=0
        )

        assert total_success == 0
        assert total_failure == 0
        assert failure_rate == 0.0

    def test_calculate_sliding_window_metrics_high_failure_rate(self, health_tracker):
        """Test sliding window calculation with high failure rate."""
        total_success, total_failure, failure_rate = health_tracker._calculate_sliding_window_metrics(
            current_success=2, current_failure=8, prev_success=1, prev_failure=9
        )

        assert total_success == 3
        assert total_failure == 17
        assert failure_rate == 17 / 20  # 0.85

    @pytest.mark.asyncio
    async def test_record_success(self, health_tracker, mock_redis):
        """Test recording a successful SMS send."""
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True

        with patch('src.health_tracker.time.time', return_value=1000.0):
            result = await health_tracker.record_success("provider1")

            assert result is True
            # Verify the correct Redis key and window was used
            current_window = int(1000.0 // 300) * 300  # 900
            expected_key = f"health:provider1:success:{current_window}"
            mock_redis.incr.assert_called_once_with(expected_key)
            mock_redis.expire.assert_called_once_with(expected_key, 300)

    @pytest.mark.asyncio
    async def test_record_failure(self, health_tracker, mock_redis):
        """Test recording a failed SMS send."""
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True

        with patch('src.health_tracker.time.time', return_value=1000.0):
            result = await health_tracker.record_failure("provider1")

            assert result is True
            # Verify the correct Redis key and window was used
            current_window = int(1000.0 // 300) * 300  # 900
            expected_key = f"health:provider1:failure:{current_window}"
            mock_redis.incr.assert_called_once_with(expected_key)
            mock_redis.expire.assert_called_once_with(expected_key, 300)

    @pytest.mark.asyncio
    async def test_record_success_redis_connection_error(self, health_tracker, mock_redis):
        """Test recording success with Redis connection error."""
        mock_redis.incr.side_effect = ConnectionError("Connection failed")

        result = await health_tracker.record_success("provider1")

        assert result is False
        mock_redis.incr.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_failure_redis_timeout_error(self, health_tracker, mock_redis):
        """Test recording failure with Redis timeout error."""
        mock_redis.incr.side_effect = TimeoutError("Timeout")

        result = await health_tracker.record_failure("provider1")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_health_status_healthy_provider(self, health_tracker, mock_redis):
        """Test getting health status for a healthy provider."""
        current_time = 1000.0
        current_window = int(current_time // 300) * 300  # 900
        prev_window = current_window - 300  # 600

        # Mock Redis responses for current window (8 success, 2 failures = 20% failure rate)
        mock_redis.get.side_effect = [
            b"8",  # current success
            b"2",  # current failure
            b"5",  # previous success
            b"1",  # previous failure
        ]

        with patch('src.health_tracker.time.time', return_value=current_time):
            status = await health_tracker.get_health_status("provider1")

            # Verify Redis calls
            expected_calls = [
                f"health:provider1:success:{current_window}",
                f"health:provider1:failure:{current_window}",
                f"health:provider1:success:{prev_window}",
                f"health:provider1:failure:{prev_window}",
            ]
            for call in mock_redis.get.call_args_list:
                assert call[0][0] in expected_calls

            # Verify status calculation
            assert status["provider_id"] == "provider1"
            assert status["is_healthy"] is True  # 20% < 70% threshold
            assert status["total_requests"] == 17  # 8+2+5+1 with weighting
            assert status["success_count"] == 13  # 8+5
            assert status["failure_count"] == 4   # 2+1 with weighting
            assert abs(status["failure_rate"] - 0.235) < 0.01  # ~23.5%

    @pytest.mark.asyncio
    async def test_get_health_status_unhealthy_provider(self, health_tracker, mock_redis):
        """Test getting health status for an unhealthy provider."""
        current_time = 1000.0
        current_window = int(current_time // 300) * 300  # 900
        prev_window = current_window - 300  # 600

        # Mock Redis responses for current window (2 success, 8 failures = 80% failure rate)
        mock_redis.get.side_effect = [
            b"2",  # current success
            b"8",  # current failure
            b"1",  # previous success
            b"4",  # previous failure
        ]

        with patch('src.health_tracker.time.time', return_value=current_time):
            status = await health_tracker.get_health_status("provider1")

            assert status["provider_id"] == "provider1"
            assert status["is_healthy"] is False  # 80% > 70% threshold
            assert status["total_requests"] == 16  # 2+8+1+4 with weighting
            assert status["success_count"] == 4    # 2+1 with weighting
            assert status["failure_count"] == 12   # 8+4
            assert abs(status["failure_rate"] - 0.75) < 0.01  # 75%

    @pytest.mark.asyncio
    async def test_get_health_status_no_requests(self, health_tracker, mock_redis):
        """Test getting health status when no requests have been made."""
        # Mock Redis responses - no data
        mock_redis.get.return_value = None

        with patch('src.health_tracker.time.time', return_value=1000.0):
            status = await health_tracker.get_health_status("provider1")

            assert status["provider_id"] == "provider1"
            assert status["is_healthy"] is True  # Default to healthy when no requests
            assert status["total_requests"] == 0
            assert status["success_count"] == 0
            assert status["failure_count"] == 0
            assert status["failure_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_get_health_status_redis_error(self, health_tracker, mock_redis):
        """Test getting health status with Redis error."""
        mock_redis.get.side_effect = RedisError("Redis error")

        status = await health_tracker.get_health_status("provider1")

        assert status["provider_id"] == "provider1"
        assert status["error"] == "Redis error"
        assert status["is_healthy"] is True  # Default to healthy on error
        assert status["total_requests"] == 0

    @pytest.mark.asyncio
    async def test_is_provider_healthy_healthy_provider(self, health_tracker, mock_redis):
        """Test checking if healthy provider is healthy."""
        # Mock healthy status response
        healthy_status = {
            "provider_id": "provider1",
            "is_healthy": True,
            "total_requests": 10,
            "failure_rate": 0.2
        }
        health_tracker.get_health_status = AsyncMock(return_value=healthy_status)

        result = await health_tracker.is_provider_healthy("provider1")

        assert result is True

    @pytest.mark.asyncio
    async def test_is_provider_healthy_unhealthy_provider(self, health_tracker, mock_redis):
        """Test checking if unhealthy provider is unhealthy."""
        # Mock unhealthy status response
        unhealthy_status = {
            "provider_id": "provider1",
            "is_healthy": False,
            "total_requests": 10,
            "failure_rate": 0.8
        }
        health_tracker.get_health_status = AsyncMock(return_value=unhealthy_status)

        result = await health_tracker.is_provider_healthy("provider1")

        assert result is False

    @pytest.mark.asyncio
    async def test_is_provider_healthy_error_fallback(self, health_tracker, mock_redis):
        """Test that health check defaults to healthy on error."""
        health_tracker.get_health_status = AsyncMock(side_effect=Exception("Unexpected error"))

        result = await health_tracker.is_provider_healthy("provider1")

        assert result is True  # Should default to healthy on error

    @pytest.mark.asyncio
    async def test_get_all_providers_health(self, health_tracker, mock_redis):
        """Test getting health status for all providers."""
        # Mock responses for all three providers
        def mock_get_health_status(provider_id):
            return {
                "provider_id": provider_id,
                "is_healthy": provider_id == "provider1",  # Only provider1 is healthy
                "total_requests": 10,
                "failure_rate": 0.2 if provider_id == "provider1" else 0.8
            }

        health_tracker.get_health_status = AsyncMock(side_effect=mock_get_health_status)

        result = await health_tracker.get_all_providers_health()

        # Verify all providers are included
        assert "provider1" in result["providers"]
        assert "provider2" in result["providers"]
        assert "provider3" in result["providers"]

        # Verify summary calculation
        assert result["summary"]["total_providers"] == 3
        assert result["summary"]["healthy_providers"] == 1
        assert result["summary"]["unhealthy_providers"] == 2
        assert result["summary"]["system_healthy"] is True  # At least one healthy

    @pytest.mark.asyncio
    async def test_get_all_providers_health_no_healthy_providers(self, health_tracker, mock_redis):
        """Test getting health status when no providers are healthy."""
        def mock_get_health_status(provider_id):
            return {
                "provider_id": provider_id,
                "is_healthy": False,
                "total_requests": 10,
                "failure_rate": 0.8
            }

        health_tracker.get_health_status = AsyncMock(side_effect=mock_get_health_status)

        result = await health_tracker.get_all_providers_health()

        assert result["summary"]["total_providers"] == 3
        assert result["summary"]["healthy_providers"] == 0
        assert result["summary"]["unhealthy_providers"] == 3
        assert result["summary"]["system_healthy"] is False  # No healthy providers

    @pytest.mark.asyncio
    async def test_reset_provider_health(self, health_tracker, mock_redis):
        """Test resetting health metrics for a provider."""
        mock_redis.delete.return_value = True

        with patch('src.health_tracker.time.time', return_value=1000.0):
            result = await health_tracker.reset_provider_health("provider1")

            assert result is True

            # Should delete 4 keys (current and previous, success and failure)
            assert mock_redis.delete.call_count == 1
            call_args = mock_redis.delete.call_args[0]
            assert len(call_args) == 4  # Should delete 4 keys

            # Verify the keys being deleted
            current_window = int(1000.0 // 300) * 300  # 900
            prev_window = current_window - 300  # 600

            expected_keys = [
                f"health:provider1:success:{current_window}",
                f"health:provider1:failure:{current_window}",
                f"health:provider1:success:{prev_window}",
                f"health:provider1:failure:{prev_window}",
            ]

            for expected_key in expected_keys:
                assert expected_key in call_args

    @pytest.mark.asyncio
    async def test_reset_provider_health_error(self, health_tracker, mock_redis):
        """Test resetting health metrics with Redis error."""
        mock_redis.delete.side_effect = Exception("Redis error")

        result = await health_tracker.reset_provider_health("provider1")

        assert result is False


class TestHealthTrackerFactory:
    """Test cases for factory functions."""

    @pytest.mark.asyncio
    async def test_create_health_tracker(self, mock_redis):
        """Test create_health_tracker factory function."""
        tracker = await create_health_tracker(mock_redis)

        assert isinstance(tracker, ProviderHealthTracker)
        assert tracker.window_duration == 300  # 5 minutes
        assert tracker.failure_threshold == 0.7  # 70%


class TestHealthTrackerIntegration:
    """Integration tests with time-based scenarios."""

    @pytest.mark.asyncio
    async def test_sliding_window_time_progression(self, health_tracker, mock_redis):
        """Test sliding window behavior as time progresses."""
        # Start at beginning of window (1000.0)
        start_time = 1000.0
        window_duration = 300

        # Mock initial state: 10 successes, 0 failures in current window
        def mock_redis_get(key):
            if "success" in key and str(int(start_time // window_duration) * window_duration) in key:
                return b"10"
            return b"0"

        mock_redis.get.side_effect = mock_redis_get

        with patch('src.health_tracker.time.time', return_value=start_time):
            status = await health_tracker.get_health_status("provider1")
            assert status["is_healthy"] is True
            assert status["total_requests"] == 10

        # Move to middle of window (1000.0 + 150 = 1150.0)
        middle_time = start_time + 150

        # Previous window should be weighted at 50%
        def mock_redis_get_middle(key):
            current_window = int(middle_time // window_duration) * window_duration
            prev_window = current_window - window_duration

            if "success" in key:
                if str(current_window) in key:
                    return b"8"  # Current window successes
                elif str(prev_window) in key:
                    return b"10"  # Previous window successes
            return b"0"

        mock_redis.get.side_effect = mock_redis_get_middle

        with patch('src.health_tracker.time.time', return_value=middle_time):
            status = await health_tracker.get_health_status("provider1")

            # Should have 8 + (10 * 0.5) = 13 total successes
            assert status["total_requests"] == 13

        # Move to next window (1000.0 + 300 = 1300.0)
        next_window_time = start_time + window_duration

        def mock_redis_get_next(key):
            current_window = int(next_window_time // window_duration) * window_duration
            if str(current_window) in key:
                return b"5" if "success" in key else b"0"
            return b"0"  # Previous window data expired

        mock_redis.get.side_effect = mock_redis_get_next

        with patch('src.health_tracker.time.time', return_value=next_window_time):
            status = await health_tracker.get_health_status("provider1")

            # Should only have current window data (5 requests)
            assert status["total_requests"] == 5

    @pytest.mark.asyncio
    async def test_failure_threshold_boundary_conditions(self, health_tracker, mock_redis):
        """Test behavior around the 70% failure threshold."""
        current_time = 1000.0

        # Test exactly at threshold (70% failure rate)
        def mock_redis_at_threshold(key):
            return b"3" if "success" in key else b"7"  # 7/10 = 0.7

        mock_redis.get.side_effect = mock_redis_at_threshold

        with patch('src.health_tracker.time.time', return_value=current_time):
            status = await health_tracker.get_health_status("provider1")
            assert status["is_healthy"] is False  # Should be unhealthy at exactly 70%
            assert abs(status["failure_rate"] - 0.7) < 0.01

        # Test just below threshold (69% failure rate)
        def mock_redis_below_threshold(key):
            return b"31" if "success" in key else b"69"  # 69/100 = 0.69

        mock_redis.get.side_effect = mock_redis_below_threshold

        with patch('src.health_tracker.time.time', return_value=current_time):
            status = await health_tracker.get_health_status("provider1")
            assert status["is_healthy"] is True  # Should be healthy below 70%
            assert abs(status["failure_rate"] - 0.69) < 0.01

        # Test just above threshold (71% failure rate)
        def mock_redis_above_threshold(key):
            return b"29" if "success" in key else b"71"  # 71/100 = 0.71

        mock_redis.get.side_effect = mock_redis_above_threshold

        with patch('src.health_tracker.time.time', return_value=current_time):
            status = await health_tracker.get_health_status("provider1")
            assert status["is_healthy"] is False  # Should be unhealthy above 70%
            assert abs(status["failure_rate"] - 0.71) < 0.01