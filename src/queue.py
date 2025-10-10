"""
Main SMS API endpoint with rate limiting and queueing.

This module provides the primary SMS sending endpoint that:
1. Validates incoming requests
2. Checks rate limits (both per-provider and global)
3. Queues requests for asynchronous processing
4. Returns immediate response with message ID
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, status, Request, Depends
from pydantic import BaseModel, Field

from .config import settings
from .rate_limiter import RateLimiter, GlobalRateLimiter, create_rate_limiter, create_global_rate_limiter
from .tasks import queue_sms_task
from .distribution import create_distribution_service, SMSDistributionService
from .health_tracker import create_health_tracker
from .database import get_sms_request_repository, get_sms_response_repository

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic models for request/response validation

class SMSRequest(BaseModel):
    """SMS request model."""
    phone: str = Field(..., min_length=10, max_length=15, description="Phone number in international format")
    text: str = Field(..., min_length=1, max_length=160, description="SMS message text (max 160 characters)")

    class Config:
        schema_extra = {
            "example": {
                "phone": "01921317475",
                "text": "Hello, this is a test SMS!"
            }
        }


class SMSResponse(BaseModel):
    """SMS response model."""
    success: bool
    message_id: Optional[str] = None
    message: str
    provider: Optional[str] = None
    queued: bool = False


class RateLimitInfo(BaseModel):
    """Rate limit information model."""
    provider_limited: bool = False
    global_limited: bool = False
    provider_count: int = 0
    global_count: int = 0
    provider_limit: int = 50
    global_limit: int = 200


# Create router
router = APIRouter()


# Dependency to get Redis client
async def get_redis_client(request: Request) -> Any:
    """Get Redis client from app state."""
    return request.app.state.redis


# Dependency to get rate limiter instances
async def get_rate_limiters(redis_client=Depends(get_redis_client)) -> tuple[RateLimiter, GlobalRateLimiter]:
    """Get rate limiter instances."""
    rate_limiter = await create_rate_limiter(redis_client)
    global_rate_limiter = await create_global_rate_limiter(redis_client)
    return rate_limiter, global_rate_limiter


# Dependency to get health tracker instance
async def get_health_tracker(redis_client=Depends(get_redis_client)):
    """Get health tracker instance."""
    return await create_health_tracker(redis_client)


# Dependency to get distribution service
async def get_distribution_service(
    redis_client=Depends(get_redis_client),
    rate_limiters=Depends(get_rate_limiters),
    health_tracker=Depends(get_health_tracker)
) -> SMSDistributionService:
    """Get distribution service instance."""
    rate_limiter, global_rate_limiter = rate_limiters

    # Get provider URLs
    provider_urls = {
        "provider1": settings.provider1_url,
        "provider2": settings.provider2_url,
        "provider3": settings.provider3_url,
    }

    return await create_distribution_service(
        health_tracker=health_tracker,
        rate_limiter=rate_limiter,
        global_rate_limiter=global_rate_limiter,
        provider_urls=provider_urls
    )


@router.post(
    "/send",
    response_model=SMSResponse,
    summary="Send SMS with queueing",
    description="Send SMS with automatic rate limiting and queueing for high throughput",
    response_description="SMS send result with message ID and status"
)
async def send_sms(
    request: SMSRequest,
    redis_client=Depends(get_redis_client),
    rate_limiters=Depends(get_rate_limiters),
    distribution_service=Depends(get_distribution_service)
) -> SMSResponse:
    """
    Send SMS with intelligent queueing and rate limiting.

    This endpoint provides the main SMS sending functionality that:
    - Validates the request
    - Checks rate limits for all providers
    - Queues the SMS for asynchronous processing
    - Returns immediate response with message ID

    Args:
        request: SMS request with phone and text

    Returns:
        SMS response with success status and message ID

    Raises:
        HTTPException: If request is invalid or system overloaded
    """
    rate_limiter, global_rate_limiter = rate_limiters

    try:
        logger.info(f"Processing SMS request: {request.phone}")

        # Check global rate limit first
        global_allowed, global_count = await global_rate_limiter.is_allowed()

        if not global_allowed:
            logger.warning(f"Global rate limit exceeded: {global_count}/{settings.total_rate_limit}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Global rate limit exceeded",
                    "current_count": global_count,
                    "limit": settings.total_rate_limit,
                    "reset_in_seconds": 1
                }
            )

        # Queue the SMS task with intelligent distribution
        message_id = await queue_sms_task(
            phone=request.phone,
            text=request.text,
        )

        if message_id:
            # Successfully queued
            logger.info(f"SMS queued successfully: {message_id}")
            return SMSResponse(
                success=True,
                message_id=message_id,
                message="SMS queued for sending",
                queued=True
            )
        else:
            # No provider available
            logger.warning("No provider available for SMS request")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No SMS provider available"
            )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise

    except Exception as e:
        logger.error(f"Unexpected error processing SMS: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get(
    "/rate-limits",
    response_model=RateLimitInfo,
    summary="Get current rate limit status",
    description="Get current rate limiting status for all providers and global limits",
    response_description="Current rate limit information"
)
async def get_rate_limits(
    rate_limiters=Depends(get_rate_limiters)
) -> RateLimitInfo:
    """
    Get current rate limit status for debugging and monitoring.

    Returns:
        Current rate limit information for all providers
    """
    rate_limiter, global_rate_limiter = rate_limiters
    return await _get_rate_limit_info(rate_limiter, global_rate_limiter)


async def _get_rate_limit_info(
    rate_limiter: RateLimiter,
    global_rate_limiter: GlobalRateLimiter
) -> RateLimitInfo:
    """Get comprehensive rate limit information."""
    # Check all providers
    provider_limited = True
    provider_count = 0

    for provider_id in ["provider1", "provider2", "provider3"]:
        allowed, count = await rate_limiter.is_allowed(provider_id)
        if allowed:
            provider_limited = False
            provider_count = count
            break
        if count > provider_count:
            provider_count = count

    # Check global limits
    global_allowed, global_count = await global_rate_limiter.is_allowed()
    global_limited = not global_allowed

    return RateLimitInfo(
        provider_limited=provider_limited,
        global_limited=global_limited,
        provider_count=provider_count,
        global_count=global_count,
        provider_limit=settings.provider_rate_limit,
        global_limit=settings.total_rate_limit
    )


@router.get(
    "/health",
    response_model=Dict[str, Any],
    summary="Get provider health status",
    description="Get comprehensive health status for all SMS providers",
    response_description="Health status information"
)
async def get_health_status(
    health_tracker=Depends(get_health_tracker)
) -> Dict[str, Any]:
    """
    Get comprehensive health status for all providers.

    Returns:
        Health status information for all providers
    """
    return await health_tracker.get_all_providers_health()


@router.get(
    "/health/{provider_id}",
    response_model=Dict[str, Any],
    summary="Get specific provider health status",
    description="Get detailed health status for a specific provider",
    response_description="Provider health status"
)
async def get_provider_health_status(
    provider_id: str,
    health_tracker=Depends(get_health_tracker)
) -> Dict[str, Any]:
    """
    Get detailed health status for a specific provider.

    Args:
        provider_id: Provider identifier (provider1, provider2, provider3)

    Returns:
        Detailed health status for the provider
    """
    if provider_id not in ["provider1", "provider2", "provider3"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not found"
        )

    return await health_tracker.get_health_status(provider_id)


@router.post(
    "/health/{provider_id}/reset",
    response_model=Dict[str, Any],
    summary="Reset provider health metrics",
    description="Reset health metrics for a specific provider (for testing/admin purposes)",
    response_description="Reset result"
)
async def reset_provider_health(
    provider_id: str,
    health_tracker=Depends(get_health_tracker)
) -> Dict[str, Any]:
    """
    Reset health metrics for a specific provider.

    Args:
        provider_id: Provider identifier (provider1, provider2, provider3)

    Returns:
        Reset confirmation
    """
    if provider_id not in ["provider1", "provider2", "provider3"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not found"
        )

    success = await health_tracker.reset_provider_health(provider_id)

    if success:
        return {
            "success": True,
            "message": f"Health metrics reset for {provider_id}",
            "provider_id": provider_id
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset health metrics for {provider_id}"
        )


@router.get(
    "/distribution-stats",
    response_model=Dict[str, Any],
    summary="Get distribution service statistics",
    description="Get statistics about the weighted round-robin distribution",
    response_description="Distribution statistics"
)
async def get_distribution_stats(
    distribution_service=Depends(get_distribution_service)
) -> Dict[str, Any]:
    """
    Get distribution service statistics for monitoring.

    Returns:
        Distribution statistics including provider usage and health status
    """
    return distribution_service.get_distribution_stats()


@router.get(
    "/requests",
    summary="Get SMS requests with filtering",
    description="Get SMS requests with optional filtering by status, provider, and time range",
    response_description="List of SMS requests matching the filters"
)
async def get_sms_requests(
    status: Optional[str] = None,
    provider: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get SMS requests with filtering options.

    Args:
        status: Filter by request status (pending, processing, completed, failed)
        provider: Filter by provider (provider1, provider2, provider3)
        start_time: Start time in ISO format (e.g., 2025-01-01T00:00:00)
        end_time: End time in ISO format (e.g., 2025-01-01T23:59:59)
        limit: Maximum number of results (default: 100, max: 1000)

    Returns:
        List of SMS requests matching the filters
    """
    try:
        # Parse datetime strings if provided
        start_dt = None
        end_dt = None

        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid start_time format. Use ISO format (e.g., 2025-01-01T00:00:00)"
                )

        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid end_time format. Use ISO format (e.g., 2025-01-01T23:59:59)"
                )

        # Validate limit
        if limit < 1 or limit > 1000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit must be between 1 and 1000"
            )

        # Get requests from database
        sms_request_repo = get_sms_request_repository()
        requests = sms_request_repo.get_requests_with_filters(
            status=status,
            provider=provider,
            start_time=start_dt,
            end_time=end_dt,
            limit=limit
        )

        # Convert to dictionaries for JSON response
        return [
            {
                "id": request.id,
                "phone": request.phone,
                "text": request.text,
                "status": request.status,
                "provider_used": request.provider_used,
                "retry_count": request.retry_count,
                "max_retries": request.max_retries,
                "failed_providers": request.failed_providers,
                "is_permanently_failed": request.is_permanently_failed,
                "created_at": request.created_at.isoformat() if request.created_at else None,
                "updated_at": request.updated_at.isoformat() if request.updated_at else None
            }
            for request in requests
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving SMS requests: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get(
    "/requests/{request_id}",
    summary="Get specific SMS request",
    description="Get detailed information about a specific SMS request",
    response_description="SMS request details"
)
async def get_sms_request(request_id: int) -> Dict[str, Any]:
    """
    Get detailed information about a specific SMS request.

    Args:
        request_id: SMS request ID

    Returns:
        SMS request details including responses
    """
    try:
        sms_request_repo = get_sms_request_repository()
        sms_response_repo = get_sms_response_repository()

        # Get the request
        request = sms_request_repo.get_request_by_id(request_id)
        if not request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SMS request {request_id} not found"
            )

        # Get associated responses
        responses = sms_response_repo.get_response_by_request_id(request_id)

        return {
            "id": request.id,
            "phone": request.phone,
            "text": request.text,
            "status": request.status,
            "provider_used": request.provider_used,
            "retry_count": request.retry_count,
            "max_retries": request.max_retries,
            "failed_providers": request.failed_providers,
            "is_permanently_failed": request.is_permanently_failed,
            "created_at": request.created_at.isoformat() if request.created_at else None,
            "updated_at": request.updated_at.isoformat() if request.updated_at else None,
            "responses": [
                {
                    "id": response.id,
                    "response_data": response.response_data,
                    "status_code": response.status_code,
                    "created_at": response.created_at.isoformat() if response.created_at else None
                }
                for response in responses or []
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving SMS request {request_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get(
    "/stats",
    summary="Get SMS service statistics",
    description="Get comprehensive statistics about SMS requests, responses, and provider performance",
    response_description="SMS service statistics"
)
async def get_sms_stats() -> Dict[str, Any]:
    """
    Get comprehensive statistics about the SMS service.

    Returns:
        Statistics including request counts, status breakdowns, and provider performance
    """
    try:
        sms_request_repo = get_sms_request_repository()
        sms_response_repo = get_sms_response_repository()

        # Get request statistics
        request_stats = sms_request_repo.get_request_stats()

        # Get recent responses count (last hour)
        recent_responses = len(sms_response_repo.get_responses_by_time_range(
            datetime.utcnow() - timedelta(hours=1), datetime.utcnow()
        ))

        return {
            "requests": request_stats,
            "recent_responses": recent_responses,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Error retrieving SMS stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.post(
    "/distribution-stats/reset",
    response_model=Dict[str, Any],
    summary="Reset distribution statistics",
    description="Reset distribution statistics (for testing purposes)",
    response_description="Reset confirmation"
)
async def reset_distribution_stats(
    distribution_service=Depends(get_distribution_service)
) -> Dict[str, Any]:
    """
    Reset distribution statistics.

    Returns:
        Reset confirmation
    """
    await distribution_service.reset_stats()
    return {
        "success": True,
        "message": "Distribution statistics reset"
    }


@router.get(
    "/queue-status",
    summary="Get queue status",
    description="Get current queue status and configuration",
    response_description="Queue status information",
)
async def get_queue_status() -> Dict[str, Any]:
    """
    Get current queue status and configuration.

    Returns:
        Queue status information
    """
    return {
        "status": "active",
        "queue_type": "redis_taskiq",
        "providers_configured": 3,
        "max_concurrent_workers": 20,
        "task_retry_limit": 5,
    }