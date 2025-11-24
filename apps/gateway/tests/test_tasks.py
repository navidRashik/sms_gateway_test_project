"""
Tests for Taskiq tasks and SMS processing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks import (
    send_sms_to_provider,
    get_available_providers,
    select_best_provider,
    queue_sms_task,
)


@pytest.fixture
def mock_rate_limiter():
    """Create mock rate limiter."""
    limiter = AsyncMock()
    limiter.is_allowed.return_value = (True, 1)
    return limiter


@pytest.fixture
def mock_global_rate_limiter():
    """Create mock global rate limiter."""
    limiter = AsyncMock()
    limiter.is_allowed.return_value = (True, 1)
    return limiter


class TestSendSMSToProvider:
    """Test SMS sending task."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self):
        """Test successful SMS sending."""
        # Mock successful HTTP response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "message_id": "msg_123"
        }

        with patch('httpx.AsyncClient.post') as mock_post:
            mock_post.return_value = mock_response

            result = await send_sms_to_provider(
                provider_url="http://provider1:8071/api/sms",
                phone="01921317475",
                text="Hello World!",
                message_id="msg_123",
                provider_id="provider1"
            )

            assert result["success"] is True
            assert result["message_id"] == "msg_123"
            assert result["provider"] == "provider1"

    @pytest.mark.asyncio
    async def test_send_sms_timeout_schedules_retry(self):
        """On timeout, the task should schedule a retry via dispatch task, not retry HTTP inline."""
        # Mock timeout exception specific to httpx
        import httpx

        timeout_exception = httpx.TimeoutException("Timeout")

        with (
            patch("httpx.AsyncClient.post", side_effect=timeout_exception) as mock_post,
            patch("src.tasks.dispatch_sms") as mock_dispatch_task,
        ):
            # Mock the kiq().schedule_by_time chain
            mock_task = AsyncMock()
            mock_task.schedule_by_time = AsyncMock()
            mock_dispatch_task.kiq.return_value = mock_task

            result = await send_sms_to_provider(
                provider_url="http://provider1:8071/api/sms",
                phone="01921317475",
                text="Hello World!",
                message_id="msg_123",
                provider_id="provider1",
                retry_count=0,
            )

            # Only one HTTP attempt happened
            assert mock_post.call_count == 1
            # Retry scheduled through dispatch task
            assert result["success"] is False
            assert result.get("retry_scheduled") is True
            assert mock_dispatch_task.kiq.called

    @pytest.mark.asyncio
    async def test_send_sms_max_retries_exceeded(self):
        """Test SMS failure after max retries returns error without scheduling."""
        # Mock persistent failure
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        with (
            patch("httpx.AsyncClient.post", return_value=mock_response),
            patch(
                "src.tasks.dispatch_sms.kiq", new_callable=AsyncMock
            ) as mock_dispatch_kiq,
        ):
            result = await send_sms_to_provider(
                provider_url="http://provider1:8071/api/sms",
                phone="01921317475",
                text="Hello World!",
                message_id="msg_123",
                provider_id="provider1",
                retry_count=5,  # Already at max retries
            )

            assert result["success"] is False
            assert "HTTP 500" in result["error"]
            assert result["retry_count"] == 5
            # No scheduling when max retries already reached
            assert not mock_dispatch_kiq.called


class TestProcessSMSBatch:
    """Test SMS batch processing."""


class TestProviderSelection:
    """Test provider selection logic."""

    @pytest.mark.asyncio
    async def test_get_available_providers(self):
        """Test getting available providers."""
        providers = await get_available_providers()
        assert "provider1" in providers
        assert "provider2" in providers
        assert "provider3" in providers
        assert len(providers) == 3

    @pytest.mark.asyncio
    async def test_select_best_provider_available(
        self, mock_rate_limiter, mock_global_rate_limiter
    ):
        """Test selecting best provider when available."""

        # Mock provider1 as available
        async def mock_is_allowed_provider(provider_id):
            if provider_id == "provider1":
                return (True, 10)
            return (False, 60)

        mock_rate_limiter.is_allowed.side_effect = mock_is_allowed_provider
        result = await select_best_provider(mock_rate_limiter, mock_global_rate_limiter)
        assert result is not None
        provider_id, provider_url = result
        assert provider_id == "provider1"
        assert "provider1" in provider_url

    @pytest.mark.asyncio
    async def test_select_best_provider_none_available(
        self, mock_rate_limiter, mock_global_rate_limiter
    ):
        """Test no provider available."""

        # Mock all providers rate limited
        async def mock_is_allowed_provider(provider_id):
            return (False, 60)

        mock_rate_limiter.is_allowed.side_effect = mock_is_allowed_provider
        result = await select_best_provider(mock_rate_limiter, mock_global_rate_limiter)
        assert result is None

    @pytest.mark.asyncio
    async def test_select_best_provider_global_limited(
        self, mock_rate_limiter, mock_global_rate_limiter
    ):
        """Test global rate limit prevents selection."""
        # Mock global rate limit exceeded
        mock_global_rate_limiter.is_allowed.return_value = (False, 250)
        result = await select_best_provider(mock_rate_limiter, mock_global_rate_limiter)
        assert result is None


class TestQueueSMS:
    """Test SMS queueing functionality."""

    @pytest.mark.asyncio
    async def test_queue_sms_task_success(
        self, mock_rate_limiter, mock_global_rate_limiter
    ):
        """Test successful SMS queueing enqueues dispatch task."""

        with patch(
            "src.tasks.dispatch_sms.kiq", new_callable=AsyncMock
        ) as mock_dispatch_kiq:
            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

    @pytest.mark.asyncio
    async def test_queue_sms_task_enqueues_dispatch(
        self, mock_rate_limiter, mock_global_rate_limiter
    ):
        """Queueing should enqueue dispatch regardless of immediate provider availability."""
        with patch(
            "src.tasks.dispatch_sms.kiq", new_callable=AsyncMock
        ) as mock_dispatch_kiq:
            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

            assert message_id is not None
            mock_dispatch_kiq.assert_called_once()


class TestTaskiqIntegration:
    """Test Taskiq broker integration."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_taskiq_broker_integration(self):
        """Test Taskiq broker with real Redis."""
        # Skip if Redis not available
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url("redis://localhost:6379")
            await redis_client.ping()
        except Exception:
            pytest.skip("Redis not available for integration tests")

        # Test broker creation and basic functionality
        from src.tasks import broker

        # This would test actual task queueing
        # For now, just verify broker exists
        assert broker is not None

        await redis_client.close()