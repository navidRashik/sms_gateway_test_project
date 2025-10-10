"""
Taskiq configuration and SMS processing tasks.

This module defines the Taskiq broker, worker, and SMS processing tasks
for asynchronous SMS sending with proper error handling and retries.
"""

import asyncio
import logging
import uuid
from typing import Dict, Any, Optional, Tuple, Set

import httpx
from redis.asyncio import Redis
from sqlmodel import Session, create_engine

from .config import settings
from .taskiq_config import TaskIQConfig, calculate_retry_delay
from .taskiq_scheduler import broker
from .retry_service import RetryService
from .database import (
    get_sms_request_repository,
    get_sms_response_repository,
    get_provider_health_repository,
)
from .rate_limiter import create_rate_limiter, create_global_rate_limiter
from .health_tracker import create_health_tracker
from .distribution import create_distribution_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The broker is now imported at the top of the file from taskiq_scheduler.py
# This ensures consistent broker usage across the application
# No need to define the broker here as it's imported from taskiq_scheduler


# Global instances for database and Redis connections
_db_engine = None
_redis_client = None
_retry_service = None


def get_db_engine():
    """Get or create database engine."""
    global _db_engine
    if _db_engine is None:
        _db_engine = create_engine(settings.database_url)
    return _db_engine


def get_redis_client():
    """Get or create Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def get_retry_service(health_tracker=None):
    """Get or create RetryService instance."""
    global _retry_service
    if _retry_service is None:
        _retry_service = RetryService(
            redis_client=get_redis_client(),
            db_session=Session(get_db_engine()),
            health_tracker=health_tracker,
            max_retries=5,  # As per T-007 requirements
            base_delay=1.0,
            max_delay=300.0,
            jitter=True
        )
    return _retry_service


@broker.task
async def send_sms_to_provider(
    provider_url: str,
    phone: str,
    text: str,
    message_id: str,
    provider_id: str,
    retry_count: int = 0,
    health_tracker=None,
    request_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Send SMS to a specific provider with proper TaskIQ integration.

    Args:
        provider_url: URL of the SMS provider
        phone: Phone number to send SMS to
        text: SMS message content
        message_id: Unique message identifier
        provider_id: Provider identifier (provider1, provider2, provider3)
        retry_count: Current retry attempt (default: 0)
        health_tracker: ProviderHealthTracker instance for recording metrics (optional)

    Returns:
        Dictionary with send result
    """
    max_retries = TaskIQConfig.MAX_RETRIES

    try:
        logger.info(f"Sending SMS to {provider_id}: {message_id}")

        payload = {
            "phone": phone,
            "text": text
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                provider_url, json=payload, headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"SMS sent successfully to {provider_id}: {message_id}")

                # Record successful send in health tracker if available
                if health_tracker is not None:
                    await health_tracker.record_success(provider_id)

                # Update database with successful response
                if request_id:
                    try:
                        sms_response_repo = get_sms_response_repository()
                        sms_request_repo = get_sms_request_repository()
                        provider_health_repo = get_provider_health_repository()

                        # Store response
                        sms_response_repo.create_response(
                            request_id=request_id,
                            response_data=str(result),
                            status_code=response.status_code
                        )

                        # Update request status to completed
                        sms_request_repo.update_request_status(request_id, "completed", provider_id)

                        # Update provider health
                        provider_health_repo.update_provider_health(provider_id, success=True)

                    except Exception as db_error:
                        logger.error(f"Failed to update database for successful SMS {message_id}: {str(db_error)}")

                return {
                    "success": True,
                    "message_id": message_id,
                    "provider": provider_id,
                    "response": result,
                    "retry_count": retry_count
                }
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.warning(f"SMS failed for {provider_id}: {message_id} - {error_msg}")

                if retry_count < max_retries:
                    # Schedule retry with exponential backoff using TaskIQ's scheduling
                    delay = calculate_retry_delay(retry_count)
                    logger.info(f"Scheduling retry {retry_count + 1} for {message_id} in {delay}s")

                    # Use proper TaskIQ scheduling with delay
                    from datetime import datetime, timedelta
                    from .taskiq_scheduler import redis_source

                    scheduled_time = datetime.utcnow() + timedelta(seconds=delay)

                    # Schedule the retry task with the calculated delay
                    try:
                        # Re-dispatch to select a fresh provider at execution time
                        await dispatch_sms.kiq(
                            phone=phone,
                            text=text,
                            message_id=message_id,
                            request_id=request_id,
                            exclude_providers=[provider_id],
                            retry_count=retry_count + 1,
                        ).schedule_by_time(redis_source, scheduled_time)

                        # Return failure result immediately - the retry will be handled by TaskIQ scheduler
                        return {
                            "success": False,
                            "message_id": message_id,
                            "provider": provider_id,
                            "error": f"HTTP {response.status_code}: Retry scheduled",
                            "retry_count": retry_count,
                            "retry_scheduled": True,
                            "retry_in_seconds": delay,
                        }
                    except Exception as e:
                        logger.error(
                            f"Failed to schedule retry for {message_id}: {str(e)}"
                        )
                        # Fallback to immediate re-dispatch if scheduling fails
                        await dispatch_sms.kiq(
                            phone=phone,
                            text=text,
                            message_id=message_id,
                            request_id=request_id,
                            exclude_providers=[provider_id],
                            retry_count=retry_count + 1,
                        )

                        return {
                            "success": False,
                            "message_id": message_id,
                            "provider": provider_id,
                            "error": f"HTTP {response.status_code}: Retry queued (scheduling failed)",
                            "retry_count": retry_count,
                            "retry_scheduled": True,
                            "retry_in_seconds": delay,
                        }

                # Record failed send in health tracker if available
                if health_tracker is not None:
                    await health_tracker.record_failure(provider_id)

                # Update database with failed response
                if request_id:
                    try:
                        sms_response_repo = get_sms_response_repository()
                        provider_health_repo = get_provider_health_repository()

                        # Store failed response
                        sms_response_repo.create_response(
                            request_id=request_id,
                            response_data=response.text,
                            status_code=response.status_code
                        )

                        # Update provider health
                        provider_health_repo.update_provider_health(provider_id, success=False)

                    except Exception as db_error:
                        logger.error(f"Failed to update database for failed SMS {message_id}: {str(db_error)}")

                return {
                    "success": False,
                    "message_id": message_id,
                    "provider": provider_id,
                    "error": error_msg,
                    "retry_count": retry_count
                }

    except httpx.TimeoutException as e:
        logger.error(f"Timeout sending SMS to {provider_id}: {message_id} - {str(e)}")

        if retry_count < max_retries:
            # Schedule retry with exponential backoff using TaskIQ's scheduling
            delay = calculate_retry_delay(retry_count)
            logger.info(f"Scheduling timeout retry {retry_count + 1} for {message_id} in {delay}s")

            # Use proper TaskIQ scheduling with delay
            from datetime import datetime, timedelta
            from .taskiq_scheduler import redis_source

            scheduled_time = datetime.utcnow() + timedelta(seconds=delay)

            # Schedule the retry task with the calculated delay
            try:
                await dispatch_sms.kiq(
                    phone=phone,
                    text=text,
                    message_id=message_id,
                    request_id=request_id,
                    exclude_providers=[provider_id],
                    retry_count=retry_count + 1,
                ).schedule_by_time(redis_source, scheduled_time)

                # Return failure result immediately - the retry will be handled by TaskIQ scheduler
                return {
                    "success": False,
                    "message_id": message_id,
                    "provider": provider_id,
                    "error": "Timeout: Retry scheduled",
                    "retry_count": retry_count,
                    "retry_scheduled": True,
                    "retry_in_seconds": delay,
                }
            except Exception as e:
                logger.error(
                    f"Failed to schedule timeout retry for {message_id}: {str(e)}"
                )
                # Fallback to immediate re-dispatch if scheduling fails
                await dispatch_sms.kiq(
                    phone=phone,
                    text=text,
                    message_id=message_id,
                    request_id=request_id,
                    exclude_providers=[provider_id],
                    retry_count=retry_count + 1,
                )

                return {
                    "success": False,
                    "message_id": message_id,
                    "provider": provider_id,
                    "error": "Timeout: Retry queued (scheduling failed)",
                    "retry_count": retry_count,
                    "retry_scheduled": True,
                    "retry_in_seconds": delay,
                }

        # Record failed send in health tracker if available
        if health_tracker is not None:
            await health_tracker.record_failure(provider_id)

        # Update database with timeout failure
        if request_id:
            try:
                sms_response_repo = get_sms_response_repository()
                provider_health_repo = get_provider_health_repository()

                # Store timeout response
                sms_response_repo.create_response(
                    request_id=request_id,
                    response_data=f"Timeout: {str(e)}",
                    status_code=408  # Request Timeout status code
                )

                # Update provider health
                provider_health_repo.update_provider_health(provider_id, success=False)

            except Exception as db_error:
                logger.error(f"Failed to update database for timeout SMS {message_id}: {str(db_error)}")

        return {
            "success": False,
            "message_id": message_id,
            "provider": provider_id,
            "error": f"Timeout: {str(e)}",
            "retry_count": retry_count
        }

    except Exception as e:
        logger.error(f"Unexpected error sending SMS to {provider_id}: {message_id} - {str(e)}")

        if retry_count < max_retries:
            # Schedule retry with exponential backoff using TaskIQ's scheduling
            delay = calculate_retry_delay(retry_count)
            logger.info(f"Scheduling error retry {retry_count + 1} for {message_id} in {delay}s")

            # Use proper TaskIQ scheduling with delay
            from datetime import datetime, timedelta
            from .taskiq_scheduler import redis_source

            scheduled_time = datetime.utcnow() + timedelta(seconds=delay)

            # Schedule the retry task with the calculated delay
            try:
                await dispatch_sms.kiq(
                    phone=phone,
                    text=text,
                    message_id=message_id,
                    request_id=request_id,
                    exclude_providers=[provider_id],
                    retry_count=retry_count + 1,
                ).schedule_by_time(redis_source, scheduled_time)

                # Return failure result immediately - the retry will be handled by TaskIQ scheduler
                return {
                    "success": False,
                    "message_id": message_id,
                    "provider": provider_id,
                    "error": "Unexpected error: Retry scheduled",
                    "retry_count": retry_count,
                    "retry_scheduled": True,
                    "retry_in_seconds": delay,
                }
            except Exception as e:
                logger.error(
                    f"Failed to schedule error retry for {message_id}: {str(e)}"
                )
                # Fallback to immediate re-dispatch if scheduling fails
                await dispatch_sms.kiq(
                    phone=phone,
                    text=text,
                    message_id=message_id,
                    request_id=request_id,
                    exclude_providers=[provider_id],
                    retry_count=retry_count + 1,
                )

                return {
                    "success": False,
                    "message_id": message_id,
                    "provider": provider_id,
                    "error": "Unexpected error: Retry queued (scheduling failed)",
                    "retry_count": retry_count,
                    "retry_scheduled": True,
                    "retry_in_seconds": delay,
                }

        # Record failed send in health tracker if available
        if health_tracker is not None:
            await health_tracker.record_failure(provider_id)

        # Update database with unexpected error
        if request_id:
            try:
                sms_response_repo = get_sms_response_repository()
                provider_health_repo = get_provider_health_repository()

                # Store error response
                sms_response_repo.create_response(
                    request_id=request_id,
                    response_data=f"Unexpected error: {str(e)}",
                    status_code=500  # Internal Server Error status code
                )

                # Update provider health
                provider_health_repo.update_provider_health(provider_id, success=False)

            except Exception as db_error:
                logger.error(f"Failed to update database for error SMS {message_id}: {str(db_error)}")

        return {
            "success": False,
            "message_id": message_id,
            "provider": provider_id,
            "error": f"Unexpected error: {str(e)}",
            "retry_count": retry_count
        }


@broker.task
async def process_sms_batch(
    batch_data: Dict[str, Any],
    provider_url: str,
    provider_id: str
) -> Dict[str, Any]:
    """
    Process a batch of SMS messages for a provider.

    Args:
        batch_data: Dictionary containing batch information
        provider_url: Provider URL
        provider_id: Provider identifier

    Returns:
        Dictionary with batch processing results
    """
    messages = batch_data.get("messages", [])
    batch_id = batch_data.get("batch_id", "unknown")

    logger.info(f"Processing SMS batch {batch_id} for {provider_id} with {len(messages)} messages")

    results = []
    successful = 0
    failed = 0

    for message in messages:
        result = await send_sms_to_provider.kicker(
            provider_url=provider_url,
            phone=message["phone"],
            text=message["text"],
            message_id=message["message_id"],
            provider_id=provider_id
        )

        results.append(result)

        if result["success"]:
            successful += 1
        else:
            failed += 1

    logger.info(f"Batch {batch_id} completed: {successful} success, {failed} failed")

    return {
        "batch_id": batch_id,
        "provider": provider_id,
        "total_messages": len(messages),
        "successful": successful,
        "failed": failed,
        "results": results
    }


async def get_available_providers() -> Dict[str, str]:
    """
    Get dictionary of available SMS providers and their URLs.

    Returns:
        Dictionary mapping provider IDs to their URLs
    """
    return {
        "provider1": settings.provider1_url,
        "provider2": settings.provider2_url,
        "provider3": settings.provider3_url,
    }


async def select_best_provider(
    rate_limiter,
    global_rate_limiter,
    health_tracker=None,
    distribution_service=None,
    exclude_providers: Optional[Set[str]] = None,
) -> Optional[Tuple[str, str]]:
    """
    Select the best available provider using intelligent distribution logic.

    Args:
        rate_limiter: RateLimiter instance
        global_rate_limiter: GlobalRateLimiter instance
        health_tracker: ProviderHealthTracker instance (optional)
        distribution_service: SMSDistributionService instance (optional)

    Returns:
        Tuple of (provider_id, provider_url) or None if no provider available
    """
    # Use new distribution service if available (no exclusion support here yet)
    if distribution_service is not None and not exclude_providers:
        return await distribution_service.select_provider()

    # Fallback to old logic if distribution service is not available
    providers = await get_available_providers()

    # Check global rate limit first
    global_allowed, global_count = await global_rate_limiter.is_allowed()
    if not global_allowed:
        logger.warning(f"Global rate limit exceeded: {global_count}")
        return None

    # Check each provider's rate limit and health status
    exclude_providers = exclude_providers or set()

    for provider_id, provider_url in providers.items():
        # Skip excluded providers
        if provider_id in exclude_providers:
            logger.debug(f"Excluding provider {provider_id} from selection")
            continue
        # Check rate limit
        allowed, count = await rate_limiter.is_allowed(provider_id)
        if not allowed:
            logger.debug(f"Provider {provider_id} rate limited (count: {count})")
            continue

        # Check health status if health tracker is provided
        if health_tracker is not None:
            is_healthy = await health_tracker.is_provider_healthy(provider_id)
            if not is_healthy:
                logger.warning(f"Provider {provider_id} is unhealthy, skipping")
                continue

        logger.info(f"Selected provider {provider_id} (count: {count})")
        return provider_id, provider_url

    logger.warning("All providers rate limited or unhealthy")
    return None


@broker.task
async def dispatch_sms(
    phone: str,
    text: str,
    message_id: str,
    request_id: Optional[int] = None,
    exclude_providers: Optional[list[str]] = None,
    retry_count: int = 0,
) -> Optional[str]:
    """Task that selects provider at execution time and dispatches send."""
    try:
        redis_client = get_redis_client()
        # Build dependencies fresh for up-to-date state
        rate_limiter = await create_rate_limiter(redis_client)
        global_rate_limiter = await create_global_rate_limiter(redis_client)
        health_tracker = await create_health_tracker(redis_client)
        provider_urls = await get_available_providers()
        distribution_service = await create_distribution_service(
            health_tracker=health_tracker,
            rate_limiter=rate_limiter,
            global_rate_limiter=global_rate_limiter,
            provider_urls=provider_urls,
        )

        selection = await select_best_provider(
            rate_limiter,
            global_rate_limiter,
            health_tracker,
            distribution_service,
            exclude_providers=set(exclude_providers or []),
        )

        if not selection:
            logger.warning(
                f"No available provider at execution time for message {message_id}"
            )
            return None

        provider_id, provider_url = selection

        # Update DB with processing status and chosen provider
        if request_id:
            try:
                sms_request_repo = get_sms_request_repository()
                sms_request_repo.update_request_status(
                    request_id, "processing", provider_id
                )
            except Exception as db_error:
                logger.error(
                    f"Failed to update request {request_id} before send: {db_error}"
                )

        # Enqueue the actual send (don't pass health_tracker - it's not serializable)
        await send_sms_to_provider.kiq(
            provider_url=provider_url,
            phone=phone,
            text=text,
            message_id=message_id,
            provider_id=provider_id,
            retry_count=retry_count,
            health_tracker=None,  # Health tracker cannot be serialized, will be recreated in task
            request_id=request_id,
        )

        logger.info(f"Dispatched SMS {message_id} to provider {provider_id}")
        return message_id

    except Exception as e:
        logger.error(f"Error in dispatch_sms for message {message_id}: {e}")
        return None


async def queue_sms_task(
    phone: str,
    text: str,
    rate_limiter=None,
    global_rate_limiter=None,
    health_tracker=None,
    distribution_service=None,
) -> Optional[str]:
    """
    Queue an SMS task for processing.

    Args:
        phone: Phone number
        text: SMS message content
        rate_limiter: RateLimiter instance
        global_rate_limiter: GlobalRateLimiter instance
        health_tracker: ProviderHealthTracker instance (optional)
        distribution_service: SMSDistributionService instance (optional)

    Returns:
        Message ID if queued successfully, None otherwise
    """
    message_id = f"msg_{int(asyncio.get_event_loop().time())}_{str(uuid.uuid4())[:8]}"

    try:
        # Persist SMS request in database (provider will be chosen later)
        sms_request_repo = get_sms_request_repository()
        sms_request = sms_request_repo.create_request(
            phone=phone, text=text, provider_used=None
        )

        # Mark as processing since it's now enqueued for dispatch
        sms_request_repo.update_request_status(sms_request.id, "processing")

        # Queue the dispatch task which selects provider at execution time
        await dispatch_sms.kiq(
            phone=phone,
            text=text,
            message_id=message_id,
            request_id=sms_request.id,
            exclude_providers=[],
            retry_count=0,
        )

        logger.info(
            f"Queued SMS dispatch task {message_id} (request ID: {sms_request.id})"
        )
        return message_id

    except Exception as e:
        logger.error(f"Error queueing SMS task: {str(e)}")
        return None


# Standalone worker runner for development
async def run_worker():
    """Run the Taskiq worker."""
    from taskiq.cli.worker import run_worker as run_worker_cmd

    await run_worker_cmd(
        broker=broker,
        worker_name="sms_worker",
    )


if __name__ == "__main__":
    # Run worker when script is executed directly
    asyncio.run(run_worker())