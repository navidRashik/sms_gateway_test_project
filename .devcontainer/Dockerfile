FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /workspace

# Install system dependencies including Claude CLI prerequisites
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    git \
    make \
    sqlite3 \
    nodejs \
    npm \
    build-essential \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
RUN pip install --upgrade pip && pip install uv

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /workspace

USER app

# Set up Python environment location
ENV PATH="/workspace/apps/gateway/.venv/bin:$PATH"

CMD ["/bin/bash"]
