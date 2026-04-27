# mcporter-proxy

HTTP proxy for MCPorter — enables MCP tools from sandboxed agents with secure token management.

## Overview

MCPorter is a CLI tool for calling MCP (Model Context Protocol) tools. The proxy allows sandboxed agents to execute MCPorter commands through a secure HTTP API with token management via **gloves**.

## Quick Start

### 1. Initialize gloves

```bash
# Install gloves CLI
curl -fsSL https://github.com/heyAyushh/gloves/releases/download/v0.5.11/gloves-0.5.11-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C /usr/local/bin
chmod +x /usr/local/bin/gloves

# Initialize gloves runtime
gloves bootstrap
```

### 2. Configure agent and secrets

```bash
# Create agent identity
gloves set-identity --agent my-agent

# Set secrets for each platform (format: prefix/agentId/platform/agentKey)
gloves --agent my-agent set "agents/my-agent/github/$(openssl rand -hex 32)" --value "ghp_xxxxxxxxxxxx"
gloves --agent my-agent set "agents/my-agent/confluence/$(openssl rand -hex 32)" --value "your-confluence-token"
```

### 3. Configure mcporter-proxy

Edit `server/mcp_env_map.json`:

```json
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "confluence": ["CONFLUENCE_API_TOKEN"]
}
```

### 4. Use the client

```bash
export MCPORTER_PROXY_AUTH_KEY=my-agent/your-agent-key
export MCPORTER_PROXY_URL=http://mcporter-proxy:8080

# Execute MCP tool
mcporter-proxy call github.list_repos visibility=private

# Download attachment
mcporter-proxy download github owner=octocat repo=hello-world asset_id=123 --output release.zip

# Upload attachment
mcporter-proxy upload confluence page_id=123456 name=report.pdf --file ./report.pdf

# List available tools
mcporter-proxy list
mcporter-proxy list --json
```

## Configuration

### mcp_env_map.json

Located next to `server.py`. Maps MCP platforms to environment variable names and attachment operations.

**Minimal config:**
```json
{
  "github": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "jira": ["JIRA_API_TOKEN", "JIRA_EMAIL"]
}
```

**Full config with attachments:**
```json
{
  "github": {
    "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    "attachment_upload": {
      "type": "rest_api",
      "method": "POST",
      "url_template": "{upload_url}",
      "headers": {
        "Authorization": "Bearer {token}"
      }
    }
  },
  "confluence": {
    "env": ["CONFLUENCE_API_TOKEN"],
    "attachment_upload": {
      "type": "rest_api",
      "method": "POST",
      "url_template": "https://your-domain.atlassian.net/wiki/rest/api/content/{page_id}/child/attachment",
      "headers": {
        "Authorization": "Bearer {token}",
        "X-Atlassian-Token": "no-check"
      }
    }
  }
}
```

### Configuration Reference

| Field | Type | Required | Description |
|:------|:-----|:---------|:------------|
| `env` | array | Yes | Environment variable names for this platform |
| `attachment_upload` | object | No | File upload configuration |

**`attachment_upload` fields:**

| Field | Type | Required | Default | Description |
|:------|:-----|:---------|:--------|:------------|
| `type` | string | Yes | - | `rest_api` or `mcp_tool` |
| `url_template` | string | For rest_api | - | URL with `{placeholder}` placeholders |
| `method` | string | No | `POST` | HTTP method |
| `headers` | object | No | `{}` | Headers with `{token}`, `{arg_name}` placeholders |

## Client Commands

```bash
# Execute MCP tool
mcporter-proxy call <platform>.<operation> [key=value ...]

# Download attachment
mcporter-proxy download <platform> [args] --output <path>

# Upload attachment
mcporter-proxy upload <platform> [args] --file <path>

# List available tools
mcporter-proxy list [platform] [--json|--schema]
```

### Upload by platform

| Platform | Required args | Example |
|:---------|:------------|:--------|
| github | `owner`, `repo`, `upload_url` | `owner=octocat repo=hello-world upload_url="https://..." name=file.zip --file ./file.zip` |
| gitlab | `project_id` | `project_id=12345 name=file.pdf --file ./file.pdf` |
| confluence | `page_id` | `page_id=123456 name=document.pdf --file ./document.pdf` |
| jira | `issue_key` | `issue_key=PROJ-123 name=attachment.zip --file ./attachment.zip` |

## Docker Compose

```yaml
services:
  mcporter-proxy:
    build: ./server
    ports:
      - "127.0.0.1:9022:8080"
    volumes:
      - ~/.mcporter:/root/.mcporter:ro
      - ~/.openclaw/secrets:/root/.openclaw/secrets:ro
    environment:
      - MCPORTER_PROXY_ALLOWED_TOOLS=github.*,gitlab.*,atlassian.*,cognee.*
      - MCPORTER_PROXY_LOG_LEVEL=INFO
```

## Environment Variables

### Server

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MCPORTER_PROXY_PORT` | `8080` | Listening port |
| `MCPORTER_PROXY_ALLOWED_TOOLS` | `*` | Comma-separated tool patterns |
| `MCPORTER_PROXY_TIMEOUT` | `120` | Execution timeout (seconds) |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |

### Client

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MCPORTER_PROXY_URL` | `http://host.docker.internal:9022` | Proxy base URL |
| `MCPORTER_PROXY_AUTH_KEY` | **(required)** | Auth key: `<agentId>/<agentKey>` |
| `MCPORTER_PROXY_TIMEOUT` | `120000` | Request timeout (milliseconds) |
| `MCPORTER_PROXY_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARN, ERROR |

## Running Tests

```bash
make test          # Run all tests
make test-server   # Run server unit tests
make test-client   # Run client unit tests
```
