"""
Provider health tracking and failure detection service.

This module implements a health monitoring system that tracks provider success/failure rates
over 5-minute sliding windows and marks providers as unhealthy when failure rate exceeds 70%.
"""

import time
import logging
from typing import Dict, Any, Optional, Tuple

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

# Configure logging
logger = logging.getLogger(__name__)


class ProviderHealthTracker:
    """
    Redis-based health tracking for SMS providers with sliding window calculations.

    Tracks success and failure counts over 5-minute windows and marks providers as unhealthy
    when failure rate exceeds 70%. Uses Redis keys that expire automatically after 5 minutes.
    """

    def __init__(
        self,
        redis_client: Redis,
        window_duration: int = 300,  # 5 minutes
        failure_threshold: float = 0.7  # 70% failure rate
    ):
        """
        Initialize health tracker.

        Args:
            redis_client: Redis client instance
            window_duration: Time window in seconds (default: 300 = 5 minutes)
            failure_threshold: Failure rate threshold for marking unhealthy (default: 0.7 = 70%)
        """
        self.redis = redis_client
        self.window_duration = window_duration
        self.failure_threshold = failure_threshold

    def _get_window_key(self, provider_id: str, metric_type: str) -> str:
        """
        Generate Redis key for provider health metrics.

        Args:
            provider_id: Provider identifier (provider1, provider2, provider3)
            metric_type: Either 'success' or 'failure'

        Returns:
            Redis key for the metric
        """
        current_window = int(time.time() // self.window_duration) * self.window_duration
        return f"health:{provider_id}:{metric_type}:{current_window}"

    def _get_current_window_keys(self, provider_id: str) -> Tuple[str, str, str, str]:
        """
        Get current and previous window keys for sliding window calculation.

        Args:
            provider_id: Provider identifier

        Returns:
            Tuple of (current_success_key, current_failure_key, prev_success_key, prev_failure_key)
        """
        now = time.time()
        current_window = int(now // self.window_duration) * self.window_duration
        previous_window = current_window - self.window_duration

        current_success_key = f"health:{provider_id}:success:{current_window}"
        current_failure_key = f"health:{provider_id}:failure:{current_window}"
        prev_success_key = f"health:{provider_id}:success:{previous_window}"
        prev_failure_key = f"health:{provider_id}:failure:{previous_window}"

        return current_success_key, current_failure_key, prev_success_key, prev_failure_key

    def _calculate_sliding_window_metrics(
        self,
        current_success: int,
        current_failure: int,
        prev_success: int,
        prev_failure: int
    ) -> Tuple[int, int, float]:
        """
        Calculate sliding window metrics with time weighting.

        Args:
            current_success: Success count in current window
            current_failure: Failure count in current window
            prev_success: Success count in previous window
            prev_failure: Failure count in previous window

        Returns:
            Tuple of (total_success, total_failure, failure_rate)
        """
        now = time.time()
        current_window_start = int(now // self.window_duration) * self.window_duration
        fraction_into_window = (now - current_window_start) / self.window_duration

        # Weight from previous window (how much is still valid)
        previous_weight = 1.0 - fraction_into_window
        weighted_prev_success = int(prev_success * previous_weight)
        weighted_prev_failure = int(prev_failure * previous_weight)

        total_success = current_success + weighted_prev_success
        total_failure = current_failure + weighted_prev_failure
        total_requests = total_success + total_failure

        if total_requests == 0:
            failure_rate = 0.0
        else:
            failure_rate = total_failure / total_requests

        return total_success, total_failure, failure_rate

    async def record_success(self, provider_id: str) -> bool:
        """
        Record a successful SMS send for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if recorded successfully
        """
        try:
            current_success_key, _, _, _ = self._get_current_window_keys(provider_id)

            # Atomically increment success counter
            await self.redis.incr(current_success_key)

            # Set expiry for the key (5 minutes from now)
            await self.redis.expire(current_success_key, self.window_duration)

            logger.debug(f"Recorded success for provider {provider_id}")
            return True

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Redis connection error recording success for {provider_id}: {str(e)}")
            return False
        except RedisError as e:
            logger.error(f"Redis error recording success for {provider_id}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error recording success for {provider_id}: {str(e)}")
            return False

    async def record_failure(self, provider_id: str) -> bool:
        """
        Record a failed SMS send for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if recorded successfully
        """
        try:
            _, current_failure_key, _, _ = self._get_current_window_keys(provider_id)

            # Atomically increment failure counter
            await self.redis.incr(current_failure_key)

            # Set expiry for the key (5 minutes from now)
            await self.redis.expire(current_failure_key, self.window_duration)

            logger.debug(f"Recorded failure for provider {provider_id}")
            return True

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Redis connection error recording failure for {provider_id}: {str(e)}")
            return False
        except RedisError as e:
            logger.error(f"Redis error recording failure for {provider_id}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error recording failure for {provider_id}: {str(e)}")
            return False

    async def get_health_status(self, provider_id: str) -> Dict[str, Any]:
        """
        Get comprehensive health status for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with health metrics and status
        """
        try:
            current_success_key, current_failure_key, prev_success_key, prev_failure_key = self._get_current_window_keys(provider_id)

            # Get current window counts
            current_success_data = await self.redis.get(current_success_key)
            current_failure_data = await self.redis.get(current_failure_key)
            current_success = int(current_success_data) if current_success_data else 0
            current_failure = int(current_failure_data) if current_failure_data else 0

            # Get previous window counts
            prev_success_data = await self.redis.get(prev_success_key)
            prev_failure_data = await self.redis.get(prev_failure_key)
            prev_success = int(prev_success_data) if prev_success_data else 0
            prev_failure = int(prev_failure_data) if prev_failure_data else 0

            # Calculate sliding window metrics
            total_success, total_failure, failure_rate = self._calculate_sliding_window_metrics(
                current_success, current_failure, prev_success, prev_failure
            )

            total_requests = total_success + total_failure
            is_healthy = failure_rate <= self.failure_threshold if total_requests > 0 else True

            # Calculate window expiry time
            current_window = int(time.time() // self.window_duration) * self.window_duration
            window_expires_at = current_window + self.window_duration

            return {
                "provider_id": provider_id,
                "is_healthy": is_healthy,
                "total_requests": total_requests,
                "success_count": total_success,
                "failure_count": total_failure,
                "failure_rate": round(failure_rate, 3),
                "current_window": {
                    "success": current_success,
                    "failure": current_failure,
                    "expires_at": window_expires_at
                },
                "previous_window": {
                    "success": prev_success,
                    "failure": prev_failure
                },
                "threshold": self.failure_threshold,
                "window_duration_seconds": self.window_duration,
                "timestamp": time.time()
            }

        except Exception as e:
            logger.error(f"Error getting health status for {provider_id}: {str(e)}")
            return {
                "provider_id": provider_id,
                "error": str(e),
                "is_healthy": True,  # Default to healthy on error
                "total_requests": 0,
                "success_count": 0,
                "failure_count": 0,
                "failure_rate": 0.0,
                "timestamp": time.time()
            }

    async def is_provider_healthy(self, provider_id: str) -> bool:
        """
        Check if a provider is currently healthy (can be used for quick health checks).

        Args:
            provider_id: Provider identifier

        Returns:
            True if provider is healthy, False if unhealthy or error
        """
        try:
            status = await self.get_health_status(provider_id)
            return status.get("is_healthy", True)
        except Exception as e:
            logger.error(f"Error checking health for {provider_id}: {str(e)}")
            # Default to healthy on error to avoid blocking all requests
            return True

    async def get_all_providers_health(self) -> Dict[str, Any]:
        """
        Get health status for all providers.

        Returns:
            Dictionary with health status for all providers
        """
        providers = ["provider1", "provider2", "provider3"]
        all_health = {}

        for provider_id in providers:
            all_health[provider_id] = await self.get_health_status(provider_id)

        # Calculate overall system health
        healthy_providers = sum(1 for status in all_health.values() if status.get("is_healthy", True))
        total_providers = len(providers)

        return {
            "providers": all_health,
            "summary": {
                "total_providers": total_providers,
                "healthy_providers": healthy_providers,
                "unhealthy_providers": total_providers - healthy_providers,
                "system_healthy": healthy_providers > 0  # System is healthy if at least one provider is healthy
            },
            "configuration": {
                "window_duration_seconds": self.window_duration,
                "failure_threshold": self.failure_threshold
            },
            "timestamp": time.time()
        }

    async def reset_provider_health(self, provider_id: str) -> bool:
        """
        Reset health metrics for a provider (for testing or manual intervention).

        Args:
            provider_id: Provider identifier

        Returns:
            True if reset successful
        """
        try:
            # Get current and previous window keys
            current_success_key, current_failure_key, prev_success_key, prev_failure_key = self._get_current_window_keys(provider_id)

            # Delete all health keys for this provider
            keys_to_delete = [current_success_key, current_failure_key, prev_success_key, prev_failure_key]
            await self.redis.delete(*keys_to_delete)

            logger.info(f"Reset health metrics for provider {provider_id}")
            return True

        except Exception as e:
            logger.error(f"Error resetting health for {provider_id}: {str(e)}")
            return False


async def create_health_tracker(redis_client: Redis) -> ProviderHealthTracker:
    """
    Factory function to create a health tracker instance.

    Args:
        redis_client: Redis client instance

    Returns:
        Configured ProviderHealthTracker instance
    """
    from .config import settings

    return ProviderHealthTracker(
        redis_client=redis_client,
        window_duration=300,  # 5 minutes
        failure_threshold=0.7  # 70%
    )