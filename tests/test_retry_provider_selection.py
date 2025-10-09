import pytest
from unittest.mock import AsyncMock

from src.retry_service import RetryService

@pytest.mark.asyncio
async def test_retry_skips_last_failed_provider(mock_redis):
    """
    Simulate that provider2 previously failed for the request and ensure
    select_provider_round_robin excludes it from selection.
    """
    # Health tracker marks provider2 unhealthy; others healthy
    class HT:
        async def is_provider_healthy(self, provider_id):
            return provider_id != "provider2"

    ht = HT()

    rs = RetryService(
        redis_client=mock_redis,
        db_session=None,
        health_tracker=ht,
        max_retries=5,
        base_delay=1.0,
        max_delay=300.0,
        jitter=False,
    )

    # Exclude provider2 as if it failed previously
    selected = await rs.select_provider_round_robin(exclude_providers={"provider2"})
    assert selected is not None
    assert selected != "provider2"


@pytest.mark.asyncio
async def test_requeue_when_all_providers_unhealthy(mock_redis):
    """
    If all providers are unhealthy, selection should return None (no provider).
    """
    class HTAllUnhealthy:
        async def is_provider_healthy(self, _):
            return False

    ht = HTAllUnhealthy()

    rs = RetryService(
        redis_client=mock_redis,
        db_session=None,
        health_tracker=ht,
        max_retries=5,
        base_delay=1.0,
        max_delay=300.0,
        jitter=False,
    )

    selected = await rs.select_provider_round_robin(exclude_providers=None)
    assert selected is None