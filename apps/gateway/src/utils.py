"""
Utility functions for Redis operations and data parsing.

This module contains shared utility functions used across the SMS service.
"""

import logging
from typing import Optional

# Configure logging
logger = logging.getLogger(__name__)


def parse_redis_int(value: Optional[object]) -> int:
    """
    Parse a Redis GET result into an int safely.
    Handles bytes, str, int and None. Returns 0 for falsy values or non-parseable input.

    Args:
        value: The value returned from Redis GET operation

    Returns:
        Parsed integer value, or 0 if parsing fails
    """
    if not value:
        return 0
    # if redis client was configured with decode_responses=True this may already be str/int
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        try:
            return int(value.decode())
        except (ValueError, UnicodeDecodeError):
            logger.debug(f"Failed to decode Redis bytes value: {value!r}")
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            logger.debug(f"Failed to parse Redis string value to int: {value!r}")
            return 0
    # fallback
    try:
        return int(value)
    except Exception:
        logger.debug(f"Unexpected Redis value type for int parsing: {type(value)}")
        return 0