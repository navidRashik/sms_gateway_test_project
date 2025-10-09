"""
TaskIQ worker for processing SMS tasks.

This script runs the TaskIQ worker to process SMS tasks from the Redis queue.
It handles background processing of SMS requests with proper error handling and logging.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from taskiq.cli.worker import run_worker

from .config import settings
from .tasks import broker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Handle graceful shutdown of the worker."""

    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.tasks_cancelled = False

    def setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler():
            logger.info("Received shutdown signal, stopping worker...")
            self.shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_event_loop().add_signal_handler(sig, signal_handler)

    async def wait_for_shutdown(self):
        """Wait for shutdown signal."""
        await self.shutdown_event.wait()
        logger.info("Shutdown signal received")


async def run_sms_worker():
    """Run the SMS worker with graceful shutdown handling."""
    shutdown_handler = GracefulShutdown()
    shutdown_handler.setup_signal_handlers()

    logger.info("Starting SMS worker...")
    logger.info(f"Broker URL: {settings.taskiq_broker_url}")
    logger.info(f"Queue name: sms_queue")
    logger.info("Worker is ready to process SMS tasks")

    try:
        # Run the worker with graceful shutdown handling
        await run_worker(
            broker=broker,
            worker_name="sms_worker",
        )
    except asyncio.CancelledError:
        logger.info("Worker tasks cancelled")
    except Exception as e:
        logger.error(f"Worker error: {str(e)}")
        raise
    finally:
        logger.info("SMS worker stopped")


async def run_worker_with_shutdown():
    """Run worker with proper shutdown handling."""
    shutdown_handler = GracefulShutdown()

    # Create task for the worker
    worker_task = asyncio.create_task(run_sms_worker())

    # Wait for shutdown signal
    await shutdown_handler.wait_for_shutdown()

    # Cancel worker task
    worker_task.cancel()

    try:
        await worker_task
    except asyncio.CancelledError:
        logger.info("Worker task cancelled successfully")

    logger.info("Worker shutdown complete")


def main():
    """Main entry point for the SMS worker."""
    try:
        asyncio.run(run_worker_with_shutdown())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.error(f"Worker failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()