# mcporter-proxy

HTTP proxy for MCPorter — enables MCP tools from sandboxed agents with secure token management via **gloves**.

## Overview

MCPorter is a CLI tool for calling MCP (Model Context Protocol) tools. The proxy allows sandboxed agents to execute MCPorter commands through a secure HTTP API with access control and token management.

```
┌─────────────┐     HTTP      ┌─────────────┐     subprocess    ┌──────────┐
│   Agent     │ ───────────►  │  mcporter   │ ─────────────────►│ mcporter│
│  (sandbox)  │   POST /call  │   -proxy    │   mcporter call   │   CLI   │
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

1. Agent sends `mcporter-proxy call <mcptype>.<operation> ...` with header `X-MCP-Auth-Key: <agentId>-<agentKey>`
2. Server extracts `agentId` and `agentKey` from the auth key
3. Server extracts `mcptype` from the tool name (prefix before first `.`)
4. Server constructs secrets key: `<mcptype>-<agentId>-<agentKey>`
5. Server calls `gloves secrets get <key>` to retrieve the token
6. Token is cached for 5 minutes
7. Server sets the appropriate environment variable (from `mcp_env_map.json`) and runs `mcporter`

### Configuration File: `mcp_env_map.json`

Located next to `server.py`, this file maps MCP prefixes to environment variable names:

```json
{
  "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
  "gitlab": "GITLAB_TOKEN"
}
```

When a token is retrieved from gloves, it's set as the environment variable specified for that `mcptype`.

## Components

### Server (Python)

HTTP server that receives tool call requests, fetches tokens from gloves, and executes `mcporter` commands.

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MCPORTER_PROXY_PORT` | `8080` | Listening port |
| `MCPORTER_PROXY_ALLOWED_TOOLS` | `*` | Comma-separated tool patterns |
| `MCPORTER_PROXY_TIMEOUT` | `120` | Execution timeout in seconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |

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

Drop-in CLI replacement for `mcporter`. Requires `MCPROXY_AUTH_KEY` environment variable.

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MCPORTER_PROXY_URL` | `http://host.docker.internal:9022/call` | Proxy URL |
| `MCPORTER_PROXY_TIMEOUT` | `120000` | Request timeout in milliseconds |
| `MCPORTER_PROXY_RETRIES` | `2` | Number of retry attempts |
| `MCPORTER_PROXY_RETRY_DELAY` | `1000` | Delay between retries in milliseconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |
| `MCPROXY_AUTH_KEY` | **(required)** | Auth key in format `<agentId>-<agentKey>` |

**Usage:**
```bash
export MCPROXY_AUTH_KEY=borets-abc123
mcporter-proxy call github.list_repos visibility=private
```

## Docker Compose Integration

```yaml
services:
  gloves:
    image: ghcr.io/go-skida/gloves:latest
    container_name: gloves
    restart: unless-stopped
    networks:
      - openclaw-net

  mcporter-proxy:
    build: ./server
    container_name: mcporter-proxy
    ports:
      - "127.0.0.1:9022:8080"
    volumes:
      - ~/.mcporter:/root/.mcporter:ro
      - /usr/local/bin/gloves:/usr/local/bin/gloves:ro
    environment:
      - MCPORTER_PROXY_ALLOWED_TOOLS=github.*,gitlab.*,cognee.*
      - MCPORTER_PROXY_TIMEOUT=120
      - MCPORTER_PROXY_LOG_LEVEL=INFO
    restart: unless-stopped
    networks:
      - openclaw-net
```

## Setting Up Secrets in Gloves

For each agent and each MCP type, create a secret in gloves:

```bash
# Generate a secure key
AGENT_KEY=$(openssl rand -hex 32)

# Set secrets for different MCP types
gloves secrets set "github-borets-${AGENT_KEY}" --value "ghp_xxxxxxxxxxxx"
gloves secrets set "gitlab-borets-${AGENT_KEY}" --value "glpat-yyyyyyyyyyyy"
```

The agent will use: `MCPROXY_AUTH_KEY=borets-${AGENT_KEY}`

## Adding New MCP Types

1. Add the mapping to `mcp_env_map.json`:

```bash
# Example: adding Confluence and Jira
echo '{"github":"GITHUB_PERSONAL_ACCESS_TOKEN","gitlab":"GITLAB_TOKEN","confluence":"CONFLUENCE_API_TOKEN","jira":"JIRA_API_TOKEN"}' > server/mcp_env_map.json
```

2. Create secrets in gloves:

```bash
gloves secrets set "confluence-borets-${AGENT_KEY}" --value "conf_zzzz"
gloves secrets set "jira-borets-${AGENT_KEY}" --value "jira_token"
```

3. Restart the proxy: `docker compose restart mcporter-proxy`

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
- Tokens fetched from gloves secrets manager (not stored in proxy)
- Token caching (5 min TTL) reduces gloves calls
- `mcporter` credentials mounted read-only from host
- No external network exposure (binds to localhost in Docker)
- Auth key format: `<agentId>-<agentKey>` (no secrets in transit)
