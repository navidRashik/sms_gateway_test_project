"""
SMS Provider endpoints for direct provider access.

This module provides FastAPI endpoints for direct access to SMS providers,
bypassing the queue system for cases where immediate sending is needed.
"""

import logging
from typing import Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, status

from .config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Provider configurations
PROVIDERS = {
    "provider1": {
        "url": settings.provider1_url,
        "description": "SMS Provider 1"
    },
    "provider2": {
        "url": settings.provider2_url,
        "description": "SMS Provider 2"
    },
    "provider3": {
        "url": settings.provider3_url,
        "description": "SMS Provider 3"
    }
}


@router.post(
    "/provider1",
    response_model=Dict[str, Any],
    summary="Send SMS via Provider 1",
    description="Direct SMS sending through Provider 1 (bypasses queue system)",
    response_description="SMS send result"
)
async def send_sms_provider1(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send SMS directly through Provider 1.

    Args:
        request: SMS request with phone and text fields

    Returns:
        Provider response

    Raises:
        HTTPException: If request is invalid or provider fails
    """
    return await _send_to_provider("provider1", request)


@router.post(
    "/provider2",
    response_model=Dict[str, Any],
    summary="Send SMS via Provider 2",
    description="Direct SMS sending through Provider 2 (bypasses queue system)",
    response_description="SMS send result"
)
async def send_sms_provider2(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send SMS directly through Provider 2.

    Args:
        request: SMS request with phone and text fields

    Returns:
        Provider response

    Raises:
        HTTPException: If request is invalid or provider fails
    """
    return await _send_to_provider("provider2", request)


@router.post(
    "/provider3",
    response_model=Dict[str, Any],
    summary="Send SMS via Provider 3",
    description="Direct SMS sending through Provider 3 (bypasses queue system)",
    response_description="SMS send result"
)
async def send_sms_provider3(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send SMS directly through Provider 3.

    Args:
        request: SMS request with phone and text fields

    Returns:
        Provider response

    Raises:
        HTTPException: If request is invalid or provider fails
    """
    return await _send_to_provider("provider3", request)


async def _send_to_provider(provider_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send SMS to specified provider.

    Args:
        provider_id: Provider identifier
        request: SMS request data

    Returns:
        Provider response

    Raises:
        HTTPException: If validation fails or provider is unavailable
    """
    # Validate request
    if not isinstance(request, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request must be a JSON object"
        )

    phone = request.get("phone")
    text = request.get("text")

    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number is required"
        )

    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMS text is required"
        )

    # Get provider configuration
    if provider_id not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not found"
        )

    provider_config = PROVIDERS[provider_id]
    provider_url = provider_config["url"]

    # Send request to provider
    try:
        logger.info(f"Sending direct SMS to {provider_id}: {phone}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{provider_url}/{provider_id}",
                json=request,
                headers={"Content-Type": "application/json"}
            )

            # Log response
            if response.status_code == 200:
                logger.info(f"SMS sent successfully to {provider_id}: {phone}")
            else:
                logger.warning(f"SMS failed for {provider_id}: {phone} - HTTP {response.status_code}")

            # Return response as-is from provider
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException:
        logger.error(f"Timeout sending SMS to {provider_id}: {phone}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Provider request timeout"
        )

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error from {provider_id}: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Provider error: {e.response.text}"
        )

    except Exception as e:
        logger.error(f"Unexpected error sending to {provider_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error: {str(e)}"
        )


@router.get(
    "/providers",
    response_model=Dict[str, Any],
    summary="List available SMS providers",
    description="Get information about available SMS providers",
    response_description="List of providers"
)
async def list_providers() -> Dict[str, Any]:
    """
    List all available SMS providers.

    Returns:
        Dictionary with provider information
    """
    return {
        "providers": [
            {
                "id": provider_id,
                "url": config["url"],
                "description": config["description"]
            }
            for provider_id, config in PROVIDERS.items()
        ],
        "count": len(PROVIDERS)
    }


@router.get(
    "/provider1/status",
    response_model=Dict[str, Any],
    summary="Get Provider 1 status",
    description="Get current status and statistics for Provider 1",
    response_description="Provider status"
)
async def get_provider1_status() -> Dict[str, Any]:
    """Get status for Provider 1."""
    return await _get_provider_status("provider1")


@router.get(
    "/provider2/status",
    response_model=Dict[str, Any],
    summary="Get Provider 2 status",
    description="Get current status and statistics for Provider 2",
    response_description="Provider status"
)
async def get_provider2_status() -> Dict[str, Any]:
    """Get status for Provider 2."""
    return await _get_provider_status("provider2")


@router.get(
    "/provider3/status",
    response_model=Dict[str, Any],
    summary="Get Provider 3 status",
    description="Get current status and statistics for Provider 3",
    response_description="Provider status"
)
async def get_provider3_status() -> Dict[str, Any]:
    """Get status for Provider 3."""
    return await _get_provider_status("provider3")


async def _get_provider_status(provider_id: str) -> Dict[str, Any]:
    """
    Get status information for a specific provider.

    Args:
        provider_id: Provider identifier

    Returns:
        Provider status information
    """
    if provider_id not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not found"
        )

    provider_config = PROVIDERS[provider_id]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{provider_config['url']}/{provider_id}/stats",
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                stats = response.json()
                return {
                    "provider_id": provider_id,
                    "status": "healthy",
                    "stats": stats,
                    "url": provider_config["url"]
                }
            else:
                return {
                    "provider_id": provider_id,
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}",
                    "url": provider_config["url"]
                }

    except Exception as e:
        return {
            "provider_id": provider_id,
            "status": "unreachable",
            "error": str(e),
            "url": provider_config["url"]
        }