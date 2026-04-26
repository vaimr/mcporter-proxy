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

Located next to `server.py`, this file maps MCP prefixes to environment variable names and attachment download configuration.

### Minimal Config (Get Started)

Just list the environment variables your MCP type needs:

```json
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "gitlab": ["GITLAB_TOKEN"],
  "jira": ["JIRA_API_TOKEN", "JIRA_API_TOKEN_1"]
}
```

### Full Config (with Attachment Download)

```json
{
  "github": {
    "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    "attachment_download": {
      "type": "rest_api",
      "method": "GET",
      "url_template": "https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}",
      "headers": {
        "Accept": "application/octet-stream",
        "Authorization": "Bearer {token}"
      }
    }
  },
  "confluence": {
    "env": ["CONFLUENCE_API_TOKEN"],
    "attachment_download": {
      "type": "mcp_tool_redirect",
      "tool_name": "atlassian.confluence_download_attachment",
      "tool_args_mapping": {
        "page_id": "{page_id}",
        "filename": "{filename}"
      },
      "download_url_field": "download_url",
      "headers": { "Authorization": "Bearer {token}" }
    }
  }
}
```

### Configuration Reference

**mcptype object (minimal):**
```json
{ "mcptype": ["ENV_VAR"] }
```

**mcptype object (full):**
```json
{
  "mcptype": {
    "env": ["ENV_VAR1", "ENV_VAR2"],
    "attachment_download": { ... }
  }
}
```

**Fields:**

| Field | Type | Required | Description |
|:------|:-----|:---------|:------------|
| `env` | array | Yes | Environment variable names for this mcptype |
| `attachment_download` | object | No | File download configuration |

**`attachment_download` fields:**

| Field | Type | Required | Default | Description |
|:------|:-----|:---------|:--------|:------------|
| `type` | string | Yes | - | `rest_api`, `mcp_tool_redirect`, or `mcp_tool` |
| `url_template` | string | For rest_api | - | URL with `{placeholder}` placeholders |
| `method` | string | No | `GET` | HTTP method: GET, POST, PUT, PATCH |
| `headers` | object | No | `{}` | Template values with `{token}`, `{email}`, `{arg_name}` |
| `body_template` | string | No | - | JSON body for POST/PUT with placeholders |
| `tool_name` | string | For mcp_tool types | - | MCP tool name |
| `tool_args_mapping` | object | For mcp_tool types | `{}` | Maps tool args to request args |
| `download_url_field` | string | No | - | Response field with download URL (mcp_tool_redirect, mcp_tool) |
| `max_base64_size` | number | No | `5242880` (5MB) | Max base64 content size for `mcp_tool` type |
| `tool_timeout` | number | No | `60` | MCP tool execution timeout in seconds |

**`attachment_upload` fields:**

| Field | Type | Required | Default | Description |
|:------|:-----|:---------|:--------|:------------|
| `type` | string | Yes | - | `rest_api` or `mcp_tool` |
| `url_template` | string | For rest_api | - | URL with `{placeholder}` placeholders |
| `method` | string | No | `POST` | HTTP method: POST |
| `headers` | object | No | `{}` | Template values with `{token}`, `{arg_name}` |
| `tool_name` | string | For mcp_tool | - | MCP tool name |
| `tools_allowed` | array | No | - | List of allowed MCP tools |
| `default_tool` | string | For mcp_tool | - | Default MCP tool to call |

**Download types:**
- `rest_api` - Direct HTTP request to platform API
- `mcp_tool_redirect` - MCP tool returns download URL, proxy streams it
- `mcp_tool` - MCP tool returns file content directly (base64), OR download URL if `download_url_field` configured

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

**Endpoint:** `GET /health`

Health check for load balancers and orchestration systems.

Response:
```json
{ "status": "ok" }
```

**Endpoint:** `GET /list`

Returns list of servers with their tools. Wraps `mcporter list` command with token injection from gloves.

**Query parameters:**
- `json=true` - Returns JSON output (parsed from mcporter output)
- `schema=true` - Includes full tool schemas

**Endpoint:** `GET /list/<mcptype>`

Returns information for a specific MCP type.

**Example:**
```bash
# Text output (passthrough from mcporter)
curl -H "X-MCP-Auth-Key: agent-key" http://localhost:8080/list

# JSON output
curl -H "X-MCP-Auth-Key: agent-key" "http://localhost:8080/list?json=true"

# Specific server with schema
curl -H "X-MCP-Auth-Key: agent-key" "http://localhost:8080/list/github?json=true&schema=true"
```

**Response (JSON mode):**
```json
{
  "mode": "list",
  "counts": {"ok": 2, "auth": 0, "offline": 0, "http": 0, "error": 0},
  "servers": [
    {
      "name": "github",
      "status": "ok",
      "durationMs": 50,
      "transport": "HTTP http://github:3000/mcp",
      "source": {"kind": "local"},
      "tools": [
        {"name": "list_repos", "description": "List repositories", "inputSchema": {...}},
        {"name": "github.attachment_download", "description": "...", "inputSchema": {...}},
        {"name": "github.attachment_upload", "description": "...", "inputSchema": {...}}
      ]
    }
  ]
}
```

**Note:** Attachment tools (`attachment_download`, `attachment_upload`) are automatically injected for mcptypes that have corresponding download/upload configurations in `mcp_env_map.json`.

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

**Endpoint:** `POST /download-attachment`

Downloads file attachments from platforms without exposing tokens to the agent.

Request body:
```json
{
  "mcptype": "github",
  "args": {
    "owner": "user",
    "repo": "repo",
    "asset_id": "123456",
    "filename": "release.zip"
  }
}
```

Response: `multipart/form-data` streaming the file content.

**Endpoint:** `POST /upload-attachment`

Uploads file attachments to platforms without exposing tokens to the agent.

Request headers:
- `X-MCP-Auth-Key`: `<agentId>-<agentKey>` (same as other endpoints)
- `X-Target-Platform`: platform name (`github`, `gitlab`, `confluence`, `jira`)
- `X-Target-Args`: JSON object with platform-specific arguments
- `Content-Type`: `multipart/form-data; boundary=<boundary>`
- `Content-Length`: size of multipart body

Request body: `multipart/form-data` with file part

**X-Target-Args by platform:**

| Platform | Required args | Example |
|:---------|:------------|:--------|
| github | `owner`, `repo`, `upload_url` | `{"owner":"octocat","repo":"hello-world","upload_url":"https://uploads.github.com/...","name":"release.zip"}` |
| gitlab | `project_id` | `{"project_id":"12345","name":"file.pdf"}` |
| confluence | `page_id` | `{"page_id":"123456","name":"document.pdf"}` |
| jira | `issue_key` | `{"issue_key":"PROJ-123","name":"attachment.zip"}` |

Response:
```json
{
  "success": true,
  "id": "att123456",
  "url": "https://conf.example.com/download/attachments/12345/report.pdf",
  "filename": "report.pdf"
}
```

**Authentication:** Both endpoints use `X-MCP-Auth-Key` header with format `<agentId>-<agentKey>` or `<agentId>/<agentKey>`.

### Client (Node.js)

Drop-in CLI replacement for `mcporter`. Requires `MCPORTER_PROXY_AUTH_KEY` environment variable.

**Environment variables:**

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MCPORTER_PROXY_URL` | `http://host.docker.internal:9022` | Proxy base URL |
| `MCPORTER_PROXY_TIMEOUT` | `120000` | Request timeout in milliseconds |
| `MCPORTER_PROXY_RETRIES` | `2` | Number of retry attempts |
| `MCPORTER_PROXY_RETRY_DELAY` | `1000` | Delay between retries in milliseconds |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |
| `MCPORTER_PROXY_AUTH_KEY` | **(required)** | Auth key: `<agentId>-<agentKey>` or `<agentId>/<agentKey>` |

**Usage:**
```bash
export MCPORTER_PROXY_AUTH_KEY=borets-abc123

# Execute MCP tool
mcporter-proxy call github.list_repos visibility=private

# Download attachment
mcporter-proxy download github owner=octocat repo=hello-world asset_id=123 --output release.zip

# Upload attachment
mcporter-proxy upload confluence page_id=123456 name=report.pdf --file ./report.pdf
mcporter-proxy upload jira issue_key=PROJ-123 name=attachment.zip --file ./attachment.zip

# Show help
mcporter-proxy help
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
  "github": {
    "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    "attachment_download": {
      "type": "rest_api",
      "method": "GET",
      "url_template": "https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}",
      "headers": { "Authorization": "Bearer {token}" }
    }
  },
  "gitlab": {
    "env": ["GITLAB_TOKEN"],
    "attachment_download": {
      "type": "rest_api",
      "method": "GET",
      "url_template": "https://gitlab.com/api/v4/projects/{project_id}/repository/files/{file_path}/raw",
      "headers": { "PRIVATE-TOKEN": "{token}" }
    }
  },
  "confluence": {
    "env": ["CONFLUENCE_API_TOKEN"],
    "attachment_download": {
      "type": "mcp_tool_redirect",
      "tool_name": "atlassian.confluence_download_attachment",
      "tool_args_mapping": { "page_id": "{page_id}", "filename": "{filename}" },
      "download_url_field": "download_url"
    }
  }
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
