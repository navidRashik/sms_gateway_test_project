"""
Tests for SMS queue functionality.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from redis.asyncio import Redis

from src.queue import router, SMSRequest, SMSResponse
from src.rate_limiter import RateLimiter, GlobalRateLimiter
from src.models import SMSRequest as SMSRequestModel, SMSResponse as SMSResponseModel
from src.database import get_sms_request_repository, get_sms_response_repository


@pytest.fixture
def app():
    """Create FastAPI test app with proper state."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/sms")
    
    # Add mock Redis client to app state
    mock_redis = AsyncMock(spec=Redis)
    # Configure mock Redis methods
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.pexpire = AsyncMock(return_value=True)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value=b"1")
    mock_redis.rpush = AsyncMock(return_value=0)
    mock_redis.lpush = AsyncMock(return_value=0)
    mock_redis.delete = AsyncMock(return_value=True)
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.ttl = AsyncMock(return_value=0)
    
    test_app.state.redis = mock_redis
    
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


@pytest.fixture
def test_db_engine():
    """Create in-memory SQLite database engine for testing."""
    # Patch the global database engine to use in-memory SQLite for testing
    from src.database import _engine
    import src.database

    # Create in-memory SQLite engine
    engine = create_engine("sqlite:///:memory:")

    # Set the global engine to our test engine
    src.database._engine = engine

    # Create all tables in the global engine
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(engine)

    return engine


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
    async def test_send_sms_success(self, client, mock_redis, mock_rate_limiter, mock_global_rate_limiter, test_db_engine):
        """Test successful SMS sending."""
        # Mock the dependencies
        with (
            patch("src.queue.get_redis_client", return_value=mock_redis),
            patch("src.queue.create_rate_limiter", return_value=mock_rate_limiter),
            patch(
                "src.queue.create_global_rate_limiter",
                return_value=mock_global_rate_limiter,
            ),
            patch(
                "src.tasks.select_best_provider",
                return_value=("provider1", "http://provider1:8071/api/sms/provider1"),
            ),
            patch("src.tasks.send_sms_to_provider.kicker") as mock_send_task,
            patch("src.database.get_db_engine", return_value=test_db_engine),
            patch("src.database.get_sms_request_repository") as mock_get_request_repo,
            patch("src.database.get_sms_response_repository") as mock_get_response_repo,
            patch("src.database.get_sms_retry_repository") as mock_get_retry_repo,
            patch("src.database.get_provider_health_repository") as mock_get_health_repo,
            patch("src.database.initialize_database") as mock_initialize_database,
        ):
            
            # Create repository instances with the test engine
            from src.database import SMSRequestRepository, SMSResponseRepository, SMSRetryRepository, ProviderHealthRepository
            request_repo = SMSRequestRepository(engine=test_db_engine)
            response_repo = SMSResponseRepository(engine=test_db_engine)
            retry_repo = SMSRetryRepository(engine=test_db_engine)
            health_repo = ProviderHealthRepository(engine=test_db_engine)
            
            mock_get_request_repo.return_value = request_repo
            mock_get_response_repo.return_value = response_repo
            mock_get_retry_repo.return_value = retry_repo
            mock_get_health_repo.return_value = health_repo
            
            # Create all tables in the test engine directly
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)
            
            # Call the initialize_database function to ensure it doesn't interfere
            mock_initialize_database.return_value = None

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
    async def test_send_sms_global_rate_limited(self, client, mock_redis, mock_global_rate_limiter, test_db_engine):
        """Test SMS sending when globally rate limited."""
        # Mock global rate limiter to deny requests
        mock_global_rate_limiter.is_allowed.return_value = (False, 250)

        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter'), \
             patch('src.queue.create_global_rate_limiter', return_value=mock_global_rate_limiter), \
             patch('src.database.get_db_engine', return_value=test_db_engine):
             
            # Initialize the database with tables
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)

            response = client.post(
                "/api/sms/send",
                json={
                    "phone": "01921317475",
                    "text": "Hello World!"
                }
            )

            assert response.status_code == 429
            data = response.json()
            assert "Global rate limit exceeded" in data["detail"]["error"]

    @pytest.mark.asyncio
    async def test_send_sms_no_provider_available(self, client, mock_redis, mock_rate_limiter, test_db_engine):
        """Test SMS sending when no provider available."""
        # Mock rate limiter to deny all providers
        mock_rate_limiter.is_allowed.return_value = (False, 60)

        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.queue.create_global_rate_limiter'), \
             patch('src.tasks.queue_sms_task', return_value=None), \
             patch('src.database.get_db_engine', return_value=test_db_engine):
             
            # Initialize the database with tables
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)

            response = client.post(
                "/api/sms/send",
                json={
                    "phone": "01921317475",
                    "text": "Hello World!"
                }
            )

            assert response.status_code == 503

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

    def test_get_rate_limits(self, client, mock_redis, mock_rate_limiter, mock_global_rate_limiter, test_db_engine):
        """Test getting rate limit status."""
        with patch('src.queue.get_redis_client', return_value=mock_redis), \
             patch('src.queue.create_rate_limiter', return_value=mock_rate_limiter), \
             patch('src.queue.create_global_rate_limiter', return_value=mock_global_rate_limiter), \
             patch('src.database.get_db_engine', return_value=test_db_engine):
             
            # Initialize the database with tables
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)

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
    async def test_queue_sms_task_success(self, mock_redis, mock_rate_limiter, mock_global_rate_limiter, test_db_engine):
        """Test successful SMS task queueing."""
        with (
            patch("src.tasks.dispatch_sms.kiq") as mock_dispatch_kiq,
            patch("src.database.get_db_engine", return_value=test_db_engine),
        ):
             
            # Initialize the database with tables
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)

            from src.tasks import queue_sms_task

            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

            assert message_id is not None
            mock_dispatch_kiq.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_sms_task_enqueues_dispatch(
        self, mock_redis, mock_rate_limiter, mock_global_rate_limiter, test_db_engine
    ):
        """Queueing should always enqueue dispatch task regardless of immediate provider availability."""
        with (
            patch("src.tasks.dispatch_sms.kiq") as mock_dispatch_kiq,
            patch("src.database.get_db_engine", return_value=test_db_engine),
        ):
            # Initialize the database with tables
            from sqlmodel import SQLModel
            SQLModel.metadata.create_all(test_db_engine)

            from src.tasks import queue_sms_task

            message_id = await queue_sms_task(
                phone="01921317475",
                text="Hello World!",
                rate_limiter=mock_rate_limiter,
                global_rate_limiter=mock_global_rate_limiter
            )

            assert message_id is not None
            mock_dispatch_kiq.assert_called_once()


class TestIntegration:
    """Integration tests."""

    @pytest.mark.integration
    def test_full_sms_flow_integration(self):
        """Test complete SMS flow with real Redis."""
        # This would test the full integration
        # Skip for now as it requires full infrastructure
        pytest.skip("Integration test requires full infrastructure setup")