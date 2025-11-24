# DevContainer Configuration

This devcontainer is configured for the SMS Gateway Monorepo with Claude CLI and Vertex AI support.

## What's Included

- **Python 3.11** with uv package manager
- **Node.js & npm** for provider service
- **Claude CLI** with Vertex AI integration
- **Development tools**: git, make, sqlite3, docker-in-docker
- **VS Code extensions**: Python, Claude Code, GitLens, Docker, and more

## Environment Variables

The following environment variables are automatically passed from your host machine to the container:

- `CLAUDE_CODE_USE_VERTEX` - Enables Vertex AI for Claude
- `ANTHROPIC_VERTEX_PROJECT_ID` - Your GCP project ID
- `VERTEX_REGION_CLAUDE_4_5_SONNET` - Vertex AI region
- `CLAUDE_CODE_ENABLE_TELEMETRY` - Enable telemetry
- `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` - OpenTelemetry endpoint
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to GCP credentials

### Setting Up Environment Variables on Host

Add these to your `~/.zshrc` or `~/.bashrc`:

```bash
# Claude / Vertex AI configuration
export CLAUDE_CODE_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
export VERTEX_REGION_CLAUDE_4_5=asia-southeast1
export VERTEX_REGION_CLAUDE_4_5_SONNET=asia-southeast1
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=your-endpoint
```

## Google Cloud Credentials

Your `~/.config/gcloud` directory is mounted into the container. This includes:
- Application Default Credentials
- gcloud configuration
- Service account keys

Make sure you've run `gcloud auth application-default login` on your host machine before building the container.

## Using Claude CLI

Once the container is running:

```bash
# View help
claude --help

# Start copilot mode
claude copilot

# Start chat mode
claude chat

# Check version
claude --version
```

## Rebuilding the Container

When you make changes to the devcontainer configuration:

1. Press `F1` or `Cmd+Shift+P`
2. Select: `Dev Containers: Rebuild Container`

## Ports

The following ports are automatically forwarded:

- **8000**: Gateway API service
- **6379**: Redis

## Troubleshooting

### Claude CLI not working

1. Verify environment variables are set on host
2. Check that `~/.config/gcloud/application_default_credentials.json` exists
3. Rebuild the container

### Permission issues with mounted files

The container runs as user `app` (non-root). If you have permission issues:

```bash
# In the container
sudo chown -R app:app /workspace
```
