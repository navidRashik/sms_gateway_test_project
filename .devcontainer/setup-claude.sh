#!/bin/bash
set -e

echo "🔧 Setting up Claude CLI with Vertex AI..."

# Verify environment variables
if [ -z "$ANTHROPIC_VERTEX_PROJECT_ID" ]; then
    echo "❌ Error: ANTHROPIC_VERTEX_PROJECT_ID is not set"
    exit 1
fi

if [ -z "$VERTEX_REGION_CLAUDE_4_5_SONNET" ]; then
    echo "❌ Error: VERTEX_REGION_CLAUDE_4_5_SONNET is not set"
    exit 1
fi

# Verify gcloud credentials exist
if [ ! -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "❌ Error: Google Application Default Credentials not found at $GOOGLE_APPLICATION_CREDENTIALS"
    echo "Please run 'gcloud auth application-default login' on your host machine"
    exit 1
fi

echo "✅ Vertex AI Project: $ANTHROPIC_VERTEX_PROJECT_ID"
echo "✅ Region: $VERTEX_REGION_CLAUDE_4_5_SONNET"
echo "✅ Credentials: $GOOGLE_APPLICATION_CREDENTIALS"

# Test Claude CLI
echo ""
echo "🧪 Testing Claude CLI..."
if command -v claude &> /dev/null; then
    echo "✅ Claude CLI is installed"
    claude --version
else
    echo "❌ Claude CLI not found"
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f "/workspace/.env" ] && [ -f "/workspace/.env.example" ]; then
    echo ""
    echo "📝 Creating .env from .env.example..."
    cp /workspace/.env.example /workspace/.env
    echo "✅ Created .env - please update with your actual values"
fi

echo ""
echo "✅ Setup complete! You can now use:"
echo "   - claude --help               # View Claude CLI help"
echo "   - claude copilot              # Start copilot mode"
echo "   - claude chat                 # Start chat mode"
