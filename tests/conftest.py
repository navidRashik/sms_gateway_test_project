"""
Shared test configuration and fixtures.
"""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis
from sqlmodel import SQLModel, create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_redis():
    """Create mock Redis client for all tests.

    Use AsyncMock for Redis async methods so they return awaitable values
    when awaited by async production code.
    """
    redis = AsyncMock(spec=Redis)

    # Ensure Redis methods used by production code are AsyncMock and return awaitable values.
    # .incr should be awaitable and return integers (1, 2, ...). Tests may override side_effect.
    redis.incr = AsyncMock(return_value=1)
    # Expiration and set helpers should be awaitable and return True
    redis.expire = AsyncMock(return_value=True)
    redis.pexpire = AsyncMock(return_value=True)
    redis.set = AsyncMock(return_value=True)
    redis.setex = AsyncMock(return_value=True)
    # .get returns bytes when awaited
    redis.get = AsyncMock(return_value=b"1")
    # List and queue helpers for dead-letter handling should be awaitable
    redis.rpush = AsyncMock(return_value=0)
    redis.lpush = AsyncMock(return_value=0)
    # Misc helpers
    redis.delete = AsyncMock(return_value=True)
    redis.exists = AsyncMock(return_value=False)
    redis.ttl = AsyncMock(return_value=0)

    return redis


@pytest.fixture
def sample_sms_request():
    """Sample SMS request data."""
    return {
        "phone": "01921317475",
        "text": "Hello, this is a test SMS message!"
    }


@pytest.fixture
def sample_phone_numbers():
    """Sample phone numbers for testing."""
    return [
        "01921317475",
        "01712345678",
        "01898765432",
        "+8801912345678"
    ]


@pytest.fixture
def sample_sms_texts():
    """Sample SMS texts for testing."""
    return [
        "Hello World!",
        "This is a test SMS message for testing purposes.",
        "SMS content with émojis 🚀 and spëcial characters.",
        "Short"
    ]


@pytest.fixture
def rate_limit_config():
    """Sample rate limit configuration."""
    return {
        "provider_rate_limit": 50,
        "global_rate_limit": 200,
        "window_seconds": 1
    }


@pytest.fixture
def test_db_engine():
    """Create database engine for testing using the actual database file."""
    # Use the same database as production but create tables
    from src.database import get_db_engine
    
    # Get the actual database engine
    engine = get_db_engine()
    
    # Create all tables
    SQLModel.metadata.create_all(engine)
    
    return engine


@pytest.fixture
def test_db_session(test_db_engine):
    """Create database session for testing."""
    session_local = sessionmaker(bind=test_db_engine, expire_on_commit=False)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
def rate_limit_config():
    """Sample rate limit configuration."""
    return {
        "provider_rate_limit": 50,
        "global_rate_limit": 200,
        "window_seconds": 1
    }


@pytest.fixture
def test_db_engine():
    """Create in-memory SQLite database engine for testing."""
    # Override the database URL for testing
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    
    # Import settings after overriding environment variable
    from src.config import settings
    
    # Create in-memory SQLite engine
    engine = create_engine("sqlite:///:memory:")
    
    # Create all tables
    SQLModel.metadata.create_all(engine)
    
    return engine


@pytest.fixture
def test_db_session(test_db_engine):
    """Create database session for testing."""
    session_local = sessionmaker(bind=test_db_engine, expire_on_commit=False)
    session = session_local()
    try:
        yield session
    finally:
        session.close()