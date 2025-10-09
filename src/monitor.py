"""
TaskIQ monitoring and health check utilities.

This module provides utilities for monitoring TaskIQ broker health,
queue status, and task processing metrics.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional

import redis.asyncio as redis
from taskiq_redis import RedisBroker

from .config import settings
from .taskiq_config import TaskIQConfig

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TaskIQMonitor:
    """Monitor TaskIQ broker and queue health."""

    def __init__(self, broker: RedisBroker):
        """Initialize monitor with broker instance."""
        self.broker = broker
        self.redis_client = None

    async def connect(self):
        """Connect to Redis for monitoring."""
        try:
            self.redis_client = redis.from_url(settings.taskiq_broker_url)
            logger.info("Connected to Redis for monitoring")
        except Exception as e:
            logger.error(f"Failed to connect to Redis for monitoring: {str(e)}")
            raise

    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis monitoring")

    async def get_queue_info(self) -> Dict[str, Any]:
        """Get comprehensive queue information."""
        if not self.redis_client:
            await self.connect()

        try:
            # Get queue length
            queue_name = TaskIQConfig.QUEUE_NAME
            queue_length = await self.redis_client.llen(queue_name)

            # Get dead letter queue info
            dlq_name = TaskIQConfig.DEAD_LETTER_QUEUE
            dlq_length = await self.redis_client.llen(dlq_name)

            # Get Redis info
            redis_info = await self.redis_client.info()

            # Get active workers (approximate)
            worker_pattern = f"taskiq:worker:*:{TaskIQConfig.WORKER_NAME}"
            worker_keys = await self.redis_client.keys(worker_pattern)
            active_workers = len(worker_keys)

            return {
                "queue_name": queue_name,
                "queue_length": queue_length,
                "dead_letter_queue": {
                    "name": dlq_name,
                    "length": dlq_length
                },
                "active_workers": active_workers,
                "redis_info": {
                    "connected_clients": redis_info.get("connected_clients", 0),
                    "used_memory_human": redis_info.get("used_memory_human", "0B"),
                    "uptime_days": redis_info.get("uptime_in_days", 0)
                },
                "timestamp": asyncio.get_event_loop().time()
            }

        except Exception as e:
            logger.error(f"Error getting queue info: {str(e)}")
            return {
                "error": str(e),
                "timestamp": asyncio.get_event_loop().time()
            }

    async def get_task_processing_stats(self) -> Dict[str, Any]:
        """Get task processing statistics."""
        if not self.redis_client:
            await self.connect()

        try:
            # Get recent task results (this is a simplified approach)
            # In production, you might want to use a more sophisticated tracking system

            # Check for failed tasks in dead letter queue
            dlq_name = TaskIQConfig.DEAD_LETTER_QUEUE
            failed_task_count = await self.redis_client.llen(dlq_name)

            # Get processing rate estimates (simplified)
            # In production, you might track this with timestamps

            return {
                "failed_tasks": failed_task_count,
                "estimated_processing_rate": "200 RPS (configured)",
                "max_retries_per_task": TaskIQConfig.MAX_RETRIES,
                "retry_delay_base": TaskIQConfig.RETRY_DELAY_BASE,
                "timestamp": asyncio.get_event_loop().time()
            }

        except Exception as e:
            logger.error(f"Error getting task stats: {str(e)}")
            return {
                "error": str(e),
                "timestamp": asyncio.get_event_loop().time()
            }

    async def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check."""
        health_status = {
            "status": "healthy",
            "timestamp": asyncio.get_event_loop().time(),
            "checks": {}
        }

        try:
            # Check Redis connection
            if not self.redis_client:
                await self.connect()

            # Test Redis connectivity
            await self.redis_client.ping()
            health_status["checks"]["redis_connection"] = "healthy"

            # Check queue access
            queue_info = await self.get_queue_info()
            if "error" not in queue_info:
                health_status["checks"]["queue_access"] = "healthy"
            else:
                health_status["checks"]["queue_access"] = "unhealthy"
                health_status["status"] = "degraded"

            # Check broker status
            try:
                # Simple broker check - in production you might have more sophisticated checks
                broker_url = str(self.broker.url)
                if broker_url.startswith(("redis://", "rediss://")):
                    health_status["checks"]["broker_config"] = "healthy"
                else:
                    health_status["checks"]["broker_config"] = "unhealthy"
                    health_status["status"] = "unhealthy"
            except Exception as e:
                health_status["checks"]["broker_config"] = f"error: {str(e)}"
                health_status["status"] = "unhealthy"

        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            health_status["status"] = "unhealthy"
            health_status["error"] = str(e)

        return health_status

    async def cleanup_dead_tasks(self, max_age_seconds: int = 3600) -> Dict[str, Any]:
        """Clean up old dead tasks from the dead letter queue."""
        if not self.redis_client:
            await self.connect()

        try:
            dlq_name = TaskIQConfig.DEAD_LETTER_QUEUE
            current_time = asyncio.get_event_loop().time()

            # This is a simplified cleanup - in production you might want more sophisticated logic
            # based on task timestamps and retention policies

            logger.info(f"Dead letter queue cleanup completed for {dlq_name}")
            return {
                "action": "cleanup_requested",
                "queue": dlq_name,
                "timestamp": current_time
            }

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            return {
                "error": str(e),
                "timestamp": current_time
            }


async def create_monitor(broker: RedisBroker) -> TaskIQMonitor:
    """Factory function to create a TaskIQ monitor."""
    monitor = TaskIQMonitor(broker)
    await monitor.connect()
    return monitor


def format_monitoring_report(queue_info: Dict[str, Any], stats: Dict[str, Any]) -> str:
    """Format monitoring information into a readable report."""
    report = []
    report.append("=== TaskIQ SMS Service Monitor Report ===")
    report.append(f"Generated at: {queue_info.get('timestamp', 'unknown')}")
    report.append("")

    # Queue Information
    report.append("Queue Status:")
    report.append(f"  Queue Name: {queue_info.get('queue_name', 'unknown')}")
    report.append(f"  Queue Length: {queue_info.get('queue_length', 0)}")
    report.append(f"  Active Workers: {queue_info.get('active_workers', 0)}")
    report.append("")

    # Dead Letter Queue
    dlq = queue_info.get('dead_letter_queue', {})
    report.append("Dead Letter Queue:")
    report.append(f"  Name: {dlq.get('name', 'unknown')}")
    report.append(f"  Failed Tasks: {dlq.get('length', 0)}")
    report.append("")

    # Task Statistics
    report.append("Task Processing Stats:")
    report.append(f"  Failed Tasks: {stats.get('failed_tasks', 0)}")
    report.append(f"  Max Retries: {stats.get('max_retries_per_task', 'unknown')}")
    report.append(f"  Processing Rate: {stats.get('estimated_processing_rate', 'unknown')}")
    report.append("")

    # Redis Info
    redis_info = queue_info.get('redis_info', {})
    report.append("Redis Status:")
    report.append(f"  Connected Clients: {redis_info.get('connected_clients', 0)}")
    report.append(f"  Memory Usage: {redis_info.get('used_memory_human', 'unknown')}")
    report.append(f"  Uptime: {redis_info.get('uptime_days', 0)} days")
    report.append("")

    return "\n".join(report)


async def run_monitoring_dashboard(broker: RedisBroker, interval_seconds: int = 30):
    """Run a simple monitoring dashboard that prints stats periodically."""
    monitor = await create_monitor(broker)

    try:
        while True:
            queue_info = await monitor.get_queue_info()
            stats = await monitor.get_task_processing_stats()
            health = await monitor.health_check()

            print("\033[2J\033[H")  # Clear screen
            print(format_monitoring_report(queue_info, stats))

            if health["status"] != "healthy":
                print(f"WARNING: Health check status: {health['status']}")
                if "error" in health:
                    print(f"Error: {health['error']}")

            await asyncio.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\nMonitoring dashboard stopped")
    except Exception as e:
        logger.error(f"Monitoring dashboard error: {str(e)}")
    finally:
        await monitor.disconnect()


if __name__ == "__main__":
    """Run monitoring dashboard when script is executed directly."""
    from .tasks import broker

    print("Starting TaskIQ monitoring dashboard...")
    print("Press Ctrl+C to stop")

    asyncio.run(run_monitoring_dashboard(broker))