"""
Database service layer for SMS Service using SQLModel.

This module provides:
- Database session management and connection pooling
- Repository classes for SMS requests and responses
- Data integrity during concurrent operations
- Database query support for filtering by status, provider, and time range
"""

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, and_, or_, desc
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, select, Session

from .config import settings
from .models import SMSRequest, SMSResponse, ProviderHealth, SMSRetry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global database engine and session factory
_engine = None
_session_factory = None


def get_db_engine():
    """Get or create database engine with connection pooling."""
    global _engine
    if _engine is None:
        # Configure connection pooling for high throughput
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args = {
                "check_same_thread": False,  # Allow multiple threads
                "timeout": 20.0,  # Connection timeout
            }
        # For SQLite use default engine options (pooling args not supported on SingletonThreadPool)
        if settings.database_url.startswith("sqlite"):
            _engine = create_engine(
                settings.database_url,
                connect_args=connect_args,
                echo=settings.debug,
            )
        else:
            _engine = create_engine(
                settings.database_url,
                connect_args=connect_args,
                pool_size=20,  # Base connection pool size
                max_overflow=30,  # Additional connections when pool is full
                pool_timeout=30,  # Timeout for getting connection from pool
                pool_recycle=3600,  # Recycle connections after 1 hour
                echo=settings.debug,  # Log SQL if debug mode is enabled
            )
        # Ensure all tables are created on first engine creation so tests and
        # runtime code that call get_db_engine() immediately can rely on
        # SQLModel metadata existing in the database.
        try:
            SQLModel.metadata.create_all(_engine)
        except Exception:
            # Creating tables is best-effort; callers may handle migrations.
            logger.debug("create_all failed when initializing engine; continuing")
    return _engine


def get_session_factory():
    """Get or create session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_db_engine()
        _session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return _session_factory


@contextmanager
def get_db_session():
    """Context manager for database sessions with automatic cleanup."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        session.rollback()
        raise
    finally:
        session.close()


class SMSRequestRepository:
    """Repository for SMS request operations."""

    def __init__(self, engine=None):
        self.engine = engine or get_db_engine()
 
    def create_request(self, phone: str, text: str, provider_used: Optional[str] = None) -> SMSRequest:
        """Create a new SMS request in the database using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            sms_request = SMSRequest(
                phone=phone,
                text=text,
                status="pending",
                provider_used=provider_used,
                retry_count=0,
                max_retries=5,
                failed_providers="",
                is_permanently_failed=False
            )
 
            session.add(sms_request)
            session.flush()  # Get the ID without committing
            session.refresh(sms_request)
 
            logger.info(f"Created SMS request {sms_request.id} for phone {phone}")
            return sms_request

    def update_request_status(self, request_id: int, status: str, provider_used: Optional[str] = None) -> bool:
        """Update SMS request status and provider using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            sms_request = session.get(SMSRequest, request_id)
            if not sms_request:
                logger.warning(f"SMS request {request_id} not found")
                return False
 
            sms_request.status = status
            sms_request.provider_used = provider_used or sms_request.provider_used
            sms_request.updated_at = datetime.utcnow()
 
            logger.info(f"Updated SMS request {request_id} status to {status}")
            return True

    def update_request_retry_info(self, request_id: int, retry_count: int,
                                 failed_providers: str, is_permanently_failed: bool = False) -> bool:
        """Update SMS request retry information using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            sms_request = session.get(SMSRequest, request_id)
            if not sms_request:
                logger.warning(f"SMS request {request_id} not found")
                return False
 
            sms_request.retry_count = retry_count
            sms_request.failed_providers = failed_providers
            sms_request.is_permanently_failed = is_permanently_failed
            sms_request.updated_at = datetime.utcnow()
 
            logger.info(f"Updated SMS request {request_id} retry info: count={retry_count}")
            return True

    def get_request_by_id(self, request_id: int) -> Optional[SMSRequest]:
        """Get SMS request by ID using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.get(SMSRequest, request_id)

    def get_requests_by_status(self, status: str, limit: int = 100) -> List[SMSRequest]:
        """Get SMS requests by status using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.exec(select(SMSRequest).where(SMSRequest.status == status).limit(limit)).all()

    def get_requests_by_provider(self, provider: str, limit: int = 100) -> List[SMSRequest]:
        """Get SMS requests by provider using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.exec(select(SMSRequest).where(SMSRequest.provider_used == provider).limit(limit)).all()

    def get_requests_by_time_range(self, start_time: datetime, end_time: datetime,
                                  limit: int = 100) -> List[SMSRequest]:
        """Get SMS requests within time range using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.exec(
                select(SMSRequest).where(
                    and_(SMSRequest.created_at >= start_time, SMSRequest.created_at <= end_time)
                ).limit(limit)
            ).all()

    def get_requests_with_filters(self, status: Optional[str] = None,
                                 provider: Optional[str] = None,
                                 start_time: Optional[datetime] = None,
                                 end_time: Optional[datetime] = None,
                                 limit: int = 100) -> List[SMSRequest]:
        """Get SMS requests with multiple filters using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            query = select(SMSRequest)
            
            if status:
                query = query.where(SMSRequest.status == status)
            if provider:
                query = query.where(SMSRequest.provider_used == provider)
            if start_time:
                query = query.where(SMSRequest.created_at >= start_time)
            if end_time:
                query = query.where(SMSRequest.created_at <= end_time)
 
            return session.exec(query.limit(limit)).all()

    def get_request_stats(self) -> Dict[str, Any]:
        """Get SMS request statistics using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            total_requests = session.exec(select(SMSRequest)).all()
 
            stats = {
                "total_requests": len(total_requests),
                "status_breakdown": {},
                "provider_breakdown": {},
                "recent_requests": len(self.get_requests_by_time_range(
                    datetime.utcnow() - timedelta(hours=1), datetime.utcnow()
                ))
            }
 
            # Count by status
            for status in ["pending", "processing", "completed", "failed"]:
                count = len([r for r in total_requests if r.status == status])
                stats["status_breakdown"][status] = count
 
            # Count by provider
            for provider in ["provider1", "provider2", "provider3"]:
                count = len([r for r in total_requests if r.provider_used == provider])
                stats["provider_breakdown"][provider] = count
 
            return stats


class SMSResponseRepository:
    """Repository for SMS response operations."""

    def __init__(self, engine=None):
        self.engine = engine or get_db_engine()
 
    def create_response(self, request_id: int, response_data: str, status_code: int) -> SMSResponse:
        """Create a new SMS response in the database using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            sms_response = SMSResponse(
                request_id=request_id,
                response_data=response_data,
                status_code=status_code
            )
 
            session.add(sms_response)
            session.flush()
            session.refresh(sms_response)
 
            # Update the corresponding request status
            sms_request = session.get(SMSRequest, request_id)
            if sms_request:
                sms_request.status = "completed" if status_code == 200 else "failed"
                sms_request.updated_at = datetime.utcnow()
 
            logger.info(f"Created SMS response for request {request_id} with status {status_code}")
            return sms_response

    def get_response_by_request_id(self, request_id: int) -> Optional[SMSResponse]:
        """Get SMS response by request ID using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.exec(select(SMSResponse).where(SMSResponse.request_id == request_id)).first()

    def get_responses_by_time_range(self, start_time: datetime, end_time: datetime,
                                   limit: int = 100) -> List[SMSResponse]:
        """Get SMS responses within time range."""
        with get_db_session() as session:
            return session.exec(
                select(SMSResponse).where(
                    and_(SMSResponse.created_at >= start_time, SMSResponse.created_at <= end_time)
                ).limit(limit)
            ).all()


class SMSRetryRepository:
    """Repository for SMS retry operations."""

    def __init__(self, engine=None):
        self.engine = engine or get_db_engine()
 
    def create_retry(self, request_id: int, attempt_number: int, provider_used: str,
                    error_message: str, delay_seconds: int) -> SMSRetry:
        """Create a new SMS retry record using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            sms_retry = SMSRetry(
                request_id=request_id,
                attempt_number=attempt_number,
                provider_used=provider_used,
                error_message=error_message,
                delay_seconds=delay_seconds
            )
 
            session.add(sms_retry)
            session.flush()
            session.refresh(sms_retry)
 
            logger.info(f"Created retry record {sms_retry.id} for request {request_id}, attempt {attempt_number}")
            return sms_retry

    def get_retries_by_request_id(self, request_id: int) -> List[SMSRetry]:
        """Get all retry records for a request using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            return session.exec(select(SMSRetry).where(SMSRetry.request_id == request_id)).all()


class ProviderHealthRepository:
    """Repository for provider health operations."""

    def __init__(self, engine=None):
        self.engine = engine or get_db_engine()
 
    def update_provider_health(self, provider_name: str, success: bool) -> ProviderHealth:
        """Update provider health metrics using the repository's engine."""
        from sqlmodel import Session as _Session
 
        with _Session(self.engine) as session:
            # Get existing health record or create new one
            health_record = session.exec(
                select(ProviderHealth).where(ProviderHealth.provider_name == provider_name)
            ).first()
 
            if not health_record:
                health_record = ProviderHealth(provider_name=provider_name)
                session.add(health_record)
 
            # Update metrics
            if success:
                health_record.success_count += 1
            else:
                health_record.failure_count += 1

            health_record.last_checked = datetime.utcnow()

            # Calculate health status (simple success rate calculation)
            total_requests = health_record.success_count + health_record.failure_count
            if total_requests >= 10:  # Only consider health after 10+ requests
                success_rate = health_record.success_count / total_requests
                health_record.is_healthy = success_rate >= 0.8  # 80% success rate threshold

            session.flush()
            session.refresh(health_record)

            logger.info(f"Updated health for {provider_name}: success={success}, healthy={health_record.is_healthy}")
            return health_record

    def get_provider_health(self, provider_name: str) -> Optional[ProviderHealth]:
        """Get provider health record."""
        with get_db_session() as session:
            return session.exec(
                select(ProviderHealth).where(ProviderHealth.provider_name == provider_name)
            ).first()

    def get_all_providers_health(self) -> Dict[str, ProviderHealth]:
        """Get health records for all providers."""
        with get_db_session() as session:
            health_records = session.exec(select(ProviderHealth)).all()
            return {record.provider_name: record for record in health_records}

    def reset_provider_health(self, provider_name: str) -> bool:
        """Reset health metrics for a provider."""
        with get_db_session() as session:
            health_record = session.exec(
                select(ProviderHealth).where(ProviderHealth.provider_name == provider_name)
            ).first()
            if not health_record:
                return False

            health_record.success_count = 0
            health_record.failure_count = 0
            health_record.is_healthy = True
            health_record.last_checked = datetime.utcnow()

            logger.info(f"Reset health metrics for {provider_name}")
            return True


# Global repository instances
_sms_request_repo = None
_sms_response_repo = None
_sms_retry_repo = None
_provider_health_repo = None


def get_sms_request_repository(engine=None) -> SMSRequestRepository:
    """Get SMS request repository instance."""
    global _sms_request_repo
    if _sms_request_repo is None or engine is not None:
        _sms_request_repo = SMSRequestRepository(engine=engine)
    return _sms_request_repo


def get_sms_response_repository(engine=None) -> SMSResponseRepository:
    """Get SMS response repository instance."""
    global _sms_response_repo
    if _sms_response_repo is None or engine is not None:
        _sms_response_repo = SMSResponseRepository(engine=engine)
    return _sms_response_repo


def get_sms_retry_repository(engine=None) -> SMSRetryRepository:
    """Get SMS retry repository instance."""
    global _sms_retry_repo
    if _sms_retry_repo is None or engine is not None:
        _sms_retry_repo = SMSRetryRepository(engine=engine)
    return _sms_retry_repo


def get_provider_health_repository(engine=None) -> ProviderHealthRepository:
    """Get provider health repository instance."""
    global _provider_health_repo
    if _provider_health_repo is None or engine is not None:
        _provider_health_repo = ProviderHealthRepository(engine=engine)
    return _provider_health_repo


def initialize_database():
    """Initialize database and create tables."""
    try:
        engine = get_db_engine()
        logger.info("Creating database tables...")
        SQLModel.metadata.create_all(engine)

        # Initialize provider health records
        with get_db_session() as session:
            for provider_name in ["provider1", "provider2", "provider3"]:
                existing = session.exec(
                    select(ProviderHealth).where(ProviderHealth.provider_name == provider_name)
                ).first()
                if not existing:
                    health_record = ProviderHealth(provider_name=provider_name)
                    session.add(health_record)

        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        raise


async def async_initialize_database():
    """Async wrapper for database initialization."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, initialize_database)