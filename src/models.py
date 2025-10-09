"""
Database models for SMS Service using SQLModel.
"""

import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SMSRequest(SQLModel, table=True):
    """Model for SMS requests."""

    __tablename__ = "sms_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    phone: str = Field(..., max_length=20, description="Phone number to send SMS to")
    text: str = Field(..., max_length=160, description="SMS message content")
    status: str = Field(default="pending", max_length=20, description="Request status")
    provider_used: Optional[str] = Field(default=None, max_length=50, description="SMS provider used")
    retry_count: int = Field(default=0, description="Number of retry attempts made")
    max_retries: int = Field(default=5, description="Maximum retry attempts allowed")
    failed_providers: str = Field(default="", max_length=200, description="Comma-separated list of providers that failed")
    is_permanently_failed: bool = Field(default=False, description="Whether request has permanently failed after max retries")
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Request creation timestamp")
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Request update timestamp")


class SMSResponse(SQLModel, table=True):
    """Model for SMS responses."""

    __tablename__ = "sms_responses"

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(..., foreign_key="sms_requests.id", description="Reference to SMS request")
    response_data: str = Field(..., description="Raw response from SMS provider")
    status_code: int = Field(..., description="HTTP status code from provider")
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Response creation timestamp")


class ProviderHealth(SQLModel, table=True):
    """Model for SMS provider health metrics."""

    __tablename__ = "provider_health"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_name: str = Field(..., max_length=50, unique=True, description="Name of SMS provider")
    success_count: int = Field(default=0, description="Number of successful requests")
    failure_count: int = Field(default=0, description="Number of failed requests")
    last_checked: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Last health check timestamp")
    is_healthy: bool = Field(default=True, description="Whether provider is considered healthy")


class SMSRetry(SQLModel, table=True):
    """Model for tracking SMS retry attempts."""

    __tablename__ = "sms_retries"

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(..., foreign_key="sms_requests.id", description="Reference to SMS request")
    attempt_number: int = Field(..., description="Retry attempt number (1-5)")
    provider_used: str = Field(..., max_length=50, description="Provider used for this retry attempt")
    error_message: str = Field(..., max_length=500, description="Error message from failed attempt")
    delay_seconds: int = Field(..., description="Delay before this retry attempt in seconds")
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Retry attempt timestamp")


# Create all tables function for database initialization
def create_tables(engine):
    """Create all database tables."""
    SQLModel.metadata.create_all(engine)