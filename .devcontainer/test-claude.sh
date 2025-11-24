#!/bin/bash
# Test script for Claude CLI with Vertex AI

echo "🧪 Testing Claude CLI Configuration"
echo "===================================="
echo ""

# Test 1: Check Claude CLI installation
echo "1️⃣  Testing Claude CLI installation..."
if command -v claude &> /dev/null; then
    echo "   ✅ Claude CLI is installed"
    claude --version
else
    echo "   ❌ Claude CLI not found"
    exit 1
fi
echo ""

# Test 2: Check environment variables
echo "2️⃣  Checking environment variables..."
check_env() {
    if [ -z "${!1}" ]; then
        echo "   ❌ $1 is not set"
        return 1
    else
        echo "   ✅ $1 = ${!1}"
        return 0
    fi
}

check_env "CLAUDE_CODE_USE_VERTEX"
check_env "ANTHROPIC_VERTEX_PROJECT_ID"
check_env "VERTEX_REGION_CLAUDE_4_5_SONNET"
check_env "GOOGLE_APPLICATION_CREDENTIALS"
echo ""

# Test 3: Check Google Cloud credentials
echo "3️⃣  Checking Google Cloud credentials..."
if [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "   ✅ Credentials file exists at: $GOOGLE_APPLICATION_CREDENTIALS"

    # Check if file is readable
    if [ -r "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
        echo "   ✅ Credentials file is readable"

        # Verify JSON format
        if python3 -m json.tool "$GOOGLE_APPLICATION_CREDENTIALS" &> /dev/null; then
            echo "   ✅ Credentials file is valid JSON"
        else
            echo "   ⚠️  Credentials file may not be valid JSON"
        fi
    else
        echo "   ❌ Credentials file is not readable"
    fi
else
    echo "   ❌ Credentials file not found at: $GOOGLE_APPLICATION_CREDENTIALS"
fi
echo ""

# Test 4: Test Claude CLI with simple command
echo "4️⃣  Testing Claude CLI with --help..."
if claude --help &> /dev/null; then
    echo "   ✅ Claude CLI help command works"
else
    echo "   ❌ Claude CLI help command failed"
fi
echo ""

# Test 5: Test copilot mode availability
echo "5️⃣  Checking copilot mode availability..."
if claude copilot --help &> /dev/null; then
    echo "   ✅ Copilot mode is available"
    echo ""
    echo "   To start copilot mode, run:"
    echo "   $ claude copilot"
else
    echo "   ⚠️  Could not verify copilot mode (this may be normal)"
fi
echo ""

echo "===================================="
echo "✅ All tests completed!"
echo ""
echo "Quick start commands:"
echo "  claude copilot          # Start AI coding assistant"
echo "  claude chat             # Start chat mode"
echo "  claude --help           # View all commands"
