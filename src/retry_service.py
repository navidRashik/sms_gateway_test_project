"""
Retry service with exponential backoff for SMS requests.

This module implements sophisticated retry logic for failed SMS requests with:
- Exponential backoff delays
- Smart provider selection (skip failed providers)
- Round-robin distribution among healthy providers
- Database persistence for retry tracking
- Maximum retry attempts (5)
"""

import asyncio
import logging
import random
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from datetime import datetime

from redis.asyncio import Redis
from sqlmodel import Session

from .models import SMSRequest, SMSRetry
from .health_tracker import ProviderHealthTracker
from .config import settings

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Status information for SMS providers."""
    is_healthy: bool
    failure_count: int
    last_failure_time: Optional[datetime] = None


class RetryService:
    """
    Service for handling SMS retry logic with exponential backoff.

    Implements smart provider selection, round-robin distribution,
    and database persistence for retry attempts.
    """

    def __init__(
        self,
        redis_client: Redis,
        db_session: Session,
        health_tracker: ProviderHealthTracker,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 300.0,  # 5 minutes
        jitter: bool = True
    ):
        """
        Initialize retry service.

        Args:
            redis_client: Redis client for provider tracking
            db_session: Database session for persistence
            health_tracker: Provider health tracker instance
            max_retries: Maximum number of retry attempts (default: 5)
            base_delay: Base delay in seconds for exponential backoff (default: 1.0)
            max_delay: Maximum delay in seconds (default: 300.0)
            jitter: Whether to add random jitter to delays (default: True)
        """
        self.redis = redis_client
        self.db_session = db_session
        self.health_tracker = health_tracker
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

        # Available providers
        self.providers = {
            "provider1": settings.provider1_url,
            "provider2": settings.provider2_url,
            "provider3": settings.provider3_url,
        }

        # Round-robin counter for healthy provider selection
        self._round_robin_counter = 0

    def calculate_backoff_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay with optional jitter.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: base_delay * (2 ^ attempt)
        delay = self.base_delay * (2 ** attempt)

        # Cap at maximum delay
        delay = min(delay, self.max_delay)

        # Add jitter to prevent thundering herd
        if self.jitter:
            # Add random jitter between 0% and 25% of the delay
            jitter_amount = delay * 0.25 * random.random()
            delay += jitter_amount

        return delay

    async def get_failed_providers(self, request_id: int) -> Set[str]:
        """
        Get set of providers that have failed for a specific request.

        Args:
            request_id: SMS request ID

        Returns:
            Set of failed provider IDs
        """
        try:
            # Get failed providers from database
            retries = self.db_session.query(SMSRetry).filter(
                SMSRetry.request_id == request_id
            ).all()

            failed_providers = set()
            for retry in retries:
                failed_providers.add(retry.provider_used)

            return failed_providers

        except Exception as e:
            logger.error(f"Error getting failed providers for request {request_id}: {str(e)}")
            return set()

    async def get_healthy_providers(self, exclude_providers: Set[str] = None) -> List[str]:
        """
        Get list of healthy providers, excluding specified ones.

        Args:
            exclude_providers: Set of provider IDs to exclude

        Returns:
            List of healthy provider IDs
        """
        if exclude_providers is None:
            exclude_providers = set()

        healthy_providers = []

        for provider_id in self.providers.keys():
            if provider_id in exclude_providers:
                continue

            # Check health status
            is_healthy = await self.health_tracker.is_provider_healthy(provider_id)
            if is_healthy:
                healthy_providers.append(provider_id)

        return healthy_providers

    async def select_provider_round_robin(self, exclude_providers: Set[str] = None) -> Optional[str]:
        """
        Select a provider using round-robin distribution among healthy providers.

        Args:
            exclude_providers: Set of provider IDs to exclude

        Returns:
            Selected provider ID or None if no healthy providers available
        """
        healthy_providers = await self.get_healthy_providers(exclude_providers)

        if not healthy_providers:
            logger.warning("No healthy providers available for retry")
            return None

        # Use round-robin selection
        selected_index = self._round_robin_counter % len(healthy_providers)
        selected_provider = healthy_providers[selected_index]

        # Increment counter for next selection
        self._round_robin_counter += 1

        logger.info(f"Selected provider {selected_provider} via round-robin (index: {selected_index})")
        return selected_provider

    async def record_retry_attempt(
        self,
        request_id: int,
        attempt_number: int,
        provider_used: str,
        error_message: str,
        delay_seconds: int
    ) -> bool:
        """
        Record a retry attempt in the database.

        Args:
            request_id: SMS request ID
            attempt_number: Retry attempt number (1-5)
            provider_used: Provider used for this attempt
            error_message: Error message from the failed attempt
            delay_seconds: Delay before this retry in seconds

        Returns:
            True if recorded successfully
        """
        try:
            retry_record = SMSRetry(
                request_id=request_id,
                attempt_number=attempt_number,
                provider_used=provider_used,
                error_message=error_message,
                delay_seconds=delay_seconds
            )

            self.db_session.add(retry_record)
            self.db_session.commit()

            logger.debug(f"Recorded retry attempt {attempt_number} for request {request_id}")
            return True

        except Exception as e:
            logger.error(f"Error recording retry attempt for request {request_id}: {str(e)}")
            self.db_session.rollback()
            return False

    async def update_request_retry_status(
        self,
        request_id: int,
        retry_count: int,
        failed_providers: List[str],
        is_permanently_failed: bool = False
    ) -> bool:
        """
        Update SMS request retry status in database.

        Args:
            request_id: SMS request ID
            retry_count: Current retry count
            failed_providers: List of failed provider IDs
            is_permanently_failed: Whether request has permanently failed

        Returns:
            True if updated successfully
        """
        try:
            request = self.db_session.query(SMSRequest).filter(
                SMSRequest.id == request_id
            ).first()

            if not request:
                logger.error(f"SMS request {request_id} not found for status update")
                return False

            request.retry_count = retry_count
            request.failed_providers = ",".join(failed_providers)
            request.is_permanently_failed = is_permanently_failed
            request.updated_at = datetime.utcnow()

            # Update status based on retry state
            if is_permanently_failed:
                request.status = "permanently_failed"
            elif retry_count > 0:
                request.status = "retrying"

            self.db_session.commit()

            logger.debug(f"Updated retry status for request {request_id}: retry_count={retry_count}")
            return True

        except Exception as e:
            logger.error(f"Error updating retry status for request {request_id}: {str(e)}")
            self.db_session.rollback()
            return False

    async def should_retry(
        self,
        request_id: int,
        current_attempt: int,
        failed_provider: str,
        error_message: str
    ) -> Tuple[bool, Optional[str], float]:
        """
        Determine if request should be retried and select next provider.

        Args:
            request_id: SMS request ID
            current_attempt: Current attempt number (0-based)
            failed_provider: Provider that just failed
            error_message: Error message from the failure

        Returns:
            Tuple of (should_retry, next_provider_id, delay_seconds)
        """
        # Check if we've exceeded max retries
        if current_attempt >= self.max_retries:
            logger.info(f"Max retries ({self.max_retries}) exceeded for request {request_id}")
            await self.mark_request_permanently_failed(request_id)
            # Send to dead-letter queue
            await self.redis.lpush(
                "dead_letter_queue",
                f'{{"request_id": {request_id}, "reason": "Max retries exceeded"}}',
            )
            return False, None, 0

        # Get previously failed providers for this request
        failed_providers = await self.get_failed_providers(request_id)
        failed_providers.add(failed_provider)  # Add current failed provider

        # Select next provider using round-robin among healthy providers
        next_provider = await self.select_provider_round_robin(failed_providers)

        if not next_provider:
            logger.warning(f"No healthy providers available for retry of request {request_id}")
            return False, None, 0

        # Calculate delay for next attempt
        next_attempt = current_attempt + 1
        delay_seconds = self.calculate_backoff_delay(next_attempt)

        logger.info(
            f"Will retry request {request_id} with provider {next_provider} "
            f"in {delay_seconds:.1f}s (attempt {next_attempt}/{self.max_retries})"
        )

        return True, next_provider, delay_seconds

    async def mark_request_permanently_failed(self, request_id: int) -> bool:
        """
        Mark an SMS request as permanently failed after max retries.

        Args:
            request_id: SMS request ID

        Returns:
            True if marked successfully
        """
        return await self.update_request_retry_status(
            request_id=request_id,
            retry_count=self.max_retries,
            failed_providers=[],  # Will be populated from retry records
            is_permanently_failed=True
        )

    async def execute_retry_with_backoff(
        self,
        request_id: int,
        phone: str,
        text: str,
        current_attempt: int,
        provider_id: str,
        provider_url: str,
        error_message: str,
        delay_seconds: float
    ) -> Dict[str, any]:
        """
        Execute a retry attempt with the specified delay and provider.
        This method schedules the retry without blocking the worker.

        Args:
            request_id: SMS request ID
            phone: Phone number
            text: SMS message content
            current_attempt: Current attempt number (1-based)
            provider_id: Provider to use for retry
            provider_url: Provider URL
            error_message: Previous error message (for logging)
            delay_seconds: Delay before retry

        Returns:
            Retry result dictionary
        """
        try:
            # Record retry attempt in database
            await self.record_retry_attempt(
                request_id=request_id,
                attempt_number=current_attempt,
                provider_used=provider_id,
                error_message=error_message,
                delay_seconds=int(delay_seconds)
            )

            # Import here to avoid circular imports
            from .tasks import send_sms_to_provider
            from datetime import datetime, timedelta
            from .taskiq_scheduler import redis_source

            # Schedule the retry using TaskIQ with proper delay scheduling
            scheduled_time = datetime.utcnow() + timedelta(seconds=delay_seconds)
            
            # Schedule the retry task with the calculated delay
            try:
                send_sms_to_provider.kiq(
                    provider_url=provider_url,
                    phone=phone,
                    text=text,
                    message_id=f"retry_{request_id}_{current_attempt}",
                    provider_id=provider_id,
                    retry_count=current_attempt - 1  # This represents the retry count for the scheduled task
                ).schedule_by_time(
                    redis_source,
                    scheduled_time
                )
            except Exception as e:
                logger.error(f"Failed to schedule retry for request {request_id}: {str(e)}")
                # Fallback to immediate queuing if scheduling fails
                send_sms_to_provider.kiq(
                    provider_url=provider_url,
                    phone=phone,
                    text=text,
                    message_id=f"retry_{request_id}_{current_attempt}",
                    provider_id=provider_id,
                    retry_count=current_attempt - 1  # This represents the retry count for the scheduled task
                )

            return {
                "success": False,  # This is just scheduling, not actual result
                "message": f"Retry scheduled for attempt {current_attempt}",
                "retry_count": current_attempt,
                "provider": provider_id
            }

        except Exception as e:
            logger.error(f"Error scheduling retry for request {request_id}: {str(e)}")
            return {
                "success": False,
                "error": f"Retry scheduling error: {str(e)}",
                "retry_count": current_attempt
            }