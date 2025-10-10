# Developer Guide: Twillow SMS Gateway

## Overview
This document provides developer setup instructions, architecture overview, configuration reference, testing guidance (unit, integration, manual), and a reviewer-focused quick validation guide for the Twillow SMS Gateway (FastAPI + Redis + TaskIQ + SQLite).

The documentation highlights the sliding-window behavior used by the rate limiter and health tracker and provides step-by-step instructions to manually verify the architecture by shortening the windows for fast feedback during review.

## Goals & Success Criteria
- Start the service locally (Docker Compose) and run end-to-end scenarios.
- Verify rate limiting (per-provider and global) and the TaskIQ-based processing pipeline.
- Reproduce provider health transitions and retry behavior deterministically.
- Provide quick commands so reviewers can validate core behavior within ~10 minutes.

## Prerequisites
- Python 3.11+
- Node.js 18+ (optional, for provider mocks)
- Docker & Docker Compose (recommended for local stack)
- Git
- Recommended: GNU Make (optional convenience)

## Repository layout (important files)
- `src/` — Python application code (FastAPI)
- `docs/` — documentation (this file)
- `docker-compose.yml` — local stack (Redis, app, provider mocks)
- `Dockerfile` / `Dockerfile.nodejs` — container builds
- `requirements.txt` / `pyproject.toml` — Python dependencies
- `alembic/` — DB migrations
- `tests/` — unit & integration tests
- `.agents/` — planning & worklist (per project policy)

## Configuration
Configuration is primarily via environment variables or a `.env` file. See [`src/config.py`](src/config.py:1) for full defaults and descriptions.

Key configuration variables:
- REDIS_URL (default: redis://localhost:6379)
- TASKIQ_BROKER_URL (default: redis://localhost:6379)
- DATABASE_URL (default: sqlite:///./sms_service.db)
- PROVIDER1_URL, PROVIDER2_URL, PROVIDER3_URL — provider endpoints
- PROVIDER_RATE_LIMIT (default: 50) — requests per second per provider
- TOTAL_RATE_LIMIT (default: 200) — global requests per second
- RATE_LIMIT_WINDOW (default: 1) — sliding window duration in seconds (see Sliding-window testing)
- HEALTH_WINDOW_DURATION (default: 300) — window in seconds used by health tracker
- HEALTH_FAILURE_THRESHOLD (default: 0.7) — failure rate threshold (70%)
- DEBUG (true/false)

Example `.env` (project root):
```bash
# bash
REDIS_URL=redis://localhost:6379
TASKIQ_BROKER_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./data/sms_gateway.db
PROVIDER1_URL=http://localhost:8071/api/sms/provider1
PROVIDER2_URL=http://localhost:8072/api/sms/provider2
PROVIDER3_URL=http://localhost:8073/api/sms/provider3
PROVIDER_RATE_LIMIT=50
TOTAL_RATE_LIMIT=200
RATE_LIMIT_WINDOW=1
HEALTH_WINDOW_DURATION=300
HEALTH_FAILURE_THRESHOLD=0.7
DEBUG=true
```

## Quickstart (Docker Compose)
Start the local environment that includes Redis, provider mocks and the app:

```bash
docker-compose up --build
```

- App: http://localhost:8000
- OpenAPI / Swagger: http://localhost:8000/docs

Start the worker in a separate terminal (if not run inside compose):
```bash
# Activate virtualenv (if using .venv)
source .venv/bin/activate

python -m src.worker
```

To run the scheduler (if needed):
```bash
taskiq scheduler src.taskiq_scheduler:scheduler
```

## Local developer setup
1. Install dependencies and sync using uv (if project uses uv):
```bash
uv sync
source .venv/bin/activate
```
2. Run app locally:
```bash
uvicorn "src.main:app" --reload --host 0.0.0.0 --port 8000
```
3. Run unit tests locally:
```bash
pytest -q
```

## Running tests (recommended commands for reviewers)
- Full test suite:
```bash
pytest -q
```
- Targeted checks (fast validation):
```bash
# Health tracker tests
pytest tests/test_health_tracker.py::TestProviderHealthTracker -q

# Rate limiter high-load scenarios
pytest tests/test_rate_limiter_high_load.py -q

# Queue & API focused tests
pytest tests/test_queue.py -q

# Retry behavior
pytest tests/test_retry_service.py::TestRetryService -q
```

Notes:
- Tests use `tests/conftest.py` fixtures that mock or simulate Redis. Most unit tests do not require a running Redis instance.
- Integration tests in `tests/` use deterministic in-memory Redis-like stores to make CI repeatable.

## Testing matrix — what to validate
- Unit tests: logic-heavy modules (rate limiter, health tracker, retry logic, distribution). Look at `tests/test_rate_limiter.py`, `tests/test_health_tracker.py`, `tests/test_retry_service.py`.
- Integration tests: cross-module interaction (queue -> tasks -> DB). See `tests/test_integration*.py`.
- Manual end-to-end: use Docker Compose + worker + provider mocks; validate provider distribution, rate limiting, retries, and DB persistence.

## Sliding-window testing (fast reviewer workflow)
The project uses a sliding / short-window approach for rate limiting. The window is controlled by `RATE_LIMIT_WINDOW` (seconds) in [`src/config.py`](src/config.py:1). By shortening the window and health window, reviewers can rapidly exercise limits and health transitions.

Quick steps to reproduce in ~1–2 minutes:
1. Edit `.env` or export vars in your shell:
```bash
export RATE_LIMIT_WINDOW=1
export PROVIDER_RATE_LIMIT=5
export TOTAL_RATE_LIMIT=20
export HEALTH_WINDOW_DURATION=5
export HEALTH_FAILURE_THRESHOLD=0.7
```
2. Start the stack:
```bash
docker-compose up --build
# in another terminal start the worker if not started inside compose
python -m src.worker
```
3. Fire a short burst of requests to saturate providers:
```bash
for i in {1..30}; do
  curl -sS -X POST http://localhost:8000/api/sms \
    -H "Content-Type: application/json" \
    -d '{"phone":"+15551234567","text":"test '"$i"'"}' &
done
wait
```
4. Observe:
- Per-provider Redis keys named `rate_limit:provider1`, `rate_limit:provider2`, `rate_limit:provider3`. Counters increment and expire every `RATE_LIMIT_WINDOW` seconds.
- Some requests may receive 429 (gateway-side rate limiting) once per-provider limits or total global limit is exceeded.
- If provider mocks return failures (simulate by making provider mocks respond 500), health tracker aggregates failures over `HEALTH_WINDOW_DURATION` and marks provider unhealthy if failure rate > `HEALTH_FAILURE_THRESHOLD`. The distribution service will then avoid that provider.

Example Redis inspection:
```bash
redis-cli GET rate_limit:provider1
redis-cli --raw --scan | grep rate_limit
```

Expected quick outcomes (with above env):
- When sending a tight burst, each provider's per-window count should not exceed `PROVIDER_RATE_LIMIT` (5) within a single second; excess requests will be rate-limited or queued.
- Health flips are visible within `HEALTH_WINDOW_DURATION` seconds after sufficient failures.

## Simulating provider failures
- Provider mock servers (in docker-compose) can be configured to return HTTP 500 for testing. Use their provided admin endpoints (if present) or modify mock to fail a percentage of requests.
- Alternatively, patch the provider URL to a local mock that returns 500 for a period while running the burst above to force failures and drive health transitions.

## Validating retry & dead-letter behavior
- Retries use exponential backoff controlled in `src/retry_service.py` with `max_retries` default of 5.
- To validate: make provider return intermittent 5xx responses; observe task retries, eventual dead-letter entry after exhausting retries (see `tests/test_dead_letter_queue.py`).

## Debugging tips
- Enable DEBUG logging:
```bash
export DEBUG=true
```
- Tail the application and worker logs in separate terminals.
- Check Redis keys and TTLs to ensure rate-limit keys expire as expected.
- Use tests with increased verbosity or focused `-k <keyword>` runs to reproduce failing scenarios quickly.

## Core files to inspect (quick map)
- `src/config.py` — central configuration values (window durations, thresholds)
- `src/rate_limiter.py` — Redis-based per-provider & global rate limiting logic
- `src/health_tracker.py` — sliding-window health calculations and failure aggregation
- `src/distribution.py` — provider selection and weighted round-robin distribution
- `src/tasks.py` — TaskIQ tasks: sending to providers, recording responses
- `src/retry_service.py` — retry logic, exponential backoff, dead-letter handling
- `src/queue.py` — FastAPI endpoints and request enqueueing
- `src/worker.py` — TaskIQ worker runner

## Acceptance criteria for reviewers
- App starts with Docker Compose and responds at `/api/sms`.
- Worker processes queued tasks and writes responses/requests to the DB (or logs).
- Per-provider limits are enforced (use sliding-window test to verify).
- Provider health transitions occur after repeated failures and are respected by distribution.
- Targeted unit tests from `tests/` pass locally.

## CI / notes for reviewers
- Most unit tests rely on mocks and do not require external services.
- Integration tests that simulate Redis use in-memory stores; if a test requires a real Redis in your environment, prefer running it in the Docker Compose stack.
- If tests fail locally, run the focused test with `-k` to inspect logs and underlying assertions.

## Appendix — Common commands
```bash
# Start local stack
docker-compose up --build

# Run app locally
uvicorn "src.main:app" --reload --host 0.0.0.0 --port 8000

# Run worker
python -m src.worker

# Run full test suite
pytest -q

# Run focused health tracker test
pytest tests/test_health_tracker.py::TestProviderHealthTracker -q
```
