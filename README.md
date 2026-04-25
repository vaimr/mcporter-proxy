# mcporter-proxy

HTTP proxy for MCPorter — enables MCP tools from sandboxed agents with secure token management via **gloves**.

## Overview

MCPorter is a CLI tool for calling MCP (Model Context Protocol) tools. The proxy allows sandboxed agents to execute MCPorter commands through a secure HTTP API with access control and token management.

```
┌─────────────┐     HTTP      ┌─────────────┐     gloves run     ┌──────────┐
│   Agent     │ ───────────►  │  mcporter   │ ─────────────────►│ mcporter│
│  (sandbox)  │   POST /call  │   -proxy    │   --env VAR=...    │   CLI   │
└─────────────┘               └─────────────┘                   └──────────┘
                                      │
                                      ▼
                               ┌─────────────┐
                               │   gloves    │
                               │  (secrets)  │
                               └─────────────┘
```

## Architecture

### Token Flow

1. Agent sends `mcporter-proxy call <mcptype>.<operation> ...` with header `X-MCP-Auth-Key: <agentId>-<agentKey>` (or `<agentId>/<agentKey>`)
2. Server extracts `agentId` and `agentKey` from the auth key (auto-detects separator: `-` or `/`)
3. Server extracts `mcptype` from the tool name (prefix before first `.`)
4. Server constructs secrets key: `<prefix><sep><agentId><sep><mcptype><sep><agentKey>` (separator from auth key or `MCPROXY_SECRETS_SEPARATOR` if set)
5. If `mcp_env_map[mcptype]` is an array, server generates multiple `--env` flags (one per element)
6. Server runs `gloves --agent {agentId} run --env {VAR}=gloves://{secrets_key} -- mcporter call ...`
7. `gloves` injects the secret as an environment variable and executes `mcporter`

### Configuration File: `mcp_env_map.json`

Located next to `server.py`, this file maps MCP prefixes to environment variable names. Values must be arrays:

```json
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "gitlab": ["GITLAB_TOKEN"],
  "confluence": ["CONFLUENCE_TOKEN", "CONFLUENCE_TOKEN_1", "CONFLUENCE_TOKEN_2"]
}
```

When a token is retrieved from gloves, it's injected as the environment variable specified for that `mcptype`. If multiple env vars are specified (array), server generates multiple `--env` flags with keys: `prefix/agentId/mcptype/agentKey` (index 0) and `prefix/agentId/mcptype/agentKey/1`, `prefix/agentId/mcptype/agentKey/2` (indices 1, 2, ...).

## Components

### Server (Python)

HTTP server that receives tool call requests, runs commands via `gloves run`, and executes `mcporter` commands.

**Environment variables:**

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MCPORTER_PROXY_PORT` | `8080` | Listening port |
| `MCPORTER_PROXY_ALLOWED_TOOLS` | `*` | Comma-separated tool patterns |
| `MCPORTER_PROXY_TIMEOUT` | `120` | Execution timeout in seconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `MCPROXY_SECRETS_PREFIX` | `agents` | Prefix for secrets keys |
| `MCPROXY_SECRETS_SEPARATOR` | _(auto)_ | Separator for secrets keys; if empty, uses separator from auth key (`-` or `/`) |

**Endpoint:** `POST /call`

Request body:
```json
{
  "tool": "github.list_repos",
  "args": { "visibility": "private" }
}
```

Response:
```json
{
  "stdout": "...",
  "stderr": "...",
  "returncode": 0
}
```

### Client (Node.js)

Drop-in CLI replacement for `mcporter`. Requires `MCPORTER_PROXY_AUTH_KEY` environment variable.

**Environment variables:**

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MCPORTER_PROXY_URL` | `http://host.docker.internal:9022/call` | Proxy URL |
| `MCPORTER_PROXY_TIMEOUT` | `120000` | Request timeout in milliseconds |
| `MCPORTER_PROXY_RETRIES` | `2` | Number of retry attempts |
| `MCPORTER_PROXY_RETRY_DELAY` | `1000` | Delay between retries in milliseconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |
| `MCPORTER_PROXY_AUTH_KEY` | **(required)** | Auth key: `<agentId>-<agentKey>` or `<agentId>/<agentKey>` |

**Usage:**
```bash
export MCPORTER_PROXY_AUTH_KEY=borets-abc123
mcporter-proxy call github.list_repos visibility=private
```

## Docker Compose Integration

### Gloves Secrets Storage

Gloves stores secrets encrypted at `~/.openclaw/secrets/` on the host. This directory must be mounted into the `mcporter-proxy` container for gloves to access the secrets.

```yaml
services:
  mcporter-proxy:
    build: ./server
    container_name: mcporter-proxy
    ports:
      - "127.0.0.1:9022:8080"
    volumes:
      - ~/.mcporter:/root/.mcporter:ro
      - ~/.openclaw/secrets:/root/.openclaw/secrets:ro
    environment:
      - MCPORTER_PROXY_ALLOWED_TOOLS=github.*,gitlab.*,atlassian.jira_*,atlassian.confluence_*,cognee.*
      - MCPORTER_PROXY_TIMEOUT=120
      - MCPORTER_PROXY_LOG_LEVEL=INFO
    restart: unless-stopped
```

### Initial Setup

1. Initialize gloves on the host:

```bash
# Install gloves CLI (if not already installed)
curl -fsSL https://github.com/heyAyushh/gloves/releases/download/v0.5.11/gloves-0.5.11-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C /usr/local/bin
chmod +x /usr/local/bin/gloves

# Initialize gloves runtime (creates ~/.openclaw/secrets and ~/.openclaw/.gloves.toml)
gloves bootstrap
```

2. Create agent identity and secrets:

```bash
# Generate a secure key
AGENT_KEY=$(openssl rand -hex 32)

# Create agent identity in gloves (agent_id matches your auth key prefix)
gloves set-identity --agent borets

# Set secrets for different MCP types, scoped to agent
# Format: prefix/agentId/mcptype/agentKey (default prefix is "agents")
gloves --agent borets set "agents/borets/github/${AGENT_KEY}" --value "ghp_xxxxxxxxxxxx"
gloves --agent borets set "agents/borets/gitlab/${AGENT_KEY}" --value "glpat-yyyyyyyyyyyy"
```

The agent will use: `MCPORTER_PROXY_AUTH_KEY=borets-${AGENT_KEY}` or `MCPORTER_PROXY_AUTH_KEY=borets/${AGENT_KEY}`

## Adding New MCP Types

1. Add the mapping to `mcp_env_map.json`:

```bash
# Example: adding Confluence and Jira
cat > server/mcp_env_map.json << 'EOF'
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "gitlab": ["GITLAB_TOKEN"],
  "confluence": ["CONFLUENCE_API_TOKEN"],
  "jira": ["JIRA_API_TOKEN", "JIRA_API_TOKEN_1"]
}
EOF
```

2. Create secrets in gloves:

```bash
# Single token MCP
gloves --agent borets set "agents/borets/confluence/${AGENT_KEY}" --value "conf_zzzz"

# Multiple tokens MCP (indices 0, 1, ...)
gloves --agent borets set "agents/borets/jira/${AGENT_KEY}" --value "jira_token"
gloves --agent borets set "agents/borets/jira/${AGENT_KEY}/1" --value "jira_token_2"
```

3. Rebuild and restart the proxy: `docker compose build mcporter-proxy && docker compose restart mcporter-proxy`

## Running Tests

```bash
make test          # Run all tests with mock server
make test-client   # Run client unit tests
make test-server   # Run server unit tests
```

Or manually:
```bash
# Start mock server
node tests/mock_server.js &

# Run client tests
MCPORTER_PROXY_URL=http://localhost:9023/call node tests/test_client.js

# Run server tests
python3 tests/test_server.py
```

## Security

- Tool whitelist via `MCPORTER_PROXY_ALLOWED_TOOLS` pattern matching
- Tokens never stored in proxy code or environment — fetched live from gloves
- `gloves run` injects secrets directly as environment variables (not passed as CLI args)
- `mcporter` credentials mounted read-only from host
- Gloves secrets mounted read-only from host (`~/.openclaw/secrets:ro`)
- No external network exposure (binds to localhost in Docker)
- Auth key format: `<agentId>-<agentKey>` or `<agentId>/<agentKey>` (no secrets transmitted, only reference)
