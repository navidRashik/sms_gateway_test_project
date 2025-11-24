FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    git \
    make \
    sqlite3 \
    nodejs \
    npm \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
RUN pip install --upgrade pip && pip install uv

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /workspace

USER app

# Set up Python environment location
ENV PATH="/workspace/apps/gateway/.venv/bin:$PATH"

CMD ["/bin/bash"]
