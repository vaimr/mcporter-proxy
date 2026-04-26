# mcporter-proxy Skill

## Overview

`mcporter-proxy` is a **secure MCP proxy server** that extends standard MCP tool execution with:
- **Token isolation** - Secrets are never exposed to agents, retrieved from `gloves` on demand
- **Attachment downloads** - Stream file downloads through the proxy without token exposure
- **Centralized configuration** - All MCP integrations configured via `mcp_env_map.json`

## Architecture

```
Agent → mcporter-proxy → gloves (secrets) → mcporter (execution)
                ↓
         Token injection
                ↓
         Platform API
```

## Commands

### Tool Execution

```bash
mcporter-proxy call <tool> [args]
```

Executes an MCP tool with environment variables injected from `gloves` secrets.

**Example:**
```bash
mcporter-proxy call github.list_repos visibility=private
```

### Attachment Download

```bash
mcporter-proxy download <platform> [args] [--output <path>]
```

Downloads file attachments through the proxy, streaming directly to a file.

**Example:**
```bash
mcporter-proxy download github owner=octocat repo=hello-world asset_id=123 --output release.zip
```

### Health Check

```bash
curl http://localhost:8080/health
```

Returns `{"status": "ok"}` for load balancer health checks.

### Schema Endpoints

```bash
# List all available mcptypes
curl -H "X-MCP-Auth-Key: <key>" http://localhost:8080/schema

# Get schema for specific mcptype
curl -H "X-MCP-Auth-Key: <key>" http://localhost:8080/schema/github
```

Returns list of mcptypes or full schema with tools and attachment endpoints.

## Environment Variables

| Variable | Required | Description |
|:---------|:---------|:------------|
| `MCPORTER_PROXY_AUTH_KEY` | Yes | Auth key: `<agentId>-<agentKey>` or `<agentId>/<agentKey>` |
| `MCPORTER_PROXY_URL` | No | Base URL (default: `http://host.docker.internal:9022`) |
| `MCPORTER_PROXY_PORT` | No | Server port (default: `8080`) |
| `MCPORTER_PROXY_ALLOWED_TOOLS` | No | Comma-separated tool patterns (default: `*`) |
| `MCPORTER_PROXY_TIMEOUT` | No | Execution timeout in seconds (default: `120`) |

## Configuration

Configuration file: `server/mcp_env_map.json`

### Minimal Config
```json
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "jira": ["JIRA_API_TOKEN"]
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
        "Authorization": "Bearer {token}"
      }
    }
  }
}
```

### Configuration Fields

| Field | Description |
|:------|:------------|
| `env` | Environment variable names to inject from `gloves` |
| `attachment_download.type` | `rest_api`, `mcp_tool_redirect`, or `mcp_tool` |
| `attachment_download.url_template` | URL with `{placeholder}` substitution |
| `attachment_download.tool_name` | MCP tool name for tool-based downloads |
| `attachment_download.tool_args_mapping` | Maps request args to tool args |
| `attachment_download.max_base64_size` | Max base64 size for `mcp_tool` (default: 5MB) |
| `attachment_download.tool_timeout` | Tool execution timeout (default: 60s) |
| `attachment_download.download_url_field` | Response field with download URL |

### Download Types

| Type | Description |
|:-----|:------------|
| `rest_api` | Direct HTTP request to platform API |
| `mcp_tool_redirect` | MCP tool returns URL, proxy streams from URL |
| `mcp_tool` | MCP tool returns file content (base64), or URL if `download_url_field` set |

## MCP Tool Response Format

MCP tools should return JSON with these fields:

### For Tool Execution
```json
{
  "stdout": "...",
  "stderr": "...",
  "returncode": 0
}
```

### For File Content (mcp_tool type)
```json
{
  "file_content": "<base64-encoded-data>",
  "base64": true,
  "filename": "document.pdf",
  "content_type": "application/pdf"
}
```

### For Download URL (mcp_tool with download_url_field)
```json
{
  "download_url": "https://platform.com/file.pdf"
}
```

## Security Model

1. Agent sends request with `X-MCP-Auth-Key: <agentId>-<agentKey>`
2. Proxy parses auth key, retrieves secrets from `gloves` via env var injection
3. Tokens are injected into `gloves run --env VAR=gloves://... -- mcporter call ...`
4. Agent never sees actual tokens - only executes through proxy

## Use Cases

### 1. Execute Tool Without Token Exposure
```bash
mcporter-proxy call jira.create_issue project=MYPROJ summary="Bug report"
```

### 2. Download GitHub Release Asset
```bash
mcporter-proxy download github owner=octocat repo=hello-world asset_id=123456 --output v1.0.0.zip
```

### 3. Download from Confluence (via MCP tool)
```bash
mcporter-proxy download confluence page_id=123456 filename=doc.pdf --output document.pdf
```

### 4. List Allowed Tools
```bash
MCPORTER_PROXY_ALLOWED_TOOLS="github.*,jira.*" mcporter-proxy call github.list_repos
```

## Adding New MCP Types

1. Edit `server/mcp_env_map.json`:

```json
{
  "newplatform": {
    "env": ["NEWPLATFORM_API_TOKEN"],
    "attachment_download": {
      "type": "rest_api",
      "url_template": "https://api.newplatform.com/files/{file_id}",
      "headers": {
        "Authorization": "Bearer {token}"
      }
    }
  }
}
```

2. Create secrets in `gloves`:
```bash
gloves set NEWPLATFORM_API_TOKEN=<token>
```

3. Use it:
```bash
mcporter-proxy download newplatform file_id=abc123 --output report.pdf
```
