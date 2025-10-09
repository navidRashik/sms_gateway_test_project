import asyncio
import pytest

from unittest.mock import AsyncMock

from src.retry_service import RetryService

@pytest.mark.asyncio
async def test_retry_service_schedules_exponential_backoff_via_taskiq(monkeypatch, mock_redis):
    """
    Ensure exponential backoff calculation follows base * 2**attempt (no jitter).
    Patch asyncio.sleep to raise if called to ensure scheduling logic is not performing sleeps here.
    """
    # Patch asyncio.sleep to raise if called (ensures we don't actually sleep)
    async def _sleep_fail(*args, **kwargs):
        raise RuntimeError("asyncio.sleep should not be called in this test")

    monkeypatch.setattr(asyncio, "sleep", _sleep_fail)

    # Dummy health tracker that marks all providers healthy
    class DummyHealth:
        async def is_provider_healthy(self, _):
            return True

    rs = RetryService(
        redis_client=mock_redis,
        db_session=None,
        health_tracker=DummyHealth(),
        max_retries=5,
        base_delay=1.0,
        max_delay=300.0,
        jitter=False,  # deterministic delays
    )

    # attempt is 0-based in calculate_backoff_delay
    d0 = rs.calculate_backoff_delay(0)  # should be 1.0
    d1 = rs.calculate_backoff_delay(1)  # should be 2.0
    d2 = rs.calculate_backoff_delay(2)  # should be 4.0

    assert pytest.approx(d0, rel=1e-6) == 1.0
    assert pytest.approx(d1, rel=1e-6) == 2.0
    assert pytest.approx(d2, rel=1e-6) == 4.0


@pytest.mark.asyncio
async def test_dead_letter_queue_after_max_retries(monkeypatch, mock_redis):
    """
    Configure max_retries=1 and ensure that mark_request_permanently_failed
    invokes update_request_retry_status with is_permanently_failed=True.
    """
    # Dummy health tracker
    health = AsyncMock()
    health.is_provider_healthy = AsyncMock(return_value=True)

    rs = RetryService(
        redis_client=mock_redis,
        db_session=None,
        health_tracker=health,
        max_retries=1,
        base_delay=1.0,
        max_delay=300.0,
        jitter=False,
    )

    # Patch update_request_retry_status to capture calls
    called = {}

    async def fake_update_request_retry_status(request_id, retry_count, failed_providers, is_permanently_failed=False):
        called["request_id"] = request_id
        called["retry_count"] = retry_count
        called["failed_providers"] = failed_providers
        called["is_permanently_failed"] = is_permanently_failed
        return True

    monkeypatch.setattr(rs, "update_request_retry_status", fake_update_request_retry_status)

    # Call mark_request_permanently_failed which should call update_request_retry_status with is_permanently_failed True
    result = await rs.mark_request_permanently_failed(request_id=123)

    assert result is True
    assert called.get("request_id") == 123
    assert called.get("retry_count") == rs.max_retries
    assert called.get("is_permanently_failed") is True