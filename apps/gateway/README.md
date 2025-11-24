# Twillow SMS Gateway Service

High-throughput SMS gateway service built with FastAPI, Redis, TaskIQ, and SQLite. This service queues incoming SMS requests and distributes them across multiple providers while enforcing per-provider and global rate limits.

## Features

- **FastAPI** - High-performance async web framework
- **Redis** - Message queue and rate limiting
- **TaskIQ** - Asynchronous task processing
- **SQLite** - Request logging and persistence
- **Rate Limiting** - Per-provider and global rate limits
- **Provider Selection** - Round-robin distribution across providers
- **Database Migrations** - Alembic for schema management

## Installation

```bash
# Using uv (recommended)
uv sync

# Using pip
pip install -e .
```

## Development

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=src tests/

# Format code
black src/ tests/
isort src/ tests/

# Lint
ruff check src/ tests/
```

## Running the Service

```bash
# Start the API server
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Start the worker
python -m src.worker

# Start the scheduler
python -m src.scheduler
```

## Environment Variables

See the root README for required environment variables and configuration options.

## Project Structure

```
src/
├── main.py           # FastAPI application
├── models.py         # Database models
├── config.py         # Configuration management
├── worker.py         # TaskIQ worker
├── scheduler.py      # Scheduled tasks
└── tasks/            # Task definitions

tests/
├── test_api.py       # API endpoint tests
├── test_tasks.py     # Task processing tests
└── conftest.py       # Test fixtures

alembic/
└── versions/         # Database migrations
```
