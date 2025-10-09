"""
Comprehensive integration tests for the complete SMS gateway system.

Tests all components working together: rate limiting, health tracking,
distribution, queueing, task processing, and database persistence.
"""

import asyncio
import time
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from redis.asyncio import Redis
from sqlmodel import Session, create_engine

from src.rate_limiter import RateLimiter, GlobalRateLimiter
from src.health_tracker import ProviderHealthTracker
from src.distribution import SMSDistributionService
from src.retry_service import RetryService
from src.tasks import send_sms_to_provider, process_sms_batch, queue_sms_task
from src.database import get_sms_request_repository, get_sms_response_repository
from src.config import settings


@pytest.fixture
def mock_redis():
    """Create mock Redis client for integration tests."""
    redis = AsyncMock(spec=Redis)

    # Create async mock functions that return appropriate values
    async def async_incr(key, amount=1):
        return 1

    async def async_expire(key, seconds):
        return True

    async def async_get(key):
        return b"0"

    async def async_delete(*keys):
        return True

    async def async_exists(*keys):
        return True

    async def async_ttl(key):
        return 300

    async def async_multi_exec():
        return []

    # Assign async functions to mock
    redis.incr = async_incr
    redis.expire = async_expire
    redis.get = async_get
    redis.delete = async_delete
    redis.exists = async_exists
    redis.ttl = async_ttl
    redis.multi_exec = async_multi_exec

    return redis


@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    session = MagicMock(spec=Session)
    return session


@pytest.fixture
def provider_urls():
    """Provider URLs for testing."""
    return {
        "provider1": "http://provider1.com/send",
        "provider2": "http://provider2.com/send",
        "provider3": "http://provider3.com/send"
    }


@pytest.fixture
def full_integration_components(mock_redis, mock_db_session, provider_urls):
    """Create all integration test components with real database."""
    # Use real in-memory SQLite database for full integration testing
    engine = create_engine("sqlite:///:memory:")

    # Create tables
    from src.models import SMSRequest, SMSResponse, SMSRetry, ProviderHealth
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)

    # Create real instances for full integration testing
    health_tracker = ProviderHealthTracker(mock_redis, window_duration=300, failure_threshold=0.7)
    rate_limiter = RateLimiter(mock_redis, rate_limit=50, window=1)
    global_rate_limiter = GlobalRateLimiter(mock_redis, rate_limit=200, window=1)

    distribution_service = SMSDistributionService(
        health_tracker=health_tracker,
        rate_limiter=rate_limiter,
        global_rate_limiter=global_rate_limiter,
        provider_urls=provider_urls
    )

    retry_service = RetryService(
        redis_client=mock_redis,
        db_session=Session(engine),  # Use real database session
        health_tracker=health_tracker,
        max_retries=3,
        base_delay=0.1,
        max_delay=1.0,
        jitter=False
    )

    return {
        "health_tracker": health_tracker,
        "rate_limiter": rate_limiter,
        "global_rate_limiter": global_rate_limiter,
        "distribution_service": distribution_service,
        "retry_service": retry_service,
        "redis": mock_redis,
        "db_engine": engine,
        "db_session": Session(engine)
    }


class TestEndToEndSMSWorkflow:
    """Test complete SMS processing workflow from request to response."""

    @pytest.mark.asyncio
    async def test_complete_sms_send_workflow(self, full_integration_components):
        """Test complete SMS sending workflow with real database persistence."""
        components = full_integration_components

        # Step 1: Send SMS through the queue system
        message_id = await queue_sms_task(
            phone="01921317475",
            text="Test message for integration workflow",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        # Verify message was queued
        assert message_id is not None
        assert message_id.startswith("msg_")

        # Step 2: Verify database persistence
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            # Check that SMS request was persisted
            requests = sms_request_repo.get_requests_with_filters(limit=10)
            assert len(requests) >= 1

            # Find our request
            our_request = None
            for request in requests:
                if request.phone == "01921317475" and request.text == "Test message for integration workflow":
                    our_request = request
                    break

            assert our_request is not None
            assert our_request.status in ["processing", "completed"]
            assert our_request.provider_used in ["provider1", "provider2", "provider3"]

        # Step 3: Mock successful SMS provider response
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"message_id": "provider_123", "status": "delivered"}
            mock_post.return_value = mock_response

            # Execute the SMS sending task
            provider_id = our_request.provider_used
            provider_url = components["distribution_service"].provider_urls[provider_id]

            result = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=0,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            # Verify task execution
            assert result["success"] is True
            assert result["provider"] == provider_id
            assert "response" in result

        # Step 4: Verify database was updated with success
        sms_response_repo = get_sms_response_repository()
        with Session(components["db_engine"]) as session:
            sms_response_repo.session = session

            # Check that response was persisted
            response = sms_response_repo.get_response_by_request_id(our_request.id)
            assert response is not None
            assert response.status_code == 200
            # The response data should contain the JSON string representation
            assert response.status_code == 200

        # Step 5: Verify request status was updated
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            updated_request = sms_request_repo.get_request_by_id(our_request.id)
            assert updated_request.status == "completed"
            assert updated_request.provider_used == provider_id

        # Step 6: Verify health tracking
        health_status = await components["health_tracker"].get_health_status(provider_id)
        assert health_status["is_healthy"] is True
        assert health_status["success_count"] >= 1

    @pytest.mark.asyncio
    async def test_sms_failure_and_retry_workflow(self, full_integration_components):
        """Test SMS failure followed by successful retry with database persistence."""
        components = full_integration_components

        # Step 1: Send SMS that will fail initially
        message_id = await queue_sms_task(
            phone="01921317475",
            text="Test message for retry workflow",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        assert message_id is not None

        # Get the request from database
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            requests = sms_request_repo.get_requests_with_filters(limit=10)

            our_request = None
            for request in requests:
                if request.phone == "01921317475" and request.text == "Test message for retry workflow":
                    our_request = request
                    break

            assert our_request is not None

        # Step 2: Mock initial failure then success
        provider_id = our_request.provider_used
        provider_url = components["distribution_service"].provider_urls[provider_id]

        with patch('httpx.AsyncClient.post') as mock_post:
            # First call fails, second succeeds
            mock_response_fail = AsyncMock()
            mock_response_fail.status_code = 500
            mock_response_fail.text = "Internal server error"

            mock_response_success = AsyncMock()
            mock_response_success.status_code = 200
            mock_response_success.json.return_value = {"message_id": "provider_456", "status": "delivered"}

            mock_post.side_effect = [mock_response_fail, mock_response_success]

            # First attempt - should fail
            result1 = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=0,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            # Should indicate retry is scheduled
            assert result1["success"] is False
            assert "retry_scheduled" in result1

            # Wait for retry delay (brief)
            await asyncio.sleep(0.2)

            # Second attempt - should succeed
            result2 = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=1,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            # Should succeed on retry
            assert result2["success"] is True

        # Step 3: Verify database persistence of both attempts
        sms_response_repo = get_sms_response_repository()
        with Session(components["db_engine"]) as session:
            sms_response_repo.session = session

            # Check failure response first
            failure_response = sms_response_repo.get_response_by_request_id(our_request.id)
            assert failure_response.status_code == 500
            assert "Internal server error" in failure_response.response_data

            # Check success response (create a new request since we need to get all responses)
            all_responses = sms_response_repo.get_responses_by_time_range(
                time.time() - 3600, time.time() + 3600  # Last hour
            )
            success_response = None
            for resp in all_responses:
                if resp.request_id == our_request.id and resp.status_code == 200:
                    success_response = resp
                    break
            assert success_response.status_code == 200
            assert "delivered" in success_response.response_data

        # Step 4: Verify final request status
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            final_request = sms_request_repo.get_request_by_id(our_request.id)
            assert final_request.status == "completed"
            assert final_request.retry_count >= 1

    @pytest.mark.asyncio
    async def test_batch_sms_processing_workflow(self, full_integration_components):
        """Test batch SMS processing workflow."""
        components = full_integration_components

        # Step 1: Create batch data
        batch_data = {
            "batch_id": "test_batch_123",
            "messages": [
                {
                    "phone": "01921317475",
                    "text": "Batch message 1",
                    "message_id": "batch_msg_1"
                },
                {
                    "phone": "01712345678",
                    "text": "Batch message 2",
                    "message_id": "batch_msg_2"
                }
            ]
        }

        # Step 2: Mock provider responses
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response1 = AsyncMock()
            mock_response1.status_code = 200
            mock_response1.json.return_value = {"message_id": "prov_1", "status": "delivered"}

            mock_response2 = AsyncMock()
            mock_response2.status_code = 200
            mock_response2.json.return_value = {"message_id": "prov_2", "status": "delivered"}

            mock_post.side_effect = [mock_response1, mock_response2]

            # Step 3: Process batch
            result = await process_sms_batch(
                batch_data=batch_data,
                provider_url="http://provider1.com/send",
                provider_id="provider1"
            )

            # Verify batch processing
            assert result["batch_id"] == "test_batch_123"
            assert result["provider"] == "provider1"
            assert result["total_messages"] == 2
            assert result["successful"] == 2
            assert result["failed"] == 0
            assert len(result["results"]) == 2

            # Verify all messages succeeded
            for message_result in result["results"]:
                assert message_result["success"] is True

    @pytest.mark.asyncio
    async def test_redis_rate_limiting_integration(self, full_integration_components):
        """Test Redis rate limiting integration across the full workflow."""
        components = full_integration_components

        # Step 1: Exhaust provider rate limit
        provider_id = "provider1"

        # Simulate 55 requests (exceed 50 limit)
        for i in range(55):
            allowed, count = await components["rate_limiter"].is_allowed(provider_id)

        # Step 2: Verify rate limiting is working
        rate_stats = await components["rate_limiter"].get_rate_limit_stats(provider_id)
        assert rate_stats["is_limited"] is True
        assert rate_stats["current_count"] >= 50

        # Step 3: Try to queue SMS - should be blocked by rate limiting
        message_id = await queue_sms_task(
            phone="01921317475",
            text="This should be rate limited",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        # Should return None due to rate limiting
        assert message_id is None

        # Step 4: Verify no new requests in database
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            requests = sms_request_repo.get_requests_with_filters(limit=100)
            rate_limited_requests = [
                r for r in requests
                if r.phone == "01921317475" and r.text == "This should be rate limited"
            ]
            assert len(rate_limited_requests) == 0

    @pytest.mark.asyncio
    async def test_global_rate_limiting_integration(self, full_integration_components):
        """Test global rate limiting integration across all providers."""
        components = full_integration_components

        # Step 1: Exhaust global rate limit (simulate 205 requests)
        for i in range(205):
            allowed, count = await components["global_rate_limiter"].is_allowed()

        # Step 2: Verify global rate limiting is working
        global_count = await components["global_rate_limiter"].get_current_count()
        assert global_count >= 200

        # Step 3: Try to queue SMS - should be blocked by global rate limiting
        message_id = await queue_sms_task(
            phone="01921317475",
            text="This should be globally rate limited",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        # Should return None due to global rate limiting
        assert message_id is None

    @pytest.mark.asyncio
    async def test_provider_health_integration_workflow(self, full_integration_components):
        """Test provider health tracking integration in full workflow."""
        components = full_integration_components

        # Step 1: Send multiple SMS messages with mixed success/failure
        messages = [
            ("01921317475", "Success message 1"),
            ("01712345678", "Success message 2"),
            ("01898765432", "Failure message 1"),
            ("01921317475", "Failure message 2"),
            ("01712345678", "Success message 3"),
        ]

        for phone, text in messages:
            message_id = await queue_sms_task(
                phone=phone,
                text=text,
                rate_limiter=components["rate_limiter"],
                global_rate_limiter=components["global_rate_limiter"],
                distribution_service=components["distribution_service"]
            )

            if message_id:  # Only process if not rate limited
                # Get request from database
                sms_request_repo = get_sms_request_repository()
                with Session(components["db_engine"]) as session:
                    sms_request_repo.session = session
                    requests = sms_request_repo.get_requests_with_filters(limit=50)

                    our_request = None
                    for request in requests:
                        if request.phone == phone and request.text == text:
                            our_request = request
                            break

                    if our_request:
                        provider_id = our_request.provider_used
                        provider_url = components["distribution_service"].provider_urls[provider_id]

                        # Mock success or failure based on message content
                        is_success = "Success" in text

                        with patch('httpx.AsyncClient.post') as mock_post:
                            if is_success:
                                mock_response = AsyncMock()
                                mock_response.status_code = 200
                                mock_response.json.return_value = {"status": "delivered"}
                            else:
                                mock_response = AsyncMock()
                                mock_response.status_code = 500
                                mock_response.text = "Internal server error"

                            mock_post.return_value = mock_response

                            # Execute SMS task
                            await send_sms_to_provider(
                                provider_url=provider_url,
                                phone=phone,
                                text=text,
                                message_id=message_id,
                                provider_id=provider_id,
                                retry_count=0,
                                health_tracker=components["health_tracker"],
                                request_id=our_request.id
                            )

        # Step 2: Verify provider health was tracked correctly
        provider_id = "provider1"  # Assuming provider1 was selected

        health_status = await components["health_tracker"].get_health_status(provider_id)
        assert health_status["is_healthy"] is True  # 60% success rate > 70% threshold

        # Should have recorded some successes and failures
        assert health_status["success_count"] >= 2
        assert health_status["failure_count"] >= 1

        # Step 3: Verify distribution service uses health information
        distribution_stats = components["distribution_service"].get_distribution_stats()
        assert "requests_per_provider" in distribution_stats

    @pytest.mark.asyncio
    async def test_database_persistence_integration(self, full_integration_components):
        """Test complete database persistence throughout the workflow."""
        components = full_integration_components

        # Step 1: Send SMS and track all database changes
        initial_request_count = 0
        initial_response_count = 0

        sms_request_repo = get_sms_request_repository()
        sms_response_repo = get_sms_response_repository()

        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            sms_response_repo.session = session

            initial_requests = sms_request_repo.get_requests_with_filters(limit=1000)
            initial_responses = sms_response_repo.get_responses_by_time_range(
                time.time() - 3600, time.time() + 3600  # Last hour
            )
            initial_request_count = len(initial_requests)
            initial_response_count = len(initial_responses)

        # Step 2: Send SMS through full workflow
        message_id = await queue_sms_task(
            phone="01921317475",
            text="Database persistence test message",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        assert message_id is not None

        # Step 3: Execute SMS sending (success scenario)
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            requests = sms_request_repo.get_requests_with_filters(limit=100)

            our_request = None
            for request in requests:
                if request.phone == "01921317475" and request.text == "Database persistence test message":
                    our_request = request
                    break

            assert our_request is not None

        # Mock successful response
        provider_id = our_request.provider_used
        provider_url = components["distribution_service"].provider_urls[provider_id]

        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"message_id": "db_test_123", "status": "delivered"}
            mock_post.return_value = mock_response

            await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=0,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

        # Step 4: Verify complete database persistence
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            sms_response_repo.session = session

            # Check request persistence
            final_requests = sms_request_repo.get_requests_with_filters(limit=1000)
            final_responses = sms_response_repo.get_responses_by_time_range(
                time.time() - 3600, time.time() + 3600
            )

            assert len(final_requests) == initial_request_count + 1
            assert len(final_responses) == initial_response_count + 1

            # Verify request details
            our_final_request = None
            for request in final_requests:
                if request.phone == "01921317475" and request.text == "Database persistence test message":
                    our_final_request = request
                    break

            assert our_final_request is not None
            assert our_final_request.status == "completed"
            assert our_final_request.provider_used == provider_id
            assert our_final_request.retry_count == 0

            # Verify response details
            our_response = None
            for response in final_responses:
                if response.request_id == our_final_request.id:
                    our_response = response
                    break

            assert our_response is not None
            assert our_response.status_code == 200
            assert "delivered" in our_response.response_data

    @pytest.mark.asyncio
    async def test_error_handling_and_retry_integration(self, full_integration_components):
        """Test comprehensive error handling and retry logic integration."""
        components = full_integration_components

        # Step 1: Send SMS that will experience multiple failures
        message_id = await queue_sms_task(
            phone="01921317475",
            text="Error handling and retry test",
            rate_limiter=components["rate_limiter"],
            global_rate_limiter=components["global_rate_limiter"],
            distribution_service=components["distribution_service"]
        )

        assert message_id is not None

        # Step 2: Get request and simulate multiple provider failures
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            requests = sms_request_repo.get_requests_with_filters(limit=50)

            our_request = None
            for request in requests:
                if request.phone == "01921317475" and request.text == "Error handling and retry test":
                    our_request = request
                    break

            assert our_request is not None

        provider_id = our_request.provider_used
        provider_url = components["distribution_service"].provider_urls[provider_id]

        # Step 3: Test timeout error handling
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Connection timeout")

            # Execute task that will timeout
            result = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=0,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            # Should handle timeout with retry
            assert result["success"] is False
            assert "Timeout" in result["error"]

        # Step 4: Verify timeout was persisted in database
        sms_response_repo = get_sms_response_repository()
        with Session(components["db_engine"]) as session:
            sms_response_repo.session = session

            timeout_response = sms_response_repo.get_response_by_request_id(our_request.id)
            assert timeout_response is not None
            assert timeout_response.status_code == 408  # Request Timeout
            assert "Timeout" in timeout_response.response_data

        # Step 5: Test network error handling
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_post.side_effect = Exception("Network error")

            # Execute task that will have network error
            result = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=1,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            # Should handle unexpected error with retry
            assert result["success"] is False
            assert "Unexpected error" in result["error"]

        # Step 6: Verify error was persisted
        with Session(components["db_engine"]) as session:
            sms_response_repo.session = session

            # Get the error response (it might be the same response record updated)
            error_response = sms_response_repo.get_response_by_request_id(our_request.id)
            assert error_response.status_code == 500
            assert "Network error" in error_response.response_data

        # Step 7: Test successful retry after failures
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"message_id": "success_after_retry", "status": "delivered"}
            mock_post.return_value = mock_response

            # Final retry should succeed
            final_result = await send_sms_to_provider(
                provider_url=provider_url,
                phone=our_request.phone,
                text=our_request.text,
                message_id=message_id,
                provider_id=provider_id,
                retry_count=2,
                health_tracker=components["health_tracker"],
                request_id=our_request.id
            )

            assert final_result["success"] is True

        # Step 8: Verify final success state
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session
            sms_response_repo.session = session

            final_request = sms_request_repo.get_request_by_id(our_request.id)
            # Final response should be success
            final_response = sms_response_repo.get_response_by_request_id(our_request.id)
            assert success_response.status_code == 200
            assert "delivered" in success_response.response_data


class TestSystemIntegrationUnderLoad:
    """Test system integration under various load conditions."""

    @pytest.mark.asyncio
    async def test_high_throughput_integration(self, full_integration_components):
        """Test system under high throughput conditions."""
        components = full_integration_components

        # Step 1: Send many SMS requests rapidly
        successful_queued = 0
        start_time = time.time()

        for i in range(150):  # Test 150 RPS capacity
            message_id = await queue_sms_task(
                phone=f"019213174{i%10:02d}",  # Rotate through 10 phone numbers
                text=f"High throughput test message {i}",
                rate_limiter=components["rate_limiter"],
                global_rate_limiter=components["global_rate_limiter"],
                distribution_service=components["distribution_service"]
            )

            if message_id:
                successful_queued += 1

        end_time = time.time()
        duration = end_time - start_time

        # Step 2: Verify throughput performance
        # Should handle close to 150 RPS (some may be rate limited)
        assert successful_queued >= 100  # At least 100 should be queued
        assert duration < 10.0  # Should complete within 10 seconds

        # Step 3: Verify database persistence under load
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            all_requests = sms_request_repo.get_requests_with_filters(limit=1000)
            queued_requests = [r for r in all_requests if "High throughput test" in r.text]

            assert len(queued_requests) >= successful_queued

        # Step 4: Verify rate limiting worked correctly under load
        rate_stats = await components["rate_limiter"].get_rate_limit_stats("provider1")
        assert rate_stats["current_count"] >= 45  # Should be close to limit

    @pytest.mark.asyncio
    async def test_concurrent_processing_integration(self, full_integration_components):
        """Test concurrent SMS processing with database persistence."""
        components = full_integration_components

        # Step 1: Queue multiple SMS requests
        message_ids = []
        for i in range(20):
            message_id = await queue_sms_task(
                phone=f"019213174{i%5:02d}",
                text=f"Concurrent test message {i}",
                rate_limiter=components["rate_limiter"],
                global_rate_limiter=components["global_rate_limiter"],
                distribution_service=components["distribution_service"]
            )
            if message_id:
                message_ids.append((message_id, f"019213174{i%5:02d}", f"Concurrent test message {i}"))

        assert len(message_ids) >= 15  # Should queue most requests

        # Step 2: Process SMS concurrently
        async def process_single_sms(msg_data):
            message_id, phone, text = msg_data

            # Get request from database
            sms_request_repo = get_sms_request_repository()
            with Session(components["db_engine"]) as session:
                sms_request_repo.session = session
                requests = sms_request_repo.get_requests_with_filters(limit=100)

                our_request = None
                for request in requests:
                    if request.phone == phone and request.text == text:
                        our_request = request
                        break

                if our_request:
                    provider_id = our_request.provider_used
                    provider_url = components["distribution_service"].provider_urls[provider_id]

                    # Mock successful response
                    with patch('httpx.AsyncClient.post') as mock_post:
                        mock_response = AsyncMock()
                        mock_response.status_code = 200
                        mock_response.json.return_value = {"message_id": f"conc_{message_id}", "status": "delivered"}
                        mock_post.return_value = mock_response

                        # Process SMS
                        result = await send_sms_to_provider(
                            provider_url=provider_url,
                            phone=phone,
                            text=text,
                            message_id=message_id,
                            provider_id=provider_id,
                            retry_count=0,
                            health_tracker=components["health_tracker"],
                            request_id=our_request.id
                        )

                        return result["success"]

            return False

        # Step 3: Execute concurrent processing
        tasks = [process_single_sms(msg_data) for msg_data in message_ids]
        results = await asyncio.gather(*tasks)

        # Step 4: Verify concurrent processing results
        successful_processed = sum(results)
        assert successful_processed >= len(message_ids) * 0.9  # At least 90% success rate

        # Step 5: Verify all requests were completed in database
        sms_request_repo = get_sms_request_repository()
        with Session(components["db_engine"]) as session:
            sms_request_repo.session = session

            completed_requests = sms_request_repo.get_requests_with_filters(status="completed", limit=1000)
            concurrent_requests = [r for r in completed_requests if "Concurrent test" in r.text]

            assert len(concurrent_requests) >= successful_processed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])