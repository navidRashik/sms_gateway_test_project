FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
RUN pip install --upgrade pip && pip install uv

# Copy project files
COPY pyproject.toml ./
COPY uv.lock ./

# Install Python dependencies using uv
RUN uv sync --no-dev --frozen

# Copy application code
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Create non-root user and data directory
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the application using uv run
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
