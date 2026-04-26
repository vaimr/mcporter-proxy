#!/usr/bin/env python3
"""
HTTP proxy for mcporter – securely executes mcporter commands from sandboxed agents.
Configuration via environment variables:
  - MCPORTER_PROXY_PORT: listening port (default 8080)
  - MCPORTER_PROXY_ALLOWED_TOOLS: comma-separated list of tool patterns (supports * wildcard)
  - MCPORTER_PROXY_TIMEOUT: execution timeout in seconds (default 120)
  - MCPORTER_PROXY_LOG_LEVEL: log level (DEBUG, INFO, WARNING, ERROR, CRITICAL; default INFO)
  - MCPROXY_SECRETS_PREFIX: prefix for secrets keys (default "agents")
  - MCPROXY_SECRETS_SEPARATOR: separator for secrets keys (default "")
"""

import base64
import http.client
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOG_LEVEL = os.environ.get("MCPORTER_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mcporter-proxy")


def mask_token(token: Optional[str]) -> str:
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def mask_headers_for_log(headers: Dict[str, str]) -> Dict[str, str]:
    masked = {}
    sensitive = {"authorization", "cookie", "x-auth-token", "x-api-key"}
    for key, value in headers.items():
        if key.lower() in sensitive:
            masked[key] = mask_token(value)
        else:
            masked[key] = value
    return masked


class MCPToolError(Exception):
    """Raised when MCP tool execution fails or returns invalid response."""


SCRIPT_DIR = Path(__file__).parent
ENV_MAP_PATH = SCRIPT_DIR / "mcp_env_map.json"

UPSTREAM_CONNECT_TIMEOUT = 10
UPSTREAM_READ_TIMEOUT = 300
UPSTREAM_TIMEOUT = max(UPSTREAM_CONNECT_TIMEOUT, UPSTREAM_READ_TIMEOUT)
MAX_UPLOAD_SIZE = 100 * 1024 * 1024
BOUNDARY_BYTES = b"simpleboundary"
MULTIPART_BOUNDARY = "simpleboundary"


def get_env_map_path() -> Path:
    return Path(os.environ.get("MCP_ENV_MAP_PATH", ENV_MAP_PATH))


def validate_config(config: Dict[str, Any], mcptype: str) -> List[str]:
    """Validate attachment_download and attachment_upload config for a mcptype."""
    errors = []
    if isinstance(config, list):
        return errors

    ad = config.get("attachment_download")
    if ad:
        download_type = ad.get("type")
        if download_type not in ("rest_api", "mcp_tool_redirect", "mcp_tool"):
            errors.append(f"Invalid download type '{download_type}' for {mcptype}")
        elif download_type == "rest_api":
            if not ad.get("url_template"):
                errors.append(f"Missing url_template for {mcptype} download")
        elif download_type in ("mcp_tool_redirect", "mcp_tool"):
            if not ad.get("tool_name"):
                errors.append(f"Missing tool_name for {mcptype} download")

    au = config.get("attachment_upload")
    if au:
        upload_type = au.get("type")
        if upload_type not in ("rest_api", "mcp_tool"):
            errors.append(f"Invalid upload type '{upload_type}' for {mcptype}")
        elif upload_type == "rest_api":
            if not au.get("url_template"):
                errors.append(f"Missing url_template for {mcptype} upload")
            if not au.get("headers"):
                errors.append(f"Missing headers for {mcptype} upload")
        elif upload_type == "mcp_tool":
            if not au.get("tool_name"):
                errors.append(f"Missing tool_name for {mcptype} upload")

    return errors


def load_env_map() -> Dict[str, Any]:
    try:
        env_map_path = get_env_map_path()
        with open(env_map_path, encoding="utf-8") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    all_errors = []
    for mcptype, cfg in config.items():
        errors = validate_config(cfg, mcptype)
        all_errors.extend(errors)

    if all_errors:
        for err in all_errors:
            logger.error("Config validation: %s", err)
        raise ValueError(f"Invalid config: {'; '.join(all_errors)}")

    return config


MCP_ENV_MAP = load_env_map()
logger.info("Loaded MCP env map: %s", MCP_ENV_MAP)


def parse_auth_key(auth_key: str) -> Optional[Tuple[str, str]]:
    if not auth_key:
        logger.warning("Auth key is empty")
        return None
    if "-" in auth_key:
        parts = auth_key.split("-", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    if "/" in auth_key:
        parts = auth_key.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    logger.warning(
        "Invalid auth key format: expected <agentId>-<agentKey> or <agentId>/<agentKey>, got: %s",
        auth_key,
    )
    return None


def get_mcptype(tool: str) -> str:
    return tool.split(".")[0] if "." in tool else tool


def get_auth_key_separator(auth_key: str) -> str:
    if "/" in auth_key:
        return "/"
    if "-" in auth_key:
        return "-"
    return "/"


DEFAULT_SECRETS_PREFIX = "agents"
SECRETS_PREFIX = os.environ.get("MCPROXY_SECRETS_PREFIX", DEFAULT_SECRETS_PREFIX)
SECRETS_SEPARATOR = os.environ.get("MCPROXY_SECRETS_SEPARATOR", "")


def get_env_vars_for_mcptype(mcptype: str) -> List[str]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return []
    if isinstance(config, list):
        return config
    if isinstance(config, dict) and "env" in config:
        return config["env"]
    return []


def get_attachment_download_config(mcptype: str) -> Optional[Dict[str, Any]]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return None
    if isinstance(config, dict) and "attachment_download" in config:
        return config["attachment_download"]
    return None


def run_mcporter_list_schema(
    mcptype: str,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cmd = ["mcporter", "list", mcptype, "--schema"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return [], f"mcporter returned {result.returncode}: {result.stderr}"
        return _parse_mcporter_schema_output(result.stdout), None
    except FileNotFoundError:
        return [], "mcporter not found"
    except subprocess.TimeoutExpired:
        return [], "mcporter timed out"
    except Exception as e:
        return [], str(e)


def _parse_mcporter_schema_output(output: str) -> List[Dict[str, Any]]:
    tools = []
    current_tool: Optional[Dict[str, Any]] = None
    json_buffer: List[str] = []
    in_json = False
    json_depth = 0
    pending_desc_lines: List[str] = []
    pending_desc_active = False

    for line in output.splitlines():
        line = line.rstrip()
        if not in_json:
            if pending_desc_active and line.strip() == "*/":
                pending_desc_active = False
                continue
            elif pending_desc_active:
                if line.startswith("   *"):
                    line = line[4:]
                    if line.startswith("*"):
                        line = line[1:]
                    line = line.strip()
                elif line.strip().startswith("*"):
                    line = line.strip()[1:].strip()
                else:
                    line = line.strip()
                if line:
                    pending_desc_lines.append(line)
                continue

        if line.startswith("  /**"):
            pending_desc_active = True
            pending_desc_lines = []
            continue
        elif "Examples:" in line or line.startswith("  ---"):
            pending_desc_lines = []
            pending_desc_active = False
            continue

        if re.match(r"^\s*function\s+", line):
            func_match = re.match(r"^\s*function\s+(\w+)\s*\(([^)]*)\)", line)
            if func_match:
                if pending_desc_lines:
                    description = "\n".join(pending_desc_lines).strip()
                else:
                    description = ""
                current_tool = {
                    "name": func_match.group(1),
                    "description": description,
                    "inputSchema": {},
                }
                pending_desc_lines = []
                pending_desc_active = False
            continue

        if current_tool is not None and not in_json:
            if "{" in line:
                in_json = True
                json_depth = line.count("{") - line.count("}")
                json_buffer = [line]
                continue

        if in_json:
            json_buffer.append(line)
            for char in line:
                if char == "{":
                    json_depth += 1
                elif char == "}":
                    json_depth -= 1
            if json_depth == 0:
                in_json = False
                json_str = "\n".join(json_buffer)
                json_str = json_str.rstrip(",").rstrip()
                try:
                    schema = json.loads(json_str)
                    current_tool["inputSchema"] = schema
                except json.JSONDecodeError:
                    pass
                json_buffer = []
                tools.append(current_tool)
                current_tool = None

    return tools


def build_attachment_schema(
    mcptype: str,
    config: Dict[str, Any],
    direction: str,
) -> Dict[str, Any]:
    endpoint = (
        "/download-attachment" if direction == "download" else "/upload-attachment"
    )
    method = "POST"
    if direction == "download":
        desc = "Download file attachment from platform"
        args_props = _get_download_args_properties(mcptype)
    else:
        desc = "Upload file attachment to platform"
        args_props = _get_upload_args_properties(mcptype)

    return {
        "name": f"{mcptype}.attachment_{direction}",
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": {
                "mcptype": {"const": mcptype},
                "args": {
                    "type": "object",
                    "properties": args_props,
                },
            },
        },
        "_proxy_endpoint": f"{method} {endpoint}",
        "_proxy_direction": direction,
    }


def _get_download_args_properties(mcptype: str) -> Dict[str, Any]:
    if mcptype == "github":
        return {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "asset_id": {"type": "string"},
            "filename": {"type": "string"},
        }
    elif mcptype == "gitlab":
        return {
            "project_id": {"type": "string"},
            "file_path": {"type": "string"},
            "ref": {"type": "string"},
        }
    elif mcptype in ("confluence", "atlassian"):
        return {
            "page_id": {"type": "string"},
            "filename": {"type": "string"},
        }
    elif mcptype == "jira":
        return {
            "issue_key": {"type": "string"},
            "attachment_id": {"type": "string"},
        }
    elif mcptype == "cognee":
        return {
            "dataset_name": {"type": "string"},
        }
    else:
        return {
            "id": {"type": "string"},
            "filename": {"type": "string"},
        }


def _get_upload_args_properties(mcptype: str) -> Dict[str, Any]:
    if mcptype == "github":
        return {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "upload_url": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "gitlab":
        return {
            "project_id": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype in ("confluence", "atlassian"):
        return {
            "page_id": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "jira":
        return {
            "issue_key": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "cognee":
        return {
            "data": {"type": "string"},
            "dataset_name": {"type": "string"},
        }
    else:
        return {
            "name": {"type": "string"},
            "file": {"type": "string"},
        }


def substitute_template(template: str, values: Dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        encoded = urllib.parse.quote(str(value), safe="-_.~")
        result = result.replace(f"{{{key}}}", encoded)
    return result


def get_secrets_keys(
    mcptype: str, agent_id: str, agent_key: str
) -> List[Tuple[int, str]]:
    env_vars = get_env_vars_for_mcptype(mcptype)
    if not env_vars:
        return [(0, f"{SECRETS_PREFIX}/{agent_id}/{mcptype}/{agent_key}")]
    sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else "/"
    keys = []
    for i, _ in enumerate(env_vars):
        if i == 0:
            key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
        else:
            key = (
                f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
            )
        keys.append((i, key))
    return keys


def resolve_token(mcptype: str, agent_id: str, agent_key: str) -> Optional[str]:
    keys = get_secrets_keys(mcptype, agent_id, agent_key)
    if not keys:
        return None
    secrets_key = keys[0][1]
    try:
        result = subprocess.run(
            ["gloves", "--agent", agent_id, "get", secrets_key],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("Failed to resolve token: %s", e)
    return None


def resolve_extra_secrets(
    mcptype: str, agent_id: str, agent_key: str
) -> Dict[str, str]:
    keys = get_secrets_keys(mcptype, agent_id, agent_key)
    secrets = {}
    for i, key in keys:
        if i == 0:
            continue
        try:
            result = subprocess.run(
                ["gloves", "--agent", agent_id, "get", key],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                env_vars = get_env_vars_for_mcptype(mcptype)
                if i < len(env_vars):
                    secrets[env_vars[i]] = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return secrets


def build_headers(
    headers_template: Dict[str, str],
    token: Optional[str],
    extra_secrets: Dict[str, str],
    args: Dict[str, Any],
) -> Dict[str, str]:
    headers = {}
    for key, value in headers_template.items():
        result = value
        if token and "{token}" in result:
            result = result.replace("{token}", token)
        if "{email}" in result:
            email = extra_secrets.get("email", "") or extra_secrets.get(
                "JIRA_EMAIL", ""
            )
            result = result.replace("{email}", email)
        if "{basic_auth}" in result:
            email = extra_secrets.get("email", "") or extra_secrets.get(
                "JIRA_EMAIL", ""
            )
            auth_string = f"{email}:{token}"
            result = result.replace(
                "{basic_auth}", base64.b64encode(auth_string.encode()).decode()
            )
        # Support ${ENV_VAR} syntax - read from extra_secrets (resolved via gloves)
        for match in re.finditer(r"\$\{([^}]+)\}", result):
            env_name = match.group(1)
            env_value = extra_secrets.get(env_name, os.environ.get(env_name, ""))
            result = result.replace(f"${{{env_name}}}", env_value)
        for arg_key, arg_val in args.items():
            result = result.replace(f"{{{arg_key}}}", str(arg_val))
        headers[key] = result
    return headers


def download_via_rest(
    config: Dict[str, Any],
    args: Dict[str, Any],
    token: Optional[str],
    extra_secrets: Dict[str, str],
):
    method = config.get("method", "GET").upper()
    url_template = config.get("url_template", "")
    headers_template = config.get("headers", {})
    body_template = config.get("body_template")

    url = substitute_template(url_template, args)
    headers = build_headers(headers_template, token, extra_secrets, args)

    body = None
    if body_template and method in ("POST", "PUT", "PATCH"):
        body = substitute_template(body_template, args).encode("utf-8")
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    logger.debug(
        "download_via_rest: method=%s, url=%s",
        method,
        url,
    )
    logger.debug("download_via_rest: headers=%s", mask_headers_for_log(headers))
    logger.debug("download_via_rest: body=%s", body)

    try:
        response = urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT)
    except urllib.error.HTTPError as e:
        logger.debug("download_via_rest: HTTPError %s - %s", e.code, e.reason)
        response = e

    logger.debug(
        "download_via_rest: response status=%s, headers=%s",
        response.status,
        mask_headers_for_log(dict(response.headers)),
    )

    content_type = response.headers.get("Content-Type", "application/octet-stream")
    content_disposition = response.headers.get("Content-Disposition", "")
    filename = args.get("filename", "")
    if not filename and content_disposition:
        if "filename=" in content_disposition:
            filename = content_disposition.split("filename=")[1].strip().strip('"')
    if not filename:
        filename = (
            urllib.parse.unquote(urllib.parse.urlparse(url).path.split("/")[-1])
            or "download"
        )

    def gen():
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            yield chunk
        response.close()

    return gen(), filename, content_type


def download_via_mcp_redirect(
    config: Dict[str, Any],
    args: Dict[str, Any],
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Tuple[io.BytesIO, str, str]:
    tool_name = config.get("tool_name", "")
    tool_args_mapping = config.get("tool_args_mapping", {})
    download_url_field = config.get("download_url_field", "download_url")
    headers_template = config.get("headers", {})

    mapped_args = {}
    for tool_arg, template in tool_args_mapping.items():
        value = substitute_template(template, args)
        mapped_args[tool_arg] = value

    cmd = ["mcporter", "call", tool_name]
    for key, value in mapped_args.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        cmd.append(f"{key}={value}")

    auth_key = extra_secrets.get("_auth_key", "")
    exec_cmd = cmd
    if auth_key:
        auth_parts = parse_auth_key(auth_key)
        if auth_parts:
            agent_id, agent_key = auth_parts
            mcptype = get_mcptype(tool_name)
            sep = (
                SECRETS_SEPARATOR
                if SECRETS_SEPARATOR
                else get_auth_key_separator(auth_key)
            )
            env_vars = get_env_vars_for_mcptype(mcptype)
            if isinstance(env_vars, str):
                env_vars = [env_vars]
            env_flags = []
            for i, env_var in enumerate(env_vars):
                if i == 0:
                    secrets_key = (
                        f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
                    )
                else:
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
                env_flags.extend(["--env", f"{env_var}=gloves://{secrets_key}"])
            exec_cmd = ["gloves", "--agent", agent_id, "run", *env_flags, "--", *cmd]

    result = subprocess.run(
        exec_cmd, capture_output=True, text=True, timeout=60, check=False
    )

    if result.returncode != 0:
        raise MCPToolError(f"MCP tool failed: {result.stderr}")

    tool_response = json.loads(result.stdout)
    download_url = tool_response.get(download_url_field) or tool_response.get(
        "stdout", ""
    )

    if not download_url or not isinstance(download_url, str):
        raise MCPToolError(f"No download_url in MCP tool response")

    parsed_url = urllib.parse.urlparse(download_url)
    headers = build_headers(headers_template, token, extra_secrets, args)

    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_TIMEOUT,
    )
    conn.connect()
    conn.putrequest("GET", parsed_url.path, skip_host=True)
    if parsed_url.query:
        conn.putheader("X-Original-URI", f"{parsed_url.path}?{parsed_url.query}")
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    response = conn.getresponse()

    content_type = response.getheader("Content-Type", "application/octet-stream")
    filename = args.get("filename", parsed_url.path.split("/")[-1] or "download")

    def gen():
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            yield chunk
        conn.close()

    return gen(), filename, content_type


def download_via_mcp_direct(
    config: Dict[str, Any],
    args: Dict[str, Any],
    token: Optional[str],
    extra_secrets: Dict[str, str],
):
    tool_name = config.get("tool_name", "")
    tool_args_mapping = config.get("tool_args_mapping", {})
    tool_timeout = config.get("tool_timeout", 60)
    download_url_field = config.get("download_url_field", "")
    redirect_headers = config.get("headers", {})

    mapped_args = {}
    for tool_arg, template in tool_args_mapping.items():
        value = substitute_template(template, args)
        mapped_args[tool_arg] = value

    cmd = ["mcporter", "call", tool_name]
    for key, value in mapped_args.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        cmd.append(f"{key}={value}")

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=tool_timeout, check=False
    )

    if result.returncode != 0:
        raise MCPToolError(f"MCP tool failed: {result.stderr}")

    tool_response = json.loads(result.stdout)

    if download_url_field:
        download_url = tool_response.get(download_url_field) or tool_response.get(
            "stdout", ""
        )
        if download_url and isinstance(download_url, str):
            filename = args.get("filename", "download")
            content_type = args.get("content_type", "application/octet-stream")

            parsed_url = urllib.parse.urlparse(download_url)
            headers = build_headers(redirect_headers, token, extra_secrets, args)

            conn = http.client.HTTPSConnection(
                parsed_url.netloc,
                timeout=UPSTREAM_TIMEOUT,
            )
            conn.connect()
            conn.putrequest("GET", parsed_url.path, skip_host=True)
            if parsed_url.query:
                conn.putheader(
                    "X-Original-URI", f"{parsed_url.path}?{parsed_url.query}"
                )
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.endheaders()
            response = conn.getresponse()

            def gen():
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    yield chunk
                conn.close()

            return gen(), filename, content_type

    file_content = (
        tool_response.get("file_content") or tool_response.get("content") or ""
    )

    if not file_content:
        raise MCPToolError("No file_content in MCP tool response")

    is_base64 = tool_response.get("base64", False)
    filename = args.get("filename", "download")
    content_type = args.get("content_type", "application/octet-stream")

    if is_base64:
        max_base64_size = config.get("max_base64_size", 5 * 1024 * 1024)
        if len(file_content) > max_base64_size:
            raise MCPToolError(
                f"Base64 content size {len(file_content)} exceeds limit {max_base64_size}"
            )
        gen = base64_a85_decode_stream(file_content)
    else:
        gen = (file_content.encode("utf-8") for _ in range(1))

    return gen, filename, content_type


def base64_a85_decode_stream(data: str):
    yield base64.b64decode(data)


def generate_multipart(
    stream, filename: str, content_type: str, boundary: str = "simpleboundary"
):
    yield f"--{boundary}\r\n".encode()
    yield f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    yield f"Content-Type: {content_type}\r\n\r\n".encode()
    if hasattr(stream, "read"):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            yield chunk
    else:
        yield from stream
    yield f"\r\n--{boundary}--\r\n".encode()


def get_attachment_upload_config(mcptype: str) -> Optional[Dict[str, Any]]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return None
    if isinstance(config, list):
        return None
    return config.get("attachment_upload")


def build_upload_headers(
    headers_template: Dict[str, str],
    token: Optional[str],
    extra_secrets: Dict[str, str],
    args: Dict[str, Any],
) -> Dict[str, str]:
    headers = {}
    for key, template in headers_template.items():
        value = substitute_template(
            template, {**args, "token": token or "", **extra_secrets}
        )
        if "{basic_auth}" in value:
            email = extra_secrets.get("email", "") or extra_secrets.get(
                "JIRA_EMAIL", ""
            )
            auth_string = f"{email}:{token}"
            value = value.replace(
                "{basic_auth}", base64.b64encode(auth_string.encode()).decode()
            )
        headers[key] = value
    return headers


def upload_via_rest(
    config: Dict[str, Any],
    args: Dict[str, Any],
    file_stream,
    filename: str,
    content_type: str,
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Dict[str, Any]:
    method = config.get("method", "POST").upper()
    url_template = config.get("url_template", "")
    headers_template = config.get("headers", {})

    url = substitute_template(
        url_template, {**args, "token": token or "", **extra_secrets}
    )
    headers = build_upload_headers(headers_template, token, extra_secrets, args)

    parsed_url = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_TIMEOUT,
    )
    conn.connect()
    conn.putrequest(method, parsed_url.path, skip_host=True)
    if parsed_url.query:
        conn.putheader("X-Original-URI", f"{parsed_url.path}?{parsed_url.query}")
    for k, v in headers.items():
        conn.putheader(k, v)

    boundary = "----McporterUploadBoundary"
    header = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    footer = f"\r\n--{boundary}--\r\n".encode()

    file_size = 0
    temp_buffer = []
    while True:
        chunk = file_stream.read(65536)
        if not chunk:
            break
        temp_buffer.append(chunk)
        file_size += len(chunk)

    body_size = len(header) + file_size + len(footer)
    conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Content-Length", str(body_size))
    conn.endheaders()

    conn.send(header)
    for chunk in temp_buffer:
        conn.send(chunk)
    conn.send(footer)

    response = conn.getresponse()
    response_body = response.read().decode("utf-8", errors="replace")
    conn.close()

    if response.status >= 400:
        raise MCPToolError(
            f"Upload failed: {response.status} {response.reason}: {response_body[:500]}"
        )

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError:
        result = {"raw_response": response_body}

    return result


class MCPorterProxyHandler(BaseHTTPRequestHandler):
    timeout = int(os.environ.get("MCPORTER_PROXY_TIMEOUT", "120"))
    _allowed_patterns: Optional[List[str]] = None
    _allowed_regexes: Optional[List[re.Pattern]] = None

    @classmethod
    def _load_allowed_patterns(cls) -> List[str]:
        if cls._allowed_patterns is not None:
            return cls._allowed_patterns
        patterns = os.environ.get("MCPORTER_PROXY_ALLOWED_TOOLS", "")
        if not patterns:
            logger.warning("MCPORTER_PROXY_ALLOWED_TOOLS not set - allowing all tools")
            cls._allowed_patterns = ["*"]
        else:
            cls._allowed_patterns = [
                p.strip() for p in patterns.split(",") if p.strip()
            ]
        regexes: List[re.Pattern] = [
            re.compile(re.escape(p).replace(r"\*", ".*"))
            for p in cls._allowed_patterns or []
        ]
        cls._allowed_regexes = regexes
        return cls._allowed_patterns

    def _is_tool_allowed(self, tool: str) -> bool:
        if self._allowed_regexes is None:
            self._load_allowed_patterns()
        regexes: List[re.Pattern] = self._allowed_regexes or []
        for regex in regexes:
            if regex.fullmatch(tool):
                return True
        return False

    def _send_json_response(self, status: int, data: Dict[str, Any]):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
        elif self.path == "/list" or self.path.startswith("/list?"):
            self._handle_list(query_string=self.path[len("/list") :])
        elif self.path.startswith("/list/"):
            path_parts = self.path[6:].split("?", 1)
            name = path_parts[0]
            query_string = path_parts[1] if len(path_parts) > 1 else ""
            self._handle_list(name=name, query_string=query_string)
        elif self.path == "/schema":
            self._handle_schema_list()
        elif self.path.startswith("/schema/"):
            mcptype = self.path[8:]
            self._handle_schema_mcptype(mcptype)
        else:
            self.send_error(404, "Endpoint not found")

    def _handle_schema_list(self):
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            self.send_error(401, "Authentication required")
            return
        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return

        mcptypes = list(MCP_ENV_MAP.keys())
        response = {"mcptypes": mcptypes}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def _handle_schema_mcptype(self, mcptype: str):
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            self.send_error(401, "Authentication required")
            return
        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return

        if mcptype not in MCP_ENV_MAP:
            self.send_error(404, f"Unknown mcptype: {mcptype}")
            return

        tools, error = run_mcporter_list_schema(mcptype)

        download_config = get_attachment_download_config(mcptype)
        upload_config = get_attachment_upload_config(mcptype)

        attachment_download = None
        if download_config:
            attachment_download = build_attachment_schema(
                mcptype, download_config, "download"
            )

        attachment_upload = None
        if upload_config:
            attachment_upload = build_attachment_schema(
                mcptype, upload_config, "upload"
            )

        response = {
            "name": mcptype,
            "tools": tools,
        }
        if error:
            response["error"] = error
        if attachment_download:
            response["attachment_download"] = attachment_download
        if attachment_upload:
            response["attachment_upload"] = attachment_upload

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def _handle_list(self, name: Optional[str] = None, query_string: str = ""):
        """Handle GET /list or /list/<name> with optional json and schema flags."""
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            self.send_error(401, "Authentication required")
            return
        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return

        agent_id, agent_key = auth_parts
        sep = (
            SECRETS_SEPARATOR if SECRETS_SEPARATOR else get_auth_key_separator(auth_key)
        )

        # Parse query params
        params: Dict[str, bool] = {}
        query_string = query_string.lstrip("?")
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    key = pair.split("=")[0]
                    params[key] = pair.split("=")[1] == "true" if "=" in pair else True
                elif pair:
                    params[pair] = True

        use_json = "json" in params
        use_schema = "schema" in params

        # Build mcporter command
        cmd = ["mcporter", "list"]
        if name:
            cmd.append(name)
        if use_json:
            cmd.append("--json")
        if use_schema:
            cmd.append("--schema")

        # Build env flags for gloves
        # When name is specified, only inject tokens for that mcptype
        # When name is None, inject tokens for ALL configured mcptypes
        env_flags = []
        if name:
            mcptypes_to_inject = [name]
        else:
            mcptypes_to_inject = list(MCP_ENV_MAP.keys())

        for mcptype in mcptypes_to_inject:
            env_vars = get_env_vars_for_mcptype(mcptype)
            if isinstance(env_vars, str):
                env_vars = [env_vars]
            for i, env_var in enumerate(env_vars):
                if i == 0:
                    secrets_key = (
                        f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
                    )
                else:
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
                env_flags.extend(["--env", f"{env_var}=gloves://{secrets_key}"])

        # Wrap command with gloves if we have env flags
        if env_flags:
            exec_cmd = ["gloves", "--agent", agent_id, "run", *env_flags, "--", *cmd]
        else:
            exec_cmd = cmd

        logger.debug("Executing mcporter list: %s", " ".join(exec_cmd))

        tmp_stdout = tempfile.NamedTemporaryFile(delete=False, mode="w+b")
        tmp_stderr = tempfile.NamedTemporaryFile(delete=False, mode="w+b")
        tmp_stdout_path = tmp_stdout.name
        tmp_stderr_path = tmp_stderr.name
        tmp_stdout.close()
        tmp_stderr.close()

        try:
            with (
                open(tmp_stdout_path, "wb") as stdout_f,
                open(tmp_stderr_path, "wb") as stderr_f,
            ):
                proc = subprocess.Popen(
                    exec_cmd,
                    stdout=stdout_f,
                    stderr=stderr_f,
                )
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self.send_error(504, "mcporter timed out")
                    return
        except FileNotFoundError as e:
            if e.filename == "gloves":
                self.send_error(500, "gloves not found")
            else:
                self.send_error(500, "mcporter not found")
            return

        with open(tmp_stdout_path, "rb") as f:
            stdout_bytes = f.read()
        with open(tmp_stderr_path, "rb") as f:
            stderr_bytes = f.read()
        import os as os_module

        os_module.unlink(tmp_stdout_path)
        os_module.unlink(tmp_stderr_path)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        returncode = proc.returncode

        if returncode != 0:
            logger.warning("mcporter list failed: %s", stderr)

        if use_json:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                self.send_error(500, f"Invalid JSON from mcporter: {stderr[:200]}")
                return

            # Inject attachment tools into each server's tools array
            self._inject_attachment_tools(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            # Plain text passthrough
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(stdout.encode("utf-8"))

    def _inject_attachment_tools(self, data: Any) -> None:
        inject_attachment_tools(data)

    def _add_attachment_tools_to_server(self, server: Dict[str, Any]) -> None:
        add_attachment_tools_to_server(server)


def inject_attachment_tools(data: Any) -> None:
    if not isinstance(data, dict):
        return

    if data.get("mode") == "server":
        add_attachment_tools_to_server(data)
        return

    servers = data.get("servers", [])
    if isinstance(servers, list):
        for server in servers:
            add_attachment_tools_to_server(server)


def add_attachment_tools_to_server(server: Dict[str, Any]) -> None:
    name = server.get("name")
    if not name:
        return

    download_config = get_attachment_download_config(name)
    upload_config = get_attachment_upload_config(name)

    tools = server.get("tools", [])

    if download_config:
        download_tool = build_attachment_schema(name, download_config, "download")
        tools.append(download_tool)

    if upload_config:
        upload_tool = build_attachment_schema(name, upload_config, "upload")
        tools.append(upload_tool)

    server["tools"] = tools

    def do_POST(self):
        if self.path == "/call":
            self._handle_call()
        elif self.path == "/download-attachment":
            self._handle_download_attachment()
        elif self.path == "/upload-attachment":
            self._handle_upload_attachment()
        else:
            self.send_error(404, "Endpoint not found")

    def _handle_call(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            logger.warning("Request received with empty body")
            self.send_error(400, "Empty body")
            return

        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON received: %s", e)
            self.send_error(400, f"Invalid JSON: {e}")
            return

        tool = data.get("tool")
        if not tool:
            logger.warning("Request missing 'tool' field")
            self.send_error(400, "Missing 'tool' field")
            return

        auth_key = self.headers.get("X-MCP-Auth-Key")
        logger.info(
            "Incoming request: tool=%s auth_key=%s",
            tool,
            "present" if auth_key else "absent",
        )

        if not self._is_tool_allowed(tool):
            logger.warning("Tool blocked: %s", tool)
            self.send_error(403, f"Tool '{tool}' is not allowed")
            return

        args = data.get("args", {})
        if not isinstance(args, dict):
            logger.warning("Invalid 'args' type: %s", type(args).__name__)
            self.send_error(400, "'args' must be a dictionary")
            return

        logger.debug("Tool args: %s", args)

        cmd = ["mcporter", "call", tool]
        for key, value in args.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            cmd.append(f"{key}={value}")

        logger.debug("Executing: %s", " ".join(cmd))

        exec_cmd = cmd
        if auth_key:
            auth_parts = parse_auth_key(auth_key)
            if auth_parts:
                agent_id, agent_key = auth_parts
                mcptype = get_mcptype(tool)
                sep = (
                    SECRETS_SEPARATOR
                    if SECRETS_SEPARATOR
                    else get_auth_key_separator(auth_key)
                )
                env_vars = get_env_vars_for_mcptype(mcptype)
                if isinstance(env_vars, str):
                    env_vars = [env_vars]
                env_flags = []
                for i, env_var in enumerate(env_vars):
                    if i == 0:
                        secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
                    else:
                        secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
                    env_flags.extend(["--env", f"{env_var}=gloves://{secrets_key}"])
                    logger.debug("gloves env: %s=gloves://%s", env_var, secrets_key)
                exec_cmd = [
                    "gloves",
                    "--agent",
                    agent_id,
                    "run",
                    *env_flags,
                    "--",
                    *cmd,
                ]
            else:
                logger.warning("Invalid auth key format: %s", auth_key)

        try:
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("Command timed out after %ss: %s", self.timeout, tool)
            self.send_error(504, f"Command timed out after {self.timeout}s")
            return
        except FileNotFoundError as e:
            if e.filename == "gloves":
                logger.error("gloves executable not found - is it installed?")
            else:
                logger.error("mcporter executable not found")
            self.send_error(500, "Required executable not found")
            return

        logger.info("Command completed: %s (exit=%s)", tool, result.returncode)

        if result.stderr:
            logger.debug("stderr: %s", result.stderr[:500])

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def _handle_download_attachment(self):
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            logger.warning("Download request without auth key")
            self.send_error(401, "Authentication required")
            return

        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return

        agent_id, agent_key = auth_parts

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty body")
            return

        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self.send_error(400, f"Invalid JSON: {e}")
            return

        mcptype = data.get("mcptype")
        if not mcptype:
            self.send_error(400, "Missing 'mcptype' field")
            return

        args = data.get("args", {})
        if not isinstance(args, dict):
            self.send_error(400, "'args' must be a dictionary")
            return

        config = get_attachment_download_config(mcptype)
        if not config:
            self.send_error(400, f"No attachment download configured for {mcptype}")
            return

        download_type = config.get("type")
        if not download_type:
            self.send_error(400, "Missing 'type' in attachment_download config")
            return

        token = resolve_token(mcptype, agent_id, agent_key)
        extra_secrets = resolve_extra_secrets(mcptype, agent_id, agent_key)
        extra_secrets["_auth_key"] = auth_key
        # Add primary token to extra_secrets so ${ENV_VAR} can reference it
        env_vars = get_env_vars_for_mcptype(mcptype)
        if isinstance(env_vars, list) and len(env_vars) > 0 and token:
            extra_secrets[env_vars[0]] = token

        logger.debug(
            "_handle_download_attachment: mcptype=%s, agent_id=%s, download_type=%s, "
            "token=%s, extra_secrets_keys=%s, env_vars=%s",
            mcptype,
            agent_id,
            download_type,
            mask_token(token),
            list(extra_secrets.keys()),
            env_vars,
        )

        try:
            if download_type == "rest_api":
                stream, filename, content_type = download_via_rest(
                    config, args, token, extra_secrets
                )
            elif download_type == "mcp_tool_redirect":
                stream, filename, content_type = download_via_mcp_redirect(
                    config, args, token, extra_secrets
                )
            elif download_type == "mcp_tool":
                stream, filename, content_type = download_via_mcp_direct(
                    config, args, token, extra_secrets
                )
            else:
                self.send_error(
                    400, f"Unknown attachment download type: {download_type}"
                )
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{urllib.parse.quote(filename, safe='')}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            for chunk in stream:
                self.wfile.write(chunk)

        except (ConnectionResetError, BrokenPipeError, OSError, MCPToolError) as e:
            logger.error("Download failed: %s", e)
            self.send_error(502, f"Download failed: {str(e)}")

    def _handle_upload_attachment(self):
        auth_valid, auth_key, agent_id, agent_key = self._validate_upload_auth()
        if not auth_valid:
            return

        boundary = self._parse_upload_boundary()
        if not boundary:
            return

        mcptype, args = self._parse_upload_target_args()
        if not mcptype:
            return

        config = get_attachment_upload_config(mcptype)
        if not config:
            self.send_error(400, f"No attachment upload configured for {mcptype}")
            return

        upload_type = config.get("type")
        if not upload_type:
            self.send_error(400, "Missing 'type' in attachment_upload config")
            return

        file_stream = self._extract_upload_file(boundary)
        if not file_stream:
            return

        try:
            result = self._execute_upload(
                upload_type, config, args, file_stream, agent_id, agent_key
            )
            self._send_upload_response(
                result, args.get("name") or args.get("filename", "upload")
            )
        except (ConnectionResetError, BrokenPipeError, OSError, MCPToolError) as e:
            logger.error("Upload failed: %s", e)
            self.send_error(502, f"Upload failed: {str(e)}")

    def _validate_upload_auth(self):
        """Validate auth headers for upload. Returns (valid, auth_key, agent_id, agent_key)."""
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            logger.warning("Upload request without auth key")
            self.send_error(401, "Authentication required")
            return False, None, None, None

        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return False, None, None, None

        return True, auth_key, auth_parts[0], auth_parts[1]

    def _parse_upload_boundary(self):
        """Parse Content-Type and extract boundary for upload."""
        content_type_header = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type_header:
            self.send_error(400, "Content-Type must be multipart/form-data")
            return None

        boundary = self._parse_boundary(content_type_header)
        if not boundary:
            self.send_error(400, "Missing boundary in Content-Type")
            return None

        return boundary

    def _parse_upload_target_args(self):
        """Parse X-Target-Platform and X-Target-Args headers. Returns (mcptype, args)."""
        mcptype = self.headers.get("X-Target-Platform")
        if not mcptype:
            self.send_error(400, "Missing X-Target-Platform header")
            return None, None

        target_args_raw = self.headers.get("X-Target-Args", "{}")
        try:
            args = json.loads(target_args_raw)
        except json.JSONDecodeError as e:
            self.send_error(400, f"Invalid JSON in X-Target-Args: {e}")
            return None, None

        if not isinstance(args, dict):
            self.send_error(400, "X-Target-Args must be a JSON object")
            return None, None

        return mcptype, args

    def _extract_upload_file(self, boundary):
        """Extract file from multipart body. Returns file stream or None."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty body")
            return None

        if content_length > MAX_UPLOAD_SIZE:
            self.send_error(
                413, f"Upload size {content_length} exceeds limit {MAX_UPLOAD_SIZE}"
            )
            return None

        body = self.rfile.read(content_length)
        file_stream = self._extract_file_from_multipart(body, boundary)
        if not file_stream:
            self.send_error(400, "No file part in multipart request")
            return None

        return file_stream

    def _execute_upload(
        self, upload_type, config, args, file_stream, agent_id, agent_key
    ):
        """Execute upload based on type. Returns upload result."""
        token = resolve_token(args.get("mcptype", ""), agent_id, agent_key)
        extra_secrets = resolve_extra_secrets(
            args.get("mcptype", ""), agent_id, agent_key
        )

        if upload_type == "rest_api":
            return upload_via_rest(
                config,
                args,
                file_stream,
                args.get("name") or args.get("filename", "upload"),
                args.get("content_type") or "application/octet-stream",
                token,
                extra_secrets,
            )

        raise MCPToolError(f"Unknown attachment upload type: {upload_type}")

    def _send_upload_response(self, result, filename):
        """Send successful upload response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "success": True,
                    "id": result.get("id", result.get("attach", {}).get("id", "")),
                    "url": result.get("url", result.get("download_url", "")),
                    "filename": filename,
                }
            ).encode("utf-8")
        )

    def _parse_boundary(self, content_type: str) -> Optional[bytes]:
        if "boundary=" in content_type:
            boundary_start = content_type.find("boundary=") + len("boundary=")
            boundary_end = content_type.find(";", boundary_start)
            if boundary_end == -1:
                boundary_end = len(content_type)
            boundary_value = (
                content_type[boundary_start:boundary_end].strip().strip('"')
            )
            return boundary_value.encode()
        return None

    def _extract_file_from_multipart(
        self, body: bytes, boundary: bytes
    ) -> Optional[io.BytesIO]:
        from email.parser import BytesParser

        parser = BytesParser()
        msg = parser.parsebytes(
            b"Content-Type: multipart/form-data; boundary="
            + boundary
            + b"\r\n\r\n"
            + body
        )

        for part in msg.walk():
            if part.get_content_disposition() == "form-data" and part.get_filename():
                payload = part.get_payload(decode=True)
                if payload:
                    return io.BytesIO(payload)
        return None

    def log_message(self, format, *args):
        pass


def main():
    import signal

    port = int(os.environ.get("MCPORTER_PROXY_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), MCPorterProxyHandler)

    def shutdown_handler(_signum, _frame):
        logger.info("Received shutdown signal, closing server...")
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    print(f"mcporter-proxy listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
