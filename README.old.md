# Twillow SMS Gateway

High-throughput SMS gateway using FastAPI, Redis, TaskIQ, and SQLite. Designed to queue incoming SMS requests and distribute them across multiple providers while enforcing per-provider and global rate limits.

## Quickstart (Docker Compose)

Start local services (Redis, provider mocks, app):

```bash
docker-compose up --build
```

App will be available at http://localhost:8000 and OpenAPI at http://localhost:8000/docs

## Local development (uv)

Install dependencies and run the app locally:

```bash
# Install and sync dependencies (using uv)
uv sync

# Activate the virtual environment
source .venv/bin/activate

# Run the application
uvicorn "src.main:app" --reload --host 0.0.0.0 --port 8000
```

## Run TaskIQ worker

Start the background worker that processes SMS tasks:

```bash
# Activate virtualenv first (if using .venv)
source .venv/bin/activate

# Run the TaskIQ worker (uses src/worker.py)
python -m src.worker
```

## Configuration

Configuration is driven by environment variables or a `.env` file (see [`src/config.py`](src/config.py:40) for defaults and descriptions).

Important variables:
- REDIS_URL (default: redis://localhost:6379)
- TASKIQ_BROKER_URL (default: redis://localhost:6379)
- DATABASE_URL (default: sqlite:///./sms_service.db)
- PROVIDER1_URL, PROVIDER2_URL, PROVIDER3_URL
- PROVIDER_RATE_LIMIT (default: 50)
- TOTAL_RATE_LIMIT (default: 200)
- RATE_LIMIT_WINDOW (default: 1)  <- sliding window duration in seconds (see "Sliding-window testing" below)
- HEALTH_WINDOW_DURATION (default: 300) <- health tracking window in seconds
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
DEBUG=true
```

## Sliding-window note (important for reviewers & manual testing)

The per-provider and global rate limiting use a sliding/1-second window controlled by the setting `rate_limit_window` (env: `RATE_LIMIT_WINDOW`) defined in [`src/config.py`](src/config.py:40). You can lower this value (for example to `1`) and reduce `HEALTH_WINDOW_DURATION` to a small number (for example `5`) to manually reproduce rate-limit and health-tracking behavior quickly during review.

Key testing idea:
- Set RATE_LIMIT_WINDOW=1 and HEALTH_WINDOW_DURATION=5 to make the system reset counters every second and compute provider health over a short 5-second window.
- Use a simple load generator (curl loop or small script) to send bursts and observe:
  - per-provider counters increment and expire each second,
  - providers hitting their configured limit return 429 from the gateway (or are skipped by distribution),
  - health tracker flips provider health to unhealthy once failure rate > threshold (see `HEALTH_WINDOW_DURATION` and `health_failure_threshold` in [`src/config.py`](src/config.py:43)).

Example manual test steps:
1. Start stack:
   ```bash
   docker-compose up --build
   ```
2. Edit `.env` to set:
   ```bash
   RATE_LIMIT_WINDOW=1
   HEALTH_WINDOW_DURATION=5
   PROVIDER_RATE_LIMIT=5
   TOTAL_RATE_LIMIT=20
   ```
3. Send a small burst of requests (example using bash loop):
   ```bash
   for i in {1..30}; do
     curl -sS -X POST http://localhost:8000/api/sms \
       -H "Content-Type: application/json" \
       -d '{"phone":"+15551234567","text":"test '$i'"}' &
   done
   wait
   ```
4. Observe repository logs (app + worker) and Redis keys; you should see some requests rejected/queued due to rate limits and provider health events if provider mocks are failing.

## Running tests

Run full test suite:

```bash
# from project root
pytest -q
```

Run targeted tests (quick reviewer checks):

```bash
# Health tracker tests
pytest tests/test_health_tracker.py::TestProviderHealthTracker -q

# Rate limiter high-load scenario
pytest tests/test_rate_limiter_high_load.py -q -k "rate_limiter"

# Distribution / queue focused tests
pytest tests/test_queue.py::TestQueue -q
```

Notes:
- Many tests use `redis.AsyncMock` fixtures; they run fast and do not require a real Redis instance.
- Integration tests that simulate end-to-end behavior provide in-memory mocks for Redis to make results deterministic for CI/review.

## Architecture (short)

- FastAPI app (`src/main.py`) exposes the `/api/sms` endpoint that validates and queues requests.
- TaskIQ (Redis broker) is used to process tasks asynchronously (worker in `src/worker.py`).
- Rate limiting is implemented in [`src/rate_limiter.py`](src/rate_limiter.py) and enforced both at middleware and distribution layers.
- Provider distribution and health-aware routing implemented in [`src/distribution.py`](src/distribution.py) and [`src/health_tracker.py`](src/health_tracker.py).
- Retries and exponential backoff handled in [`src/retry_service.py`](src/retry_service.py).

## Troubleshooting (quick)

- If the worker doesn't pick tasks: ensure Redis is reachable at `TASKIQ_BROKER_URL`.
- If rate limits behave unexpectedly: check `RATE_LIMIT_WINDOW` and ensure provider keys are expiring (per-window).
- To debug health tracking issues, temporarily lower `HEALTH_WINDOW_DURATION` and enable `DEBUG` to see logs.

## Contributing

Follow branch/PR conventions in `AGENTS.md`. Create a feature branch, include tests for new behavior, and ensure CI passes.
