"""
Configuration settings for Twillow SMS Service.
"""

import os
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Redis configuration
    redis_url: str = Field(default="redis://localhost:6379", env="REDIS_URL")

    # SMS Provider configuration
    provider_rate_limit: int = Field(default=50, description="Requests per second per provider")
    total_rate_limit: int = Field(default=200, description="Total requests per second")

    # Provider endpoints (localhost for development)
    provider1_url: str = Field(default="http://localhost:8071/api/sms/provider1", env="PROVIDER1_URL")
    provider2_url: str = Field(
        default="http://localhost:8072/api/sms/provider2", env="PROVIDER2_URL"
    )
    provider3_url: str = Field(
        default="http://localhost:8073/api/sms/provider3", env="PROVIDER3_URL"
    )

    # Taskiq configuration
    taskiq_broker_url: str = Field(default="redis://localhost:6379", env="TASKIQ_BROKER_URL")

    # Application configuration
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8000, env="PORT")
    debug: bool = Field(default=False, env="DEBUG")

    # Rate limiting configuration
    rate_limit_window: int = Field(default=1, description="Rate limit window in seconds")

    # Health tracking configuration
    health_window_duration: int = Field(default=300, description="Health tracking window in seconds (5 minutes)")
    health_failure_threshold: float = Field(default=0.7, description="Failure rate threshold for marking providers unhealthy")

    # Database configuration
    database_url: str = Field(default="sqlite:///./sms_service.db", env="DATABASE_URL")

    class Config:
        """Pydantic configuration."""
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()