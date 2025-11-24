#!/usr/bin/env python3
"""
TaskIQ scheduler for SMS service.

This script provides a simple interface to start the TaskIQ scheduler
for the SMS service. The scheduler handles time-based task scheduling,
including retry delays and exponential backoff for SMS sending tasks.

To run the scheduler, use the command:
  taskiq scheduler src.taskiq_scheduler:scheduler

Or if using the project installed scripts:
  twillow-scheduler

The scheduler will:
1. Connect to Redis to manage scheduled tasks
2. Monitor scheduled tasks and trigger them at the appropriate times
3. Handle retry scheduling with exponential backoff
"""

import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Main entry point for the SMS scheduler."""
    logger.info("SMS Service Scheduler")
    logger.info("=" * 50)
    logger.info("To run the scheduler, use one of these commands:")
    logger.info("")
    logger.info("  Using taskiq CLI directly:")
    logger.info("    taskiq scheduler src.taskiq_scheduler:scheduler")
    logger.info("")
    logger.info("  Or with uv (if installed separately):")
    logger.info("    uv run taskiq scheduler src.taskiq_scheduler:scheduler")
    logger.info("")
    logger.info("Make sure Redis is running before starting the scheduler.")
    logger.info("Only run ONE instance of the scheduler in production!")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())