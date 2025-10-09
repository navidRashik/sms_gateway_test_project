"""
TaskIQ configuration for SMS service.

This module contains TaskIQ-specific configuration and error handling utilities.
"""

import logging
from typing import Any, Dict, Optional

from taskiq import TaskiqEvents, TaskiqMessage

from .config import settings

# Configure logging for TaskIQ
taskiq_logger = logging.getLogger("taskiq")
taskiq_logger.setLevel(logging.INFO)

# Create console handler for TaskIQ logs
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
console_handler.setFormatter(formatter)
taskiq_logger.addHandler(console_handler)


class TaskIQConfig:
    """TaskIQ configuration settings."""

    # Retry configuration
    MAX_RETRIES = 5
    RETRY_DELAY_BASE = 2  # Base delay in seconds for exponential backoff

    # Task timeouts
    TASK_TIMEOUT = 30  # seconds
    WORKER_TIMEOUT = 300  # seconds

    # Queue settings
    QUEUE_NAME = "sms_queue"
    MAX_CONNECTION_POOL_SIZE = 20

    # Worker settings
    WORKER_NAME = "sms_worker"
    MAX_TASKS_PER_WORKER = 100

    # Error handling
    DEAD_LETTER_QUEUE = "sms_dead_letter"
    ERROR_RETRY_LIMIT = 3


# TaskIQ event handlers for monitoring and error handling
async def taskiq_startup_handler(event: TaskiqEvents) -> None:
    """Handle TaskIQ startup events."""
    taskiq_logger.info(f"TaskIQ {event.event_type} - Starting SMS worker")
    if hasattr(event, 'worker_name'):
        taskiq_logger.info(f"Worker name: {event.worker_name}")


async def taskiq_shutdown_handler(event: TaskiqEvents) -> None:
    """Handle TaskIQ shutdown events."""
    taskiq_logger.info(f"TaskIQ {event.event_type} - Shutting down SMS worker")


async def taskiq_task_error_handler(message: TaskiqMessage, exception: Exception) -> None:
    """Handle task execution errors."""
    taskiq_logger.error(
        f"Task execution failed - Task: {message.task_name}, "
        f"Error: {str(exception)}, "
        f"Labels: {message.labels}"
    )

    # Log additional context for SMS tasks
    if message.task_name in ['send_sms_to_provider', 'process_sms_batch']:
        taskiq_logger.error(
            f"SMS task failed - Args: {message.args}, "
            f"Kwargs: {message.kwargs}"
        )


async def taskiq_task_success_handler(message: TaskiqMessage, result: Any) -> None:
    """Handle successful task execution."""
    taskiq_logger.info(
        f"Task completed successfully - Task: {message.task_name}, "
        f"Result type: {type(result).__name__}"
    )

    # Log success for SMS tasks
    if message.task_name == 'send_sms_to_provider' and isinstance(result, dict):
        success = result.get('success', False)
        message_id = result.get('message_id', 'unknown')
        provider = result.get('provider', 'unknown')
        retry_count = result.get('retry_count', 0)

        if success:
            taskiq_logger.info(
                f"SMS sent successfully - MessageID: {message_id}, "
                f"Provider: {provider}, Retries: {retry_count}"
            )
        else:
            taskiq_logger.warning(
                f"SMS failed permanently - MessageID: {message_id}, "
                f"Provider: {provider}, Final retry count: {retry_count}"
            )


# TaskIQ event middleware for enhanced error handling and monitoring
class SMSTaskIQMiddleware:
    """Middleware for SMS-specific TaskIQ enhancements."""

    async def __call__(
        self,
        message: TaskiqMessage,
        call_next: Any
    ) -> Any:
        """Process task with enhanced error handling."""
        start_time = asyncio.get_event_loop().time()

        try:
            # Execute the task
            result = await call_next(message)

            # Log successful execution
            execution_time = asyncio.get_event_loop().time() - start_time
            await taskiq_task_success_handler(message, result)

            # Add execution time to result for monitoring
            if isinstance(result, dict):
                result['_execution_time'] = execution_time

            return result

        except Exception as e:
            # Log error
            execution_time = asyncio.get_event_loop().time() - start_time
            await taskiq_task_error_handler(message, e)

            # Re-raise the exception to let TaskIQ handle retries
            raise


def get_taskiq_broker_config() -> Dict[str, Any]:
    """Get TaskIQ broker configuration."""
    return {
        "url": settings.taskiq_broker_url,
        "queue_name": TaskIQConfig.QUEUE_NAME,
        "max_connection_pool_size": TaskIQConfig.MAX_CONNECTION_POOL_SIZE,
    }


def get_taskiq_worker_config() -> Dict[str, Any]:
    """Get TaskIQ worker configuration."""
    return {
        "worker_name": TaskIQConfig.WORKER_NAME,
        "max_tasks_per_worker": TaskIQConfig.MAX_TASKS_PER_WORKER,
        "task_timeout": TaskIQConfig.TASK_TIMEOUT,
        "worker_timeout": TaskIQConfig.WORKER_TIMEOUT,
    }


def calculate_retry_delay(attempt: int) -> float:
    """Calculate delay for retry attempts using exponential backoff."""
    return TaskIQConfig.RETRY_DELAY_BASE ** attempt


def should_retry_task(exception: Exception, attempt: int) -> bool:
    """Determine if a task should be retried based on the exception."""
    if attempt >= TaskIQConfig.MAX_RETRIES:
        return False

    # Don't retry certain types of errors
    if isinstance(exception, (ValueError, TypeError)):
        # Validation errors shouldn't be retried
        return False

    # Retry network and temporary errors
    retryable_errors = (
        ConnectionError,
        TimeoutError,
        OSError,  # Network-related OS errors
    )

    return isinstance(exception, retryable_errors)