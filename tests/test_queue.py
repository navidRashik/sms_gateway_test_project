"""
Tests for SMS queue functionality.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.queue import router, SMSRequest, SMSResponse, get_rate_limits
from src.rate_limiter import RateLimiter, GlobalRateLimiter


@pytest.fixture
def app():
    """Create FastAPI test app."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/sms")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock()
    return redis


@pytest.fixture
def mock_rate_limiter(mock_redis):
    """Create mock rate limiter."""
    limiter = AsyncMock(spec=RateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


@pytest.fixture
def mock_global_rate_limiter(mock_redis):
    """Create mock global rate limiter."""
    limiter = AsyncMock(spec=GlobalRateLimiter)
    limiter.is_allowed.return_value = (True, 1)
    return limiter


class TestSMSRequest:
    """Test SMS request validation."""

    def test_valid_sms_request(self):
        """Test valid SMS request creation."""
        request = SMSRequest(phone="01921317475", text="Hello World!")
        assert request.phone == "01921317475"
        assert request.text == "Hello World!"

    def test_invalid_phone_too_short(self):
        """Test phone number too short."""
        with pytest.raises(ValueError):
            SMSRequest(phone="123", text="Hello")

    def test_invalid_phone_too_long(self):
        """Test phone number too long."""
        with pytest.raises(ValueError):
            SMSRequest(phone="1234567890123456", text="Hello")

    def test_empty_text(self):
        """Test empty text."""
        with pytest.raises(ValueError):
            SMSRequest(phone="01921317475", text="")

    def test_text_too_long(self):
        """Test text too long."""
        long_text = "x" * 161
        with pytest.raises(ValueError):
            SMSRequest(phone="01921317475", text=long_text)


class TestSMSQueueEndpoints:
    """Test SMS queue endpoints."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self, client, mock_redis, mock_rate_limiter, mock_global_rate_limiter):
        """Test successful SMS sending."""
        # Mock the dependencies
        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.queue.create_global_rate_limiter', return_value=mock_global_rate_limiter), \
             patch('src.tasks.queue_sms_task', return_value="msg_123456_abcdef"):

            response = client.post(
                "/api/sms/send",
                json={
                    "phone": "01921317475",
                    "text": "Hello World!"
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "message_id" in data
            assert data["queued"] is True

    @pytest.mark.asyncio
    async def test_send_sms_global_rate_limited(self, client, mock_redis, mock_global_rate_limiter):
        """Test SMS sending when globally rate limited."""
        # Mock global rate limiter to deny requests
        mock_global_rate_limiter.is_allowed.return_value = (False, 250)

        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter'), \
             patch('src.queue.create_global_rate_limiter', return_value=mock_global_rate_limiter):

            response = client.post(
                "/api/sms/send",
                json={
                    "phone": "01921317475",
                    "text": "Hello World!"
                }
            )

            assert response.status_code == 429
            data = response.json()
            assert "Global rate limit exceeded" in data["error"]

    @pytest.mark.asyncio
    async def test_send_sms_no_provider_available(self, client, mock_redis, mock_rate_limiter):
        """Test SMS sending when no provider available."""
        # Mock rate limiter to deny all providers
        mock_rate_limiter.is_allowed.return_value = (False, 60)

        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.queue.create_global_rate_limiter'), \
             patch('src.tasks.queue_sms_task', return_value=None):

            response = client.post(
                "/api/sms/send",
                json={
                    "phone": "01921317475",
                    "text": "Hello World!"
                }
            )

            assert response.status_code == 503
            data = response.json()
            assert "All SMS providers are currently rate limited" in data["error"]

    def test_send_sms_invalid_request(self, client):
        """Test SMS sending with invalid request."""
        response = client.post(
            "/api/sms/send",
            json={
                "phone": "123",  # Invalid phone
                "text": "Hello World!"
            }
        )

        assert response.status_code == 422  # Validation error

    def test_send_sms_missing_fields(self, client):
        """Test SMS sending with missing fields."""
        response = client.post(
            "/api/sms/send",
            json={
                "phone": "01921317475"
                # Missing text
            }
        )

        assert response.status_code == 422  # Validation error

    def test_get_rate_limits(self, client, mock_redis, mock_rate_limiter, mock_global_rate_limiter):
        """Test getting rate limit status."""
        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.queue.create_global_rate_limiter', return_value=mock_global_rate_limiter):

            response = client.get("/api/sms/rate-limits")

            assert response.status_code == 200
            data = response.json()
            assert "provider_limited" in data
            assert "global_limited" in data
            assert "provider_count" in data
            assert "global_count" in data

    def test_get_queue_status(self, client):
        """Test getting queue status."""
        response = client.get("/api/sms/queue-status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert data["queue_type"] == "redis_taskiq"
        assert data["providers_configured"] == 3


class TestSMSQueueLogic:
    """Test SMS queue business logic."""

    @pytest.mark.asyncio
    async def test_queue_sms_task_success(self, mock_redis, mock_rate_limiter, mock_global_rate_limiter):
        """Test successful SMS task queueing."""
        with patch('src.tasks.select_best_provider', return_value=("provider1", "http://provider1:8071/api/sms/provider1")), \
             patch('src.tasks.send_sms_to_provider.kicker') as mock_send_task:

            from src.tasks import queue_sms_task

            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

            assert message_id is not None
            mock_send_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_sms_task_no_provider(self, mock_redis, mock_rate_limiter, mock_global_rate_limiter):
        """Test SMS task queueing when no provider available."""
        with patch('src.tasks.select_best_provider', return_value=None):
            from src.tasks import queue_sms_task

            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

            assert message_id is None


class TestIntegration:
    """Integration tests."""

    @pytest.mark.integration
    def test_full_sms_flow_integration(self):
        """Test complete SMS flow with real Redis."""
        # This would test the full integration
        # Skip for now as it requires full infrastructure
        pytest.skip("Integration test requires full infrastructure setup")