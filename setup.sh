#!/bin/bash

# SMS Gateway Monorepo Setup Script
# This script helps you get started with the monorepo

set -e

echo "🚀 SMS Gateway Monorepo Setup"
echo "================================"
echo ""

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check Docker
if ! command_exists docker; then
    echo "❌ Docker is not installed. Please install Docker Desktop:"
    echo "   https://www.docker.com/products/docker-desktop"
    exit 1
fi
echo "✅ Docker found"

# Check Docker Compose
if ! command_exists docker-compose && ! docker compose version >/dev/null 2>&1; then
    echo "❌ Docker Compose is not installed."
    exit 1
fi
echo "✅ Docker Compose found"

# Optional: Check VS Code for DevContainer
if command_exists code; then
    echo "✅ VS Code found - You can use DevContainer!"
    echo "   To use DevContainer:"
    echo "   1. Install 'Dev Containers' extension"
    echo "   2. Press F1 → 'Dev Containers: Reopen in Container'"
else
    echo "ℹ️  VS Code not found - DevContainer option not available"
fi

echo ""
echo "📦 What would you like to do?"
echo ""
echo "1) Start all services with Docker Compose (recommended)"
echo "2) Install dependencies locally (for local development)"
echo "3) Run tests"
echo "4) View project structure"
echo "5) Exit"
echo ""

read -p "Enter your choice (1-5): " choice

case $choice in
    1)
        echo ""
        echo "🐳 Starting all services with Docker Compose..."
        echo "This may take a few minutes on first run..."
        echo ""
        docker-compose up --build
        ;;
    2)
        echo ""
        echo "📦 Installing dependencies locally..."
        echo ""

        # Gateway dependencies
        echo "Installing Gateway (Python) dependencies..."
        if command_exists uv; then
            cd apps/gateway
            uv sync
            cd ../..
            echo "✅ Gateway dependencies installed"
        else
            echo "⚠️  'uv' not found. Install it with:"
            echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
        fi

        # Provider dependencies
        echo ""
        echo "Installing Provider (Node.js) dependencies..."
        if command_exists npm; then
            cd apps/providers
            npm install
            cd ../..
            echo "✅ Provider dependencies installed"
        else
            echo "⚠️  'npm' not found. Install Node.js from https://nodejs.org/"
        fi

        echo ""
        echo "✅ Setup complete! You can now run:"
        echo "   Gateway: cd apps/gateway && source .venv/bin/activate && uvicorn src.main:app --reload"
        echo "   Provider: cd apps/providers && npm start"
        ;;
    3)
        echo ""
        echo "🧪 Running tests..."
        echo ""

        if [ -d "apps/gateway/.venv" ]; then
            cd apps/gateway
            source .venv/bin/activate
            uv run pytest
            cd ../..
        else
            echo "⚠️  Virtual environment not found. Run option 2 first."
        fi
        ;;
    4)
        echo ""
        echo "📁 Project Structure:"
        echo ""
        echo "apps/"
        echo "├── gateway/          # FastAPI SMS gateway (Python)"
        echo "│   ├── src/          # Source code"
        echo "│   ├── tests/        # Tests"
        echo "│   ├── alembic/      # DB migrations"
        echo "│   └── Dockerfile"
        echo "└── providers/        # Mock providers (Node.js)"
        echo "    ├── sms_gateway_provider.ts"
        echo "    └── Dockerfile"
        echo ""
        echo ".devcontainer/        # VS Code DevContainer config"
        echo "docker-compose.yml    # Service orchestration"
        echo "package.json          # Monorepo scripts"
        echo ""
        echo "Available npm scripts:"
        echo "  npm run dev         - Start all services"
        echo "  npm run dev:build   - Rebuild and start"
        echo "  npm run dev:down    - Stop all services"
        echo "  npm run logs        - View all logs"
        echo "  npm run test:gateway - Run tests"
        ;;
    5)
        echo "👋 Goodbye!"
        exit 0
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
