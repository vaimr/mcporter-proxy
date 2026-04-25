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

import http.client
import json
import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

LOG_LEVEL = os.environ.get("MCPORTER_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mcporter-proxy")

SCRIPT_DIR = Path(__file__).parent
ENV_MAP_PATH = SCRIPT_DIR / "mcp_env_map.json"

UPSTREAM_CONNECT_TIMEOUT = 10
UPSTREAM_READ_TIMEOUT = 300


def validate_config(config: Dict[str, Any], mcptype: str) -> List[str]:
    errors = []
    if isinstance(config, list):
        return errors
    ad = config.get("attachment_download")
    if not ad:
        return errors

    download_type = ad.get("type")
    if download_type not in ("rest_api", "mcp_tool_redirect", "mcp_tool"):
        errors.append(f"Invalid type '{download_type}' for {mcptype}")
        return errors

    if download_type == "rest_api":
        if not ad.get("url_template"):
            errors.append(f"Missing url_template for {mcptype} rest_api")
    elif download_type in ("mcp_tool_redirect", "mcp_tool"):
        if not ad.get("tool_name"):
            errors.append(f"Missing tool_name for {mcptype} {download_type}")
    return errors


def load_env_map() -> Dict[str, Any]:
    try:
        with open(ENV_MAP_PATH) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    all_errors = []
    for mcptype, cfg in config.items():
        errors = validate_config(cfg, mcptype)
        all_errors.extend(errors)

    if all_errors:
        for err in all_errors:
            logger.error(f"Config validation: {err}")
        raise ValueError(f"Invalid config: {'; '.join(all_errors)}")

    return config


MCP_ENV_MAP = load_env_map()
logger.info(f"Loaded MCP env map: {MCP_ENV_MAP}")


def parse_auth_key(auth_key: str) -> Optional[Tuple[str, str]]:
    if not auth_key:
        logger.warning("Auth key is empty")
        return None
    if "-" in auth_key:
        parts = auth_key.split("-", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
    if "/" in auth_key:
        parts = auth_key.split("/", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
    logger.warning(
        f"Invalid auth key format: expected <agentId>-<agentKey> or <agentId>/<agentKey>, got: {auth_key}"
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


def substitute_template(template: str, values: Dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", str(value))
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
    sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else "/"
    secrets_key = keys[0][1]
    try:
        result = subprocess.run(
            ["gloves", "--agent", agent_id, "get", secrets_key],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to resolve token: {e}")
    return None


def resolve_extra_secrets(
    mcptype: str, agent_id: str, agent_key: str
) -> Dict[str, str]:
    keys = get_secrets_keys(mcptype, agent_id, agent_key)
    sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else "/"
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
            )
            if result.returncode == 0:
                env_vars = get_env_vars_for_mcptype(mcptype)
                if i < len(env_vars):
                    secrets[env_vars[i]] = result.stdout.strip()
        except Exception:
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
            email = extra_secrets.get("email", "")
            result = result.replace("{email}", email)
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

    parsed_url = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_CONNECT_TIMEOUT,
    )
    conn.connect()
    conn.putrequest(method, parsed_url.path, skip_host=True)
    if parsed_url.query:
        conn.putheader("X-Original-URI", f"{parsed_url.path}?{parsed_url.query}")
    for k, v in headers.items():
        conn.putheader(k, v)
    if body:
        conn.endheaders(body)
    else:
        conn.endheaders()
    response = conn.getresponse()

    content_type = response.getheader("Content-Type", "application/octet-stream")
    content_disposition = response.getheader("Content-Disposition", "")
    filename = args.get("filename", "")
    if not filename and content_disposition:
        if "filename=" in content_disposition:
            filename = content_disposition.split("filename=")[1].strip().strip('"')
    if not filename:
        filename = parsed_url.path.split("/")[-1] or "download"

    def gen():
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            yield chunk
        conn.close()

    return gen(), filename, content_type


def download_via_mcp_redirect(
    config: Dict[str, Any],
    args: Dict[str, Any],
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Tuple[io.BytesIO, str, str]:
    import io

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

    result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        raise Exception(f"MCP tool failed: {result.stderr}")

    tool_response = json.loads(result.stdout)
    download_url = tool_response.get(download_url_field) or tool_response.get(
        "stdout", ""
    )

    if not download_url or not isinstance(download_url, str):
        raise Exception(f"No download_url in MCP tool response")

    parsed_url = urllib.parse.urlparse(download_url)
    headers = build_headers(headers_template, token, extra_secrets, args)

    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_CONNECT_TIMEOUT,
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

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=tool_timeout)

    if result.returncode != 0:
        raise Exception(f"MCP tool failed: {result.stderr}")

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
                timeout=UPSTREAM_CONNECT_TIMEOUT,
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
        raise Exception("No file_content in MCP tool response")

    is_base64 = tool_response.get("base64", False)
    filename = args.get("filename", "download")
    content_type = args.get("content_type", "application/octet-stream")

    if is_base64:
        import base64

        max_base64_size = config.get("max_base64_size", 5 * 1024 * 1024)
        if len(file_content) > max_base64_size:
            raise Exception(
                f"Base64 content size {len(file_content)} exceeds limit {max_base64_size}"
            )
        gen = base64_a85_decode_stream(file_content)
    else:
        gen = (file_content.encode("utf-8") for _ in range(1))

    return gen, filename, content_type


def base64_a85_decode_stream(data: str):
    import base64

    try:
        yield base64.b64decode(data)
    except Exception:
        yield data.encode("utf-8")


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
        for chunk in stream:
            yield chunk
    yield f"\r\n--{boundary}--\r\n".encode()


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
        cls._allowed_regexes = [
            re.compile(re.escape(p).replace(r"\*", ".*")) for p in cls._allowed_patterns
        ]
        return cls._allowed_patterns

    def _is_tool_allowed(self, tool: str) -> bool:
        if self._allowed_regexes is None:
            self._load_allowed_patterns()
        for regex in self._allowed_regexes:
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
        else:
            self.send_error(404, "Endpoint not found")

    def do_POST(self):
        if self.path == "/call":
            self._handle_call()
        elif self.path == "/download-attachment":
            self._handle_download_attachment()
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
            logger.warning(f"Invalid JSON received: {e}")
            self.send_error(400, f"Invalid JSON: {e}")
            return

        tool = data.get("tool")
        if not tool:
            logger.warning("Request missing 'tool' field")
            self.send_error(400, "Missing 'tool' field")
            return

        auth_key = self.headers.get("X-MCP-Auth-Key")
        logger.info(
            f"Incoming request: tool={tool} auth_key={'present' if auth_key else 'absent'}"
        )

        if not self._is_tool_allowed(tool):
            logger.warning(f"Tool blocked: {tool}")
            self.send_error(403, f"Tool '{tool}' is not allowed")
            return

        args = data.get("args", {})
        if not isinstance(args, dict):
            logger.warning(f"Invalid 'args' type: {type(args).__name__}")
            self.send_error(400, "'args' must be a dictionary")
            return

        logger.debug(f"Tool args: {args}")

        cmd = ["mcporter", "call", tool]
        for key, value in args.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            cmd.append(f"{key}={value}")

        logger.debug(f"Executing: {' '.join(cmd)}")

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
                    logger.debug(f"gloves env: {env_var}=gloves://{secrets_key}")
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
                logger.warning(f"Invalid auth key format: {auth_key}")

        try:
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {self.timeout}s: {tool}")
            self.send_error(504, f"Command timed out after {self.timeout}s")
            return
        except FileNotFoundError as e:
            if e.filename == "gloves":
                logger.error("gloves executable not found - is it installed?")
            else:
                logger.error("mcporter executable not found")
            self.send_error(500, "Required executable not found")
            return

        logger.info(f"Command completed: {tool} (exit={result.returncode})")

        if result.stderr:
            logger.debug(f"stderr: {result.stderr[:500]}")

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
            self.send_header(
                "Content-Type", f"multipart/form-data; boundary=simpleboundary"
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            for chunk in generate_multipart(stream, filename, content_type):
                self.wfile.write(chunk)

        except Exception as e:
            logger.error(f"Download failed: {e}")
            self.send_error(502, f"Download failed: {str(e)}")

    def log_message(self, format, *args):
        pass


def main():
    import signal

    port = int(os.environ.get("MCPORTER_PROXY_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), MCPorterProxyHandler)

    def shutdown_handler(signum, frame):
        logger.info("Received shutdown signal, closing server...")
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    print(f"mcporter-proxy listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
