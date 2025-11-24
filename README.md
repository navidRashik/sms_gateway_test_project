
# SMS Gateway Monorepo

High-throughput SMS gateway monorepo with FastAPI (Python), Node.js providers, Redis, TaskIQ, and SQLite. Designed to queue incoming SMS requests and distribute them across multiple providers while enforcing per-provider and global rate limits.

## 📁 Monorepo Structure

```
.
├── apps/
│   ├── gateway/          # FastAPI SMS gateway service (Python)
│   │   ├── src/          # Application source code
│   │   ├── tests/        # Test suite
│   │   ├── alembic/      # Database migrations
│   │   ├── Dockerfile    # Gateway container
│   │   └── pyproject.toml
│   └── providers/        # Mock SMS/Email provider service (Node.js)
│       ├── sms_gateway_provider.ts
│       ├── Dockerfile
│       └── package.json
├── .devcontainer/        # VS Code devcontainer configuration
├── docker-compose.yml    # Multi-service orchestration
└── package.json          # Monorepo scripts
```

## 🚀 Quick Start

### Option 1: Using DevContainer (Recommended)

The devcontainer includes Claude CLI with Vertex AI support for AI-assisted development.

1. **Install Prerequisites:**
   - [Docker Desktop](https://www.docker.com/products/docker-desktop)
   - [VS Code](https://code.visualstudio.com/)
   - [Dev Containers Extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
   - [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (for Vertex AI)

2. **Set up Vertex AI (One-time setup):**
   ```bash
   # Authenticate with Google Cloud
   gcloud auth application-default login

   # Add to your ~/.zshrc or ~/.bashrc:
   export CLAUDE_CODE_USE_VERTEX=1
   export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
   export VERTEX_REGION_CLAUDE_4_5_SONNET=asia-southeast1
   ```

3. **Open in DevContainer:**
   ```bash
   # Clone the repository
   git clone <your-repo-url>
   cd sms_gateway_test_project

   # Open in VS Code
   code .
   ```

4. **Reopen in Container:**
   - Press `F1` or `Cmd+Shift+P` (Mac) / `Ctrl+Shift+P` (Windows/Linux)
   - Select: `Dev Containers: Reopen in Container`
   - Wait for the container to build and start

5. **Test Claude CLI (Inside container):**
   ```bash
   # Run the test script
   bash .devcontainer/test-claude.sh

   # Start copilot mode
   claude copilot
   ```

6. **Start All Services:**
   ```bash
   # Inside the devcontainer terminal
   docker-compose up --build
   ```

For more details on the devcontainer setup, see [.devcontainer/README.md](.devcontainer/README.md).

5. **Access the Services:**
   - Gateway API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs
   - Provider 1: http://localhost:8071
   - Provider 2: http://localhost:8072
   - Provider 3: http://localhost:8073

### Option 2: Using Docker Compose (Without DevContainer)

1. **Install Prerequisites:**
   - [Docker Desktop](https://www.docker.com/products/docker-desktop)

2. **Start All Services:**
   ```bash
   # From the project root
   docker-compose up --build
   ```

3. **Access the Services:**
   - Gateway API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

### Option 3: Local Development (Manual Setup)

#### Gateway Service (Python)

```bash
cd apps/gateway

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Activate virtual environment
source .venv/bin/activate

# Run the application
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

#### Provider Service (Node.js)

```bash
cd apps/providers

# Install dependencies
npm install

# Run the provider service
npm start
```

**Note:** You'll also need Redis running locally for the full stack:
```bash
docker run -d -p 6379:6379 redis:7-alpine
```

## 📦 Available NPM Scripts

From the root directory:

```bash
# Start all services
npm run dev

# Start all services (rebuild images)
npm run dev:build

# Stop all services
npm run dev:down

# Stop all services and remove volumes
npm run dev:clean

# View all logs
npm run logs

# View specific service logs
npm run logs:gateway
npm run logs:providers
npm run logs:workers

# Run tests
npm run test:gateway
npm run test:providers

# Install dependencies
npm run install:gateway
npm run install:providers
npm run install:all
```

## 🏗️ Architecture

### Services

1. **Gateway** (`apps/gateway/`)
   - FastAPI application
   - Handles SMS request validation and queueing
   - Manages rate limiting and provider distribution
   - Port: 8000

2. **Providers** (`apps/providers/`)
   - Three mock provider instances (Node.js/TypeScript)
   - Simulates SMS/Email sending with random failures
   - Ports: 8071-8073 (SMS), 8091-8093 (Email)

3. **Redis**
   - Message broker for TaskIQ
   - Rate limiting storage
   - Port: 6379

4. **Workers**
   - Background task processors (2 replicas)
   - Process SMS sending tasks asynchronously

5. **Scheduler**
   - TaskIQ scheduler for periodic tasks
   - Health tracking and metrics

### Key Components

- **Rate Limiting**: Per-provider and global rate limits using Redis (`apps/gateway/src/rate_limiter.py`)
- **Provider Distribution**: Health-aware routing (`apps/gateway/src/distribution.py`)
- **Health Tracking**: Monitor provider success/failure rates (`apps/gateway/src/health_tracker.py`)
- **Retry Service**: Exponential backoff for failed messages (`apps/gateway/src/retry_service.py`)

## ⚙️ Configuration

Configuration is driven by environment variables. Create a `.env` file in the root directory:

```bash
# Redis
REDIS_URL=redis://redis:6379
TASKIQ_BROKER_URL=redis://redis:6379

# Database
DATABASE_URL=sqlite:///./data/sms_gateway.db

# Provider URLs (for Docker Compose)
PROVIDER1_URL=http://provider1:8071/api/sms/provider1
PROVIDER2_URL=http://provider2:8072/api/sms/provider2
PROVIDER3_URL=http://provider3:8073/api/sms/provider3

# Rate Limiting
PROVIDER_RATE_LIMIT=50
TOTAL_RATE_LIMIT=200
RATE_LIMIT_WINDOW=1

# Health Tracking
HEALTH_WINDOW_DURATION=300

# Debug
DEBUG=true
```

See `apps/gateway/src/config.py` for all available configuration options.

## 🧪 Testing

### Run All Tests

```bash
# Gateway tests
cd apps/gateway
uv run pytest

# Or from root
npm run test:gateway
```

### Run Specific Tests

```bash
cd apps/gateway

# Health tracker tests
uv run pytest tests/test_health_tracker.py::TestProviderHealthTracker -q

# Rate limiter tests
uv run pytest tests/test_rate_limiter_high_load.py -q

# Queue tests
uv run pytest tests/test_queue.py::TestQueue -q
```

## 🐛 Troubleshooting

### Services won't start

1. Check if ports are already in use:
   ```bash
   lsof -i :8000,8071,8072,8073,6379
   ```

2. Clean up Docker resources:
   ```bash
   npm run dev:clean
   docker system prune -a
   ```

### Worker not picking up tasks

- Ensure Redis is running and accessible
- Check `TASKIQ_BROKER_URL` environment variable
- View worker logs: `npm run logs:workers`

### Rate limiting issues

- Check `RATE_LIMIT_WINDOW` setting
- Ensure Redis keys are expiring correctly
- Enable debug mode: `DEBUG=true`

### Database issues

- Database location: `apps/gateway/data/sms_gateway.db`
- Run migrations: `cd apps/gateway && uv run alembic upgrade head`

## 📝 Development Workflow

### Adding New Features

1. Create a feature branch
2. Make changes in the appropriate service (`apps/gateway` or `apps/providers`)
3. Add tests
4. Run tests locally
5. Create a pull request

### Working with DevContainer

The devcontainer provides:
- All necessary Python and Node.js dependencies pre-installed
- VS Code extensions for Python, TypeScript, Docker
- Proper linting and formatting on save
- Direct access to all services

### Debugging

1. **Gateway Service:**
   - Set breakpoints in VS Code
   - Use the built-in Python debugger
   - View logs: `docker-compose logs -f gateway`

2. **Provider Service:**
   - Add console.log statements
   - View logs: `docker-compose logs -f provider1 provider2 provider3`

## 📚 Additional Documentation

- Gateway architecture: `apps/gateway/src/` (see individual modules)
- Agent documentation: `AGENTS.md`
- API documentation: http://localhost:8000/docs (when running)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

MIT

## 🙋 Support

For issues and questions, please use the GitHub issue tracker.
