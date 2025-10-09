import pytest
from unittest.mock import AsyncMock

from src.retry_service import RetryService

@pytest.mark.asyncio
async def test_dead_letter_entry_after_exhausting_retries(monkeypatch, mock_redis):
    """
    Verify that after exhausting retries, mark_request_permanently_failed
    triggers the status update to mark the request as permanently failed.
    """
    health = AsyncMock()
    health.is_provider_healthy = AsyncMock(return_value=True)

    rs = RetryService(
        redis_client=mock_redis,
        db_session=None,
        health_tracker=health,
        max_retries=2,
        base_delay=1.0,
        max_delay=300.0,
        jitter=False,
    )

    # Patch update_request_retry_status to capture its invocation
    called = {}

    async def fake_update_request_retry_status(request_id, retry_count, failed_providers, is_permanently_failed=False):
        called["request_id"] = request_id
        called["retry_count"] = retry_count
        called["is_permanently_failed"] = is_permanently_failed
        return True

    monkeypatch.setattr(rs, "update_request_retry_status", fake_update_request_retry_status)

    # Simulate exhausting retries by directly calling mark_request_permanently_failed
    result = await rs.mark_request_permanently_failed(request_id=999)

    assert result is True
    assert called.get("request_id") == 999
    assert called.get("retry_count") == rs.max_retries
    assert called.get("is_permanently_failed") is True