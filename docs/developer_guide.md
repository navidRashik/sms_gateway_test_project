# Developer Guide: Twillow SMS Gateway

## Overview
This document contains developer setup instructions, API documentation, architecture overview, troubleshooting guide, and a developer onboarding checklist for the Twillow SMS Gateway (FastAPI + Redis + TaskIQ + SQLite). It follows the conventions described in `AGENTS.md`.

## Goals & Success Criteria
- Run the service locally and in Docker Compose
- Send SMS requests through the queue and observe TaskIQ workers processing them
- Achieve throughput behavior consistent with provider and global rate limits
- Provide reproducible troubleshooting guidance for common failures

## Prerequisites
- Python 3.11+
- Node.js 18+ (for local provider mocks)
- Docker & Docker Compose (for running Redis and provider mocks)
- Git
- Recommended: GNU Make (optional convenience)

## Repository layout (important files)
- src/ — Python application code (FastAPI)
- docs/ — documentation (this file)
- docker-compose.yml — local stack (Redis, app, three provider mocks)
- Dockerfile / Dockerfile.nodejs — build images
- requirements.txt / pyproject.toml — Python dependencies (managed via uv)
- alembic/ — DB migrations
- .agents/ — planning & worklist (per project policy)

## Environment & Configuration
Configuration is primarily via environment variables or `.env` (see `src/config.py`):

- REDIS_URL (default: redis://localhost:6379)
- TASKIQ_BROKER_URL (default: redis://localhost:6379)
- DATABASE_URL (default: sqlite:///./data/sms_gateway.db)
- PROVIDER1_URL, PROVIDER2_URL, PROVIDER3_URL (provider endpoints)
- PROVIDER_RATE_LIMIT (default: 50)
- TOTAL_RATE_LIMIT (default: 200)
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
DEBUG=true
```

## Local Developer Setup (step-by-step)

1. Clone repository
```bash
# bash
git clone <repo-url> twillow-sms
cd twillow-sms
```

2. Python virtualenv and dependencies (prefer using uv)
```bash
# bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
# Install dependencies using uv which manages editable installs and pinned hashes.
# uv will create/enter the virtual environment and install exactly as specified by uv.lock/requirements.
uv install
# If you need a plain requirements.txt (for other tooling), uv can export it:
# uv export --format requirements-txt --output-file requirements.txt
```

3. Start Redis and provider mocks using Docker Compose (recommended)
```bash
# bash
docker-compose up --build
```
This brings up:
- redis at redis://localhost:6379
- provider1 at http://localhost:8071
- provider2 at http://localhost:8072
- provider3 at http://localhost:8073
- app at http://localhost:8000

4. Database migrations (Alembic)
```bash
# bash
export DATABASE_URL=sqlite:///./data/sms_gateway.db
alembic upgrade head
```
(If using docker-compose, the container mounts the data folder and migrations are run as part of startup in some workflows; manually run if needed.)

5. Running the FastAPI app locally (dev)
```bash
# bash
uvicorn "src.main:app" --reload --host 0.0.0.0 --port 8000
```

6. Running TaskIQ worker(s)
The project uses TaskIQ with a Redis broker. Start workers in a separate terminal (same venv):
```bash
# bash
# Example: spawn the worker defined in src/worker.py (project-specific)
python -m src.worker
```
(If a different entrypoint is used for TaskIQ in this project, use the provided script. The TaskIQ broker URL reads TASKIQ_BROKER_URL.)

7. Running tests
```bash
# bash
pytest -q
```

## API Documentation (endpoints, request/response examples)

Base URL (local): http://localhost:8000
OpenAPI UI: http://localhost:8000/docs
OpenAPI JSON: http://localhost:8000/openapi.json

Common headers:
- Content-Type: application/json

1) Health endpoints
- GET /health
  - Response: {"status":"healthy"}

2) Main SMS endpoints (queueing + rate limiting)
- POST /api/sms/
- POST /api/sms/send
  - Request body (application/json):
  ```json
  {
    "phone": "01921317475",
    "text": "Hello, this is a test SMS!"
  }
  ```
  - Successful response (queued):
  ```json
  {
    "success": true,
    "message_id": "7f3c2a2d-...-e1",
    "message": "SMS queued for sending",
    "provider": null,
    "queued": true
  }
  ```
  - Error: 429 if global limit exceeded
  - Error: 503 if no provider available

cURL example:
```bash
# bash
curl -X POST http://localhost:8000/api/sms/send \
  -H "Content-Type: application/json" \
  -d '{"phone":"01921317475","text":"Hello"}'
```

3) Rate-limit status
- GET /api/sms/rate-limits
  - Returns provider and global counts and flags.

4) Provider health & control
- GET /api/sms/health
  - List health for all providers
- GET /api/sms/health/{provider_id}
  - Example: /api/sms/health/provider1
- POST /api/sms/health/{provider_id}/reset
  - Reset health metrics for testing/admins

5) Distribution & stats
- GET /api/sms/distribution-stats
  - Returns weighted round-robin and provider usage statistics
- POST /api/sms/distribution-stats/reset
  - Reset distribution stats for tests

6) Requests and responses
- GET /api/sms/requests?limit=50&status=pending
  - Filter SMS requests
- GET /api/sms/requests/{request_id}
  - Get detailed request and associated provider responses

7) Provider direct endpoints (bypass queue)
- POST /api/sms/provider1
- POST /api/sms/provider2
- POST /api/sms/provider3
  - Same request payload as /api/sms/send
  - Intended for testing and immediate sends; these call the mock provider endpoints configured by PROVIDER*_URL.

Provider list and status endpoints:
- GET /api/sms/providers
- GET /api/sms/provider1/status
- ... provider2/status, provider3/status

## Example flow (end-to-end)
1. Client POST /api/sms/send with payload.
2. FastAPI checks global rate limits (TaskIQ/Redis counters) and provider availability.
3. If allowed, queue_sms_task saves request record and enqueues TaskIQ job.
4. TaskIQ worker picks up job, distribution service selects a provider respecting per-provider RPS and health metrics, sends HTTP request to provider endpoint.
5. Worker stores provider response into responses table; request status updated.

## Architecture Overview

Components
- FastAPI app (src/) — HTTP API, input validation, rate-limit checks, persistence.
- Redis — central broker + rate limit counters, queueing and ephemeral counters.
- TaskIQ (Redis-backed) — asynchronous job execution, retries and concurrency control.
- SQLite — durable persistence for requests/responses (local dev store; production would use Postgres).
- Providers (provider1/2/3) — external SMS provider endpoints (mocked in this repo as Node services).
- Rate limiter (src/rate_limiter.py) — per-provider and global token-bucket / fixed-window counters stored in Redis.
- Distribution service (src/distribution.py) — weighted/round-robin selection with health-awareness.
- Health tracker (src/health_tracker.py) — sliding window of provider success/failure to mark unhealthy.

Data flow (simplified)
Client -> FastAPI (validation & rate checks) -> Persist request -> TaskIQ enqueue -> Worker -> Distribution service -> Provider HTTP -> Worker records response -> DB

ASCII diagram
```
Client
  |
  v
FastAPI (queue endpoint) -- Redis (rate counters)
  |                         ^
  v                         |
Persist request -> TaskIQ broker (Redis) -> Worker(s)
                                      |
                                Distribution service
                                      |
                                Provider HTTP endpoints
                                      |
                                Provider responses -> Worker -> DB
```

Rate handling summary
- Each provider: 50 RPS (configurable via PROVIDER_RATE_LIMIT)
- Global total: 200 RPS (TOTAL_RATE_LIMIT)
- FastAPI performs quick checks before enqueueing to avoid overwhelming queue
- Worker uses distribution service to send respecting per-provider limits and provider health

## Troubleshooting Guide (common issues & resolutions)

1. Redis connection failures
- Symptom: "ConnectionRefusedError", app fails on startup
- Fix:
  - Ensure Redis is running: docker-compose up redis or check local service
  - Verify REDIS_URL in `.env` matches the running redis (host/port)
  - Confirm network (when running in docker-compose, app uses `redis` hostname)

2. TaskIQ workers not processing tasks
- Symptom: Jobs remain in queue, no outbound requests
- Fix:
  - Start worker: python -m src.worker (or the project's provided worker entrypoint)
  - Ensure TASKIQ_BROKER_URL points to Redis used by the app
  - Check worker logs for exceptions
  - Verify TaskIQ package compatibility and that broker startup completed

3. 429 Too Many Requests (global rate limit)
- Symptom: App returns HTTP 429 with details about current_count/limit
- Fix:
  - Reduce client request rate
  - Increase TOTAL_RATE_LIMIT only if providers can sustain higher throughput
  - Confirm counters are resetting after rate_limit_window (default 1s)

4. Provider unreachable or timeouts
- Symptom: Worker logs show timeouts, provider marked unhealthy
- Fix:
  - Verify provider URL (PROVIDER*_URL) is reachable (curl)
  - Check provider service logs (Node mocks)
  - Ensure provider health threshold & time window are appropriate

5. Database locked or migration issues (SQLite)
- Symptom: "database is locked" or Alembic migration errors
- Fix:
  - For development: stop other processes accessing SQLite file
  - Re-run migrations: alembic upgrade head
  - Consider using Postgres in multi-developer or production environments

6. CORS / Browser issues when testing UI
- Symptom: Browser blocks requests from web UI
- Fix:
  - App uses permissive CORS for development; check CORSMiddleware settings in `src/main.py`

7. Unexpected 500s in request handling
- Symptom: 500 internal errors
- Fix:
  - Check app logs for stack traces
  - Reproduce with curl to get detailed error
  - Run unit tests (pytest) and investigate failing tests

## Operational notes
- Use health endpoints and distribution-stats to monitor provider availability
- For production, replace SQLite with a managed DB and secure Redis
- Add metrics (Prometheus) for rate counters, queue depth, and task success/failure rates

## Developer Checklist (per AGENTS.md)
- Create/Update `.agents/plan.md` for feature work
- Add small tasks to `.agents/work_list.md` and update statuses
- Write unit tests for new logic and update `tests/` accordingly
- Run pre-commit hooks (black, ruff, isort) before PRs

## Acceptance Criteria (verification)
- Developer can follow this guide to start the stack and run sample requests
- API endpoints listed above are discoverable in /docs
- Architecture overview maps to the code (src/)
- Troubleshooting section resolves common developer problems

## Contact & Notes
For questions about the agent conventions and worklist, consult `AGENTS.md` and `.agents/work_list.md`.