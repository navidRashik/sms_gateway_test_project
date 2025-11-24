"""
TaskIQ scheduler configuration for SMS service.

This module sets up the TaskIQ scheduler with RedisScheduleSource for proper
time-based task scheduling, including retry delays and exponential backoff.
"""
import logging

from taskiq import TaskiqScheduler
from taskiq.serializers import JSONSerializer
from taskiq_redis import (
    RedisStreamBroker,
    RedisAsyncResultBackend,
    RedisScheduleSource,
)

from src.config import settings
from src.taskiq_config import TaskIQConfig

# Configure logging for scheduler
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create Redis result backend for tracking task results
result_backend = RedisAsyncResultBackend(
    redis_url=settings.taskiq_broker_url,
    serializer=JSONSerializer(),
)

# Create broker for task execution with result backend
broker = RedisStreamBroker(
    url=settings.taskiq_broker_url,
    queue_name=TaskIQConfig.QUEUE_NAME,
    max_connection_pool_size=TaskIQConfig.MAX_CONNECTION_POOL_SIZE,
).with_result_backend(result_backend)

# Create Redis schedule source for dynamic scheduling
redis_source = RedisScheduleSource(settings.taskiq_broker_url)

# Create scheduler that uses the Redis source
scheduler = TaskiqScheduler(
    broker=broker,
    sources=[redis_source],
)

logger.info(f"TaskIQ scheduler configured with Redis broker at {settings.taskiq_broker_url}")
logger.info(f"Using queue name: {TaskIQConfig.QUEUE_NAME}")

# Export both broker and scheduler for use in other modules
__all__ = ["broker", "scheduler", "redis_source", "result_backend"]