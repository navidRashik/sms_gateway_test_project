# Twillow SMS Gateway

High-throughput SMS gateway using FastAPI, Redis, TaskIQ, and SQLite. Handles 200 RPS by queuing requests and distributing them across 3 providers (50 RPS each).

## Quickstart (Docker Compose)

Start services (Redis, mock providers, app):

```bash
docker-compose up --build
```

App will be available at http://localhost:8000 and OpenAPI at http://localhost:8000/docs

## Local development (venv)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
uvicorn "src.main:app" --reload --host 0.0.0.0 --port 8000
```

## Run TaskIQ worker

```bash
python -m src.worker
```

## Environment variables

See `src/config.py` for defaults. Typical `.env`:

```bash
REDIS_URL=redis://localhost:6379
TASKIQ_BROKER_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./data/sms_gateway.db
PROVIDER1_URL=http://localhost:8071/api/sms/provider1
PROVIDER2_URL=http://localhost:8072/api/sms/provider2
PROVIDER3_URL=http://localhost:8073/api/sms/provider3
DEBUG=true
```

## API

OpenAPI UI: `http://localhost:8000/docs` — includes full request/response schemas and example payloads.

Main endpoints:
- POST /api/sms/send — queue SMS (payload: phone, text)
- POST /api/sms/ — alias for send
- GET /api/sms/rate-limits — rate limit status
- GET /api/sms/health — providers health
- GET /api/sms/health/{provider_id} — provider-specific health
- POST /api/sms/health/{provider_id}/reset — reset provider health
- GET /api/sms/distribution-stats — distribution stats
- POST /api/sms/distribution-stats/reset — reset distribution stats
- GET /api/sms/requests — list requests
- GET /api/sms/requests/{request_id} — details for a request
- Provider direct endpoints: POST /api/sms/provider1, /provider2, /provider3
- GET /taskiq-status — TaskIQ broker status

Examples:

```bash
curl -X POST http://localhost:8000/api/sms/send \
  -H "Content-Type: application/json" \
  -d '{"phone":"01921317475","text":"Hello"}'
```

## Architecture (short)

Components: FastAPI app, Redis (broker + counters), TaskIQ workers, SQLite persistence, provider services.

Data flow: Client -> FastAPI -> Redis counters -> DB + TaskIQ enqueue -> Worker -> Distribution service -> Provider -> DB

## Troubleshooting (quick)

- Redis unreachable: check docker-compose and REDIS_URL
- Tasks not processed: ensure worker is running and TASKIQ_BROKER_URL matches Redis
- 429 responses: global rate limit hit; lower client RPS or adjust limits
- Provider timeouts/unreachable: verify PROVIDER*_URL and provider service logs
- SQLite locked: stop other processes accessing DB or use Postgres for multi-dev

## Tests

```bash
pytest -q
```

## Contributing

See `.agents/` for planning and task lifecycle. Open PRs against `develop` or `main` per repo policy.

## License

MIT