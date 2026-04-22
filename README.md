# mcporter-proxy

HTTP proxy for MCPorter — enables MCP tools from sandboxed agents.

## Overview

MCPorter is a CLI tool for calling MCP (Model Context Protocol) tools. The proxy allows sandboxed agents to execute MCPorter commands through a secure HTTP API with access control.

```
┌─────────────┐     HTTP      ┌─────────────┐     subprocess    ┌──────────┐
│   Agent     │ ───────────►  │  mcporter   │ ─────────────────►│ mcporter│
│  (sandbox)  │   POST /call  │   -proxy    │   mcporter call   │   CLI   │
└─────────────┘               └─────────────┘                   └──────────┘
```

## Components

### Server (Python)

HTTP server that receives tool call requests and executes `mcporter` commands.

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MCPORTER_PROXY_PORT` | `8080` | Listening port |
| `MCPORTER_PROXY_ALLOWED_TOOLS` | `*` | Comma-separated tool patterns (e.g., `atlassian.jira_*,cognee.*`) — **not recommended** in production |
| `MCPORTER_PROXY_TIMEOUT` | `120` | Execution timeout in seconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |

**Endpoint:** `POST /call`

Request body:
```json
{
  "tool": "cognee.list_data",
  "args": { "key": "value" }
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

Drop-in CLI replacement for `mcporter`. Accepts the same arguments but routes requests through the proxy.

**Usage:**
```bash
mcporter-proxy call cognee.search search_query="test" top_k=5
mcporter-proxy call 'cognee.search(search_query: "test", top_k: 5)'
```

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MCPORTER_PROXY_URL` | `http://host.docker.internal:9022/call` | Proxy URL |
| `MCPORTER_PROXY_TIMEOUT` | `120000` | Request timeout in milliseconds |
| `MCPORTER_PROXY_RETRIES` | `2` | Number of retry attempts |
| `MCPORTER_PROXY_RETRY_DELAY` | `1000` | Delay between retries in milliseconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |

## Building

```bash
make build          # Build all components
make build-server   # Build server Docker image
make build-client   # Build client npm package
```

## Docker Compose Integration

```yaml
services:
  mcporter-proxy:
    build: ./server
    container_name: mcporter-proxy
    ports:
      - "127.0.0.1:9022:8080"
    volumes:
      - ~/.mcporter:/root/.mcporter:ro
    environment:
      - MCPORTER_PROXY_ALLOWED_TOOLS=atlassian.jira_*,atlassian.confluence_*,cognee.*
      - MCPORTER_PROXY_TIMEOUT=120
      - MCPORTER_PROXY_LOG_LEVEL=INFO
    restart: unless-stopped
```

## Running Tests

```bash
make test          # Run tests with mock MCP server
make test-client   # Run client unit tests (requires mock server)
make test-skills   # Run integration tests (requires real mcporter + external services)
make mock-start    # Start mock server manually
```

Or manually:
```bash
# Start mock server
node tests/mock_server.js &

# Run tests
MCPORTER_PROXY_URL=http://localhost:9023/call node tests/test_client.js
```

## Security

- Tool whitelist via `MCPORTER_PROXY_ALLOWED_TOOLS` pattern matching
- `mcporter` credentials mounted read-only from host
- No external network exposure (binds to localhost in Docker)