"""Download functions for attachment retrieval."""

import base64
import http.client
import io
import json
import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, Generator, Optional, Tuple

try:
    from server.auth import resolve_extra_secrets, resolve_token, SECRETS_PREFIX, SECRETS_SEPARATOR
except ImportError:
    from auth import resolve_extra_secrets, resolve_token, SECRETS_PREFIX, SECRETS_SEPARATOR
try:
    from server.config import get_attachment_download_config, get_env_vars_for_mcptype
except ImportError:
    from config import get_attachment_download_config, get_env_vars_for_mcptype
try:
    from server.utils import mask_headers_for_log, mask_token, substitute_template
except ImportError:
    from utils import mask_headers_for_log, mask_token, substitute_template

logger = logging.getLogger("mcporter-proxy")

UPSTREAM_TIMEOUT = 300


class MCPToolError(Exception):
    pass


def base64_a85_decode_stream(data: str):
    yield base64.b64decode(data)


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
) -> Tuple[Generator[bytes, None, None], str, str]:
    method = config.get("method", "GET").upper()
    url_template = config.get("url_template", "")
    headers_template = config.get("headers", {})
    body_template = config.get("body_template")

    url = substitute_template(url_template, args)
    unsubstituted = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", url)
    if unsubstituted:
        logger.warning(
            "download_via_rest: unsubstituted placeholders in URL: %s. "
            "Provided args: %s. URL: %s",
            unsubstituted,
            list(args.keys()),
            url,
        )
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
) -> Tuple[Generator[bytes, None, None], str, str]:
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
        try:
            from server.auth import get_auth_key_separator, parse_auth_key
        except ImportError:
            from auth import get_auth_key_separator, parse_auth_key

        auth_parts = parse_auth_key(auth_key)
        if auth_parts:
            agent_id, agent_key = auth_parts
            mcptype_from_tool = get_mcptype(tool_name)
            sep = (
                SECRETS_SEPARATOR
                if SECRETS_SEPARATOR
                else get_auth_key_separator(auth_key)
            )
            env_vars = get_env_vars_for_mcptype(mcptype_from_tool)
            if isinstance(env_vars, str):
                env_vars = [env_vars]
            env_flags = []
            for i, env_var in enumerate(env_vars):
                if i == 0:
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype_from_tool}{sep}{agent_key}"
                else:
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype_from_tool}{sep}{agent_key}{sep}{i}"
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


def get_mcptype(tool: str) -> str:
    return tool.split(".")[0] if "." in tool else tool


def download_via_mcp_direct(
    config: Dict[str, Any],
    args: Dict[str, Any],
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Tuple[Generator[bytes, None, None], str, str]:
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


__all__ = [
    "MCPToolError",
    "download_via_rest",
    "download_via_mcp_redirect",
    "download_via_mcp_direct",
]
