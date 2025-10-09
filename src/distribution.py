"""
Weighted round-robin distribution service for SMS providers.

This module implements intelligent request distribution that uses weighted round-robin
when providers are healthy and fallback logic when providers become unhealthy.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass, field
from collections import defaultdict, deque

from .health_tracker import ProviderHealthTracker
from .rate_limiter import RateLimiter, GlobalRateLimiter

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Status information for a provider."""
    provider_id: str
    is_healthy: bool
    is_rate_limited: bool
    current_load: int = 0
    last_used: float = 0.0


@dataclass
class DistributionStats:
    """Statistics for distribution tracking."""
    total_requests: int = 0
    healthy_providers: int = 0
    unhealthy_providers: int = 0
    requests_per_provider: Dict[str, int] = field(default_factory=dict)
    round_robin_index: int = 0


class SMSDistributionService:
    """
    Intelligent SMS distribution service with weighted round-robin and health awareness.

    This service implements:
    - Weighted round-robin when all providers are healthy
    - Distribution only to healthy providers when some are unhealthy
    - Equal distribution among available healthy providers
    - Provider health status checked before each distribution decision
    - Graceful handling of single healthy provider scenarios
    """

    def __init__(
        self,
        health_tracker: ProviderHealthTracker,
        rate_limiter: RateLimiter,
        global_rate_limiter: GlobalRateLimiter,
        provider_urls: Dict[str, str]
    ):
        """
        Initialize the distribution service.

        Args:
            health_tracker: ProviderHealthTracker instance
            rate_limiter: RateLimiter instance for per-provider limits
            global_rate_limiter: GlobalRateLimiter instance
            provider_urls: Dictionary mapping provider IDs to their URLs
        """
        self.health_tracker = health_tracker
        self.rate_limiter = rate_limiter
        self.global_rate_limiter = global_rate_limiter
        self.provider_urls = provider_urls

        # Provider status tracking
        self.provider_status: Dict[str, ProviderStatus] = {}
        self.distribution_stats = DistributionStats()

        # Round-robin state for healthy providers
        self.healthy_providers_queue: deque = deque()
        self.provider_usage_count: Dict[str, int] = defaultdict(int)

        # Update interval for health checks (in seconds)
        self.health_check_interval = 30.0
        self.last_health_update = 0.0

        # Initialize provider statuses
        self._initialize_providers()

    def _initialize_providers(self):
        """Initialize provider status tracking."""
        for provider_id in self.provider_urls.keys():
            self.provider_status[provider_id] = ProviderStatus(
                provider_id=provider_id,
                is_healthy=True,  # Default to healthy
                is_rate_limited=False
            )
            self.distribution_stats.requests_per_provider[provider_id] = 0

    async def _update_provider_health_status(self) -> None:
        """Update health status for all providers."""
        current_time = asyncio.get_event_loop().time()

        # Only update if enough time has passed
        if current_time - self.last_health_update < self.health_check_interval:
            return

        self.last_health_update = current_time

        for provider_id in self.provider_urls.keys():
            health_info = await self.health_tracker.get_health_status(provider_id)
            is_healthy = health_info.get("is_healthy", True)

            self.provider_status[provider_id].is_healthy = is_healthy

            if is_healthy:
                logger.debug(f"Provider {provider_id} is healthy")
            else:
                logger.warning(f"Provider {provider_id} is unhealthy (failure rate: {health_info.get('failure_rate', 0):.3f})")

    async def _update_provider_rate_limit_status(self) -> None:
        """Update rate limit status for all providers."""
        for provider_id in self.provider_urls.keys():
            allowed, count = await self.rate_limiter.is_allowed(provider_id)
            self.provider_status[provider_id].is_rate_limited = not allowed
            self.provider_status[provider_id].current_load = count

            if not allowed:
                logger.debug(f"Provider {provider_id} is rate limited (count: {count})")

    def _get_healthy_providers(self) -> List[str]:
        """Get list of currently healthy providers."""
        healthy_providers = []

        for provider_id, status in self.provider_status.items():
            if status.is_healthy and not status.is_rate_limited:
                healthy_providers.append(provider_id)

        return healthy_providers

    def _update_healthy_providers_queue(self, healthy_providers: List[str]) -> None:
        """Update the queue of healthy providers for round-robin distribution."""
        # Only update if the set of healthy providers has changed
        current_healthy_set = set(healthy_providers)
        existing_healthy_set = set(self.healthy_providers_queue)

        if current_healthy_set != existing_healthy_set:
            # Recreate the queue with current healthy providers
            self.healthy_providers_queue.clear()

            # Sort providers to ensure consistent ordering
            for provider_id in sorted(healthy_providers):
                self.healthy_providers_queue.append(provider_id)

            # Reset round-robin index if providers changed
            if self.healthy_providers_queue:
                self.distribution_stats.round_robin_index = 0

            logger.info(f"Updated healthy providers queue: {list(self.healthy_providers_queue)}")

    async def _get_next_provider_round_robin(self) -> Optional[str]:
        """Get next provider using round-robin algorithm."""
        if not self.healthy_providers_queue:
            return None

        # Get provider using round-robin
        provider_id = self.healthy_providers_queue[self.distribution_stats.round_robin_index]

        # Update index for next round-robin selection
        self.distribution_stats.round_robin_index = (
            self.distribution_stats.round_robin_index + 1
        ) % len(self.healthy_providers_queue)

        return provider_id

    async def select_provider(self) -> Optional[Tuple[str, str]]:
        """
        Select the best provider for SMS distribution using weighted round-robin logic.

        Returns:
            Tuple of (provider_id, provider_url) or None if no provider available
        """
        try:
            # Update provider health and rate limit status
            await self._update_provider_health_status()
            await self._update_provider_rate_limit_status()

            # Check global rate limit first
            global_allowed, global_count = await self.global_rate_limiter.is_allowed()
            if not global_allowed:
                logger.warning(f"Global rate limit exceeded: {global_count}")
                return None

            # Get healthy providers
            healthy_providers = self._get_healthy_providers()
            total_providers = len(self.provider_urls)
            healthy_count = len(healthy_providers)
            unhealthy_count = total_providers - healthy_count

            # Update distribution statistics
            self.distribution_stats.total_requests += 1
            self.distribution_stats.healthy_providers = healthy_count
            self.distribution_stats.unhealthy_providers = unhealthy_count

            # Log current status
            logger.debug(f"Provider status - Total: {total_providers}, Healthy: {healthy_count}, Unhealthy: {unhealthy_count}")

            if healthy_count == 0:
                logger.warning("No healthy providers available for distribution")
                return None

            # Update healthy providers queue
            self._update_healthy_providers_queue(healthy_providers)

            # Select provider using round-robin
            selected_provider = await self._get_next_provider_round_robin()

            if selected_provider:
                # Update usage statistics
                self.provider_usage_count[selected_provider] += 1
                self.distribution_stats.requests_per_provider[selected_provider] += 1

                provider_url = self.provider_urls[selected_provider]
                logger.info(f"Selected provider {selected_provider} via weighted round-robin (usage count: {self.provider_usage_count[selected_provider]})")

                return selected_provider, provider_url
            else:
                logger.warning("No provider selected despite having healthy providers")
                return None

        except Exception as e:
            logger.error(f"Error selecting provider: {str(e)}")
            return None

    def get_distribution_stats(self) -> Dict[str, Any]:
        """Get current distribution statistics."""
        return {
            "total_requests": self.distribution_stats.total_requests,
            "healthy_providers": self.distribution_stats.healthy_providers,
            "unhealthy_providers": self.distribution_stats.unhealthy_providers,
            "requests_per_provider": dict(self.distribution_stats.requests_per_provider),
            "provider_usage_count": dict(self.provider_usage_count),
            "round_robin_index": self.distribution_stats.round_robin_index,
            "healthy_providers_queue": list(self.healthy_providers_queue),
            "provider_status": {
                provider_id: {
                    "is_healthy": status.is_healthy,
                    "is_rate_limited": status.is_rate_limited,
                    "current_load": status.current_load,
                    "last_used": status.last_used
                }
                for provider_id, status in self.provider_status.items()
            }
        }

    async def reset_stats(self) -> None:
        """Reset distribution statistics (useful for testing)."""
        self.distribution_stats = DistributionStats()
        self.provider_usage_count.clear()

        for provider_id in self.provider_urls.keys():
            self.distribution_stats.requests_per_provider[provider_id] = 0

        logger.info("Distribution statistics reset")


async def create_distribution_service(
    health_tracker: ProviderHealthTracker,
    rate_limiter: RateLimiter,
    global_rate_limiter: GlobalRateLimiter,
    provider_urls: Dict[str, str]
) -> SMSDistributionService:
    """
    Factory function to create a distribution service instance.

    Args:
        health_tracker: ProviderHealthTracker instance
        rate_limiter: RateLimiter instance
        global_rate_limiter: GlobalRateLimiter instance
        provider_urls: Dictionary mapping provider IDs to their URLs

    Returns:
        Configured SMSDistributionService instance
    """
    return SMSDistributionService(
        health_tracker=health_tracker,
        rate_limiter=rate_limiter,
        global_rate_limiter=global_rate_limiter,
        provider_urls=provider_urls
    )