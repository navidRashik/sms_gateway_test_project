"""
Weighted round-robin distribution service for SMS providers.

This module implements intelligent request distribution that uses weighted round-robin
when providers are healthy and fallback logic when providers become unhealthy.
"""

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .health_tracker import ProviderHealthTracker
from .rate_limiter import GlobalRateLimiter, RateLimiter

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

        # Update healthy/unhealthy provider counts in distribution_stats
        healthy_count = sum(
            1 for status in self.provider_status.values() if status.is_healthy
        )
        unhealthy_count = sum(
            1 for status in self.provider_status.values() if not status.is_healthy
        )
        self.distribution_stats.healthy_providers = healthy_count
        self.distribution_stats.unhealthy_providers = unhealthy_count

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

    async def _should_use_weighted_distribution(self) -> bool:
        """
        Determine if weighted distribution should be used based on provider health.

        Returns True if any provider has failures, False otherwise.
        """
        for provider_id in self.provider_urls.keys():
            health_info = await self.health_tracker.get_health_status(provider_id)
            failure_count = health_info.get("failure_count", 0)
            failure_rate = health_info.get("failure_rate", 0)
            # Use weighted distribution if there are failures (count or rate)
            if failure_count > 0 or failure_rate > 0:
                return True
        return False

    async def _get_weighted_provider_round_robin(self) -> Optional[str]:
        """
        Get provider using weighted round-robin based on success rates.
        Providers with higher success rates get more weight.
        """
        healthy_providers = self._get_healthy_providers()
        if not healthy_providers:
            return None

        # Calculate weights based on success rates
        weights = {}
        for provider_id in healthy_providers:
            health_info = await self.health_tracker.get_health_status(provider_id)
            # Calculate success rate from failure rate or use provided success rate
            if "success_rate" in health_info:
                success_rate = health_info["success_rate"]
            elif "failure_rate" in health_info:
                success_rate = 1.0 - health_info["failure_rate"]
            else:
                success_rate = 1.0  # Default if no rate information
            # Use success rate as weight (higher success = higher weight)
            weights[provider_id] = max(0.1, success_rate)  # Minimum weight of 0.1

        # Select provider balancing between weight and usage count
        # Calculate a score that favors higher weights while still considering fairness
        best_provider = None
        best_score = -1

        for provider_id in healthy_providers:
            weight = weights[provider_id]
            usage = self.provider_usage_count.get(provider_id, 0)
            # Give more emphasis to weight vs fairness
            # weight^2 amplifies the difference between weights
            score = (weight * weight) / (usage + 1)
            if score > best_score:
                best_score = score
                best_provider = provider_id

        return best_provider

    async def _get_simple_round_robin(self, providers: List[str]) -> Optional[str]:
        """
        Simple round-robin across all providers.

        Args:
            providers: List of provider IDs to distribute across

        Returns:
            Selected provider ID or None
        """
        if not providers:
            return None

        # Use round-robin index to select provider
        provider_id = providers[
            self.distribution_stats.round_robin_index % len(providers)
        ]

        # Update index for next selection
        self.distribution_stats.round_robin_index += 1

        return provider_id

    async def _find_alternative_provider(
        self, excluded_provider: str, use_weighted: bool
    ) -> Optional[str]:
        """
        Find an alternative provider excluding the given provider.

        Args:
            excluded_provider: Provider ID to exclude
            use_weighted: Whether to use weighted selection

        Returns:
            Alternative provider ID or None
        """
        # Get all healthy non-rate-limited providers except the excluded one
        alternative_providers = [
            p for p in self._get_healthy_providers() if p != excluded_provider
        ]

        if not alternative_providers:
            return None

        if use_weighted:
            # Use weighted selection for alternatives
            weights = {}
            for provider_id in alternative_providers:
                health_info = await self.health_tracker.get_health_status(provider_id)
                success_rate = health_info.get("success_rate", 1.0)
                weights[provider_id] = max(0.1, success_rate)

            # Select best alternative
            best_provider = max(alternative_providers, key=lambda p: weights[p])
            return best_provider
        else:
            # Simple round-robin for alternatives
            return alternative_providers[0]

    async def select_provider(self) -> Optional[Tuple[str, str]]:
        """
        Select the best provider for SMS distribution using round-robin from start,
        switching to weighted round-robin when failures occur.

        Returns:
            Tuple of (provider_id, provider_url) or None if no provider available
        """
        try:
            # Update distribution statistics
            self.distribution_stats.total_requests += 1

            # Update provider health and rate limit status
            await self._update_provider_health_status()
            await self._update_provider_rate_limit_status()

            # Check global rate limit first (non-mutating check)
            global_count = await self.global_rate_limiter.get_current_count()
            if global_count >= self.global_rate_limiter.rate_limit:
                logger.warning(f"Global rate limit exceeded: {global_count}")
                return None

            # Get all healthy and non-rate-limited providers
            healthy_providers = self._get_healthy_providers()
            if not healthy_providers:
                logger.warning("No healthy providers available")
                return None

            # Check if we should use weighted distribution based on failure history
            use_weighted_distribution = await self._should_use_weighted_distribution()

            if use_weighted_distribution:
                # Use weighted round-robin based on success rates
                selected_provider = await self._get_weighted_provider_round_robin()
                distribution_type = "weighted round-robin"
            else:
                # Use simple round-robin across healthy providers for even initial distribution
                selected_provider = await self._get_simple_round_robin(
                    healthy_providers
                )
                distribution_type = "round-robin"

            if selected_provider:
                # Check if this provider is rate limited before final selection
                status = self.provider_status[selected_provider]
                if status.is_rate_limited:
                    logger.warning(
                        f"Provider {selected_provider} is rate limited, skipping"
                    )
                    # Try to find an alternative non-rate-limited provider
                    alternative_provider = await self._find_alternative_provider(
                        selected_provider, use_weighted_distribution
                    )
                    if alternative_provider:
                        selected_provider = alternative_provider
                        logger.info(
                            f"Selected alternative provider {selected_provider} due to rate limiting"
                        )
                    else:
                        logger.warning(
                            f"No available non-rate-limited provider for {selected_provider}"
                        )
                        return None

                # Update usage statistics
                self.provider_usage_count[selected_provider] += 1
                self.distribution_stats.requests_per_provider[selected_provider] += 1

                provider_url = self.provider_urls[selected_provider]
                logger.info(
                    f"Selected provider {selected_provider} via {distribution_type} (usage count: {self.provider_usage_count[selected_provider]})"
                )

                return selected_provider, provider_url
            else:
                logger.warning("No provider selected")
                return None

        except Exception as e:
            logger.error(f"Error selecting provider: {str(e)}")
            # Only fall back to first available provider for health tracker errors
            error_message = str(e).lower()
            if "health tracker" in error_message:
                try:
                    for provider_id, provider_url in self.provider_urls.items():
                        logger.info(
                            f"Falling back to default provider {provider_id} due to health tracker error"
                        )
                        return provider_id, provider_url
                except Exception as fallback_error:
                    logger.error(
                        f"Fallback provider selection failed: {str(fallback_error)}"
                    )
                    return None
            else:
                # For rate limiter errors and other unexpected errors, return None
                return None

    def get_distribution_stats(self) -> Dict[str, Any]:
        """Get current distribution statistics."""
        return {
            "total_requests": self.distribution_stats.total_requests,
            "healthy_providers": self.distribution_stats.healthy_providers,
            "unhealthy_providers": self.distribution_stats.unhealthy_providers,
            "requests_per_provider": dict(
                self.distribution_stats.requests_per_provider
            ),
            "provider_usage_count": dict(self.provider_usage_count),
            "round_robin_index": self.distribution_stats.round_robin_index,
            "healthy_providers_queue": list(self.healthy_providers_queue),
            "provider_status": {
                provider_id: {
                    "is_healthy": status.is_healthy,
                    "is_rate_limited": status.is_rate_limited,
                    "current_load": status.current_load,
                    "last_used": status.last_used,
                }
                for provider_id, status in self.provider_status.items()
            },
        }

    async def reset_stats(self) -> None:
        """Reset all distribution statistics."""
        self.distribution_stats.total_requests = 0
        self.distribution_stats.healthy_providers = 0
        self.distribution_stats.unhealthy_providers = 0
        # Reset requests_per_provider to 0 for all providers instead of clearing
        for provider_id in self.provider_urls.keys():
            self.distribution_stats.requests_per_provider[provider_id] = 0
        self.distribution_stats.round_robin_index = 0
        self.provider_usage_count.clear()
        self.healthy_providers_queue.clear()


def create_distribution_service(
    health_tracker: ProviderHealthTracker,
    rate_limiter: RateLimiter,
    global_rate_limiter: GlobalRateLimiter,
    provider_urls: Dict[str, str],
) -> SMSDistributionService:
    """
    Factory function to create a distribution service.

    Args:
        health_tracker: ProviderHealthTracker instance
        rate_limiter: RateLimiter instance for per-provider limits
        global_rate_limiter: GlobalRateLimiter instance
        provider_urls: Dictionary mapping provider IDs to their URLs

    Returns:
        SMSDistributionService instance
    """
    return SMSDistributionService(
        health_tracker=health_tracker,
        rate_limiter=rate_limiter,
        global_rate_limiter=global_rate_limiter,
        provider_urls=provider_urls
    )