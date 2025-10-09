"""
Tests for configuration settings.
"""

import os
from unittest.mock import patch

import pytest

from src.config import Settings


class TestSettings:
    """Test configuration settings."""

    def test_default_settings(self):
        """Test default configuration values."""
        settings = Settings()

        assert settings.redis_url == "redis://localhost:6379"
        assert settings.provider_rate_limit == 50
        assert settings.total_rate_limit == 200
        assert settings.provider1_url == "http://localhost:8071/api/sms/provider1"
        assert settings.provider2_url == "http://localhost:8071/api/sms/provider2"
        assert settings.provider3_url == "http://localhost:8071/api/sms/provider3"
        assert settings.taskiq_broker_url == "redis://localhost:6379"
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000
        assert settings.debug is False
        assert settings.rate_limit_window == 1

    def test_environment_variable_overrides(self):
        """Test environment variable overrides."""
        with patch.dict(os.environ, {
            "REDIS_URL": "redis://test:6379",
            "PROVIDER_RATE_LIMIT": "25",
            "TOTAL_RATE_LIMIT": "100",
            "PROVIDER1_URL": "http://test1:8071/api/sms/provider1",
            "HOST": "127.0.0.1",
            "PORT": "9000",
            "DEBUG": "true"
        }):
            settings = Settings()

            assert settings.redis_url == "redis://test:6379"
            assert settings.provider_rate_limit == 25
            assert settings.total_rate_limit == 100
            assert settings.provider1_url == "http://test1:8071/api/sms/provider1"
            assert settings.host == "127.0.0.1"
            assert settings.port == 9000
            assert settings.debug is True

    def test_env_file_loading(self):
        """Test loading from .env file."""
        # Create temporary .env file
        env_content = """
REDIS_URL=redis://envtest:6379
PROVIDER_RATE_LIMIT=75
DEBUG=true
        """

        with patch('src.config.os.path.exists', return_value=True), \
             patch('src.config.dotenv_values', return_value={
                 "REDIS_URL": "redis://envtest:6379",
                 "PROVIDER_RATE_LIMIT": "75",
                 "DEBUG": "true"
             }):

            settings = Settings()
            assert settings.redis_url == "redis://envtest:6379"
            assert settings.provider_rate_limit == 75
            assert settings.debug is True

    def test_case_insensitive_env_vars(self):
        """Test case insensitive environment variables."""
        with patch.dict(os.environ, {
            "redis_url": "redis://caseinsensitive:6379",
            "provider_rate_limit": "30",
            "DeBuG": "true"
        }):
            settings = Settings()

            assert settings.redis_url == "redis://caseinsensitive:6379"
            assert settings.provider_rate_limit == 30
            assert settings.debug is True


class TestSettingsValidation:
    """Test settings validation."""

    def test_invalid_rate_limit_values(self):
        """Test invalid rate limit values."""
        with patch.dict(os.environ, {
            "PROVIDER_RATE_LIMIT": "0",  # Should be positive
            "TOTAL_RATE_LIMIT": "-1"     # Should be positive
        }):
            # Should not raise exception but use defaults
            settings = Settings()
            assert settings.provider_rate_limit > 0
            assert settings.total_rate_limit > 0

    def test_malformed_urls(self):
        """Test malformed URL handling."""
        with patch.dict(os.environ, {
            "REDIS_URL": "not-a-url",
            "PROVIDER1_URL": "also-not-a-url"
        }):
            # Should not raise exception
            settings = Settings()
            # Values should be as provided (validation happens at runtime)
            assert settings.redis_url == "not-a-url"
            assert settings.provider1_url == "also-not-a-url"