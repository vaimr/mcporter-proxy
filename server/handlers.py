"""HTTP request handlers for mcporter-proxy."""

import base64
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional, Tuple

try:
    from server.auth import SECRETS_PREFIX, SECRETS_SEPARATOR, get_auth_key_separator, get_available_secrets, get_env_vars_for_mcptype, parse_auth_key, resolve_extra_secrets, resolve_token
except ImportError:
    from auth import SECRETS_PREFIX, SECRETS_SEPARATOR, get_auth_key_separator, get_available_secrets, get_env_vars_for_mcptype, parse_auth_key, resolve_extra_secrets, resolve_token
try:
    from server.config import MCP_ENV_MAP, get_attachment_download_config, get_attachment_upload_config
except ImportError:
    from config import MCP_ENV_MAP, get_attachment_download_config, get_attachment_upload_config
try:
    from server.download import MCPToolError, download_via_mcp_direct, download_via_mcp_redirect, download_via_rest
except ImportError:
    from download import MCPToolError, download_via_mcp_direct, download_via_mcp_redirect, download_via_rest
try:
    from server.schema import run_mcporter_list_schema
except ImportError:
    from schema import run_mcporter_list_schema
try:
    from server.tools import build_attachment_schema, build_attachment_text_schema_for_mcptype, inject_attachment_tools
except ImportError:
    from tools import build_attachment_schema, build_attachment_text_schema_for_mcptype, inject_attachment_tools
try:
    from server.upload import upload_via_rest
except ImportError:
    from upload import upload_via_rest
try:
    from server.utils import mask_token
except ImportError:
    from utils import mask_token

logger = logging.getLogger("mcporter-proxy")

MAX_UPLOAD_SIZE = 100 * 1024 * 1024


class ChunkedFileReference:
    def __init__(self, chunks: List[Tuple[bytes, int]], total_size: int):
        self.chunks = chunks
        self.total_size = total_size
        self.position = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = b"".join(data for data, _ in self.chunks)
            self.position = self.total_size
            self.chunks = []
            return result
        result = b""
        remaining = size
        for i, (data, chunk_size) in enumerate(self.chunks):
            if remaining <= 0:
                break
            to_read = min(remaining, chunk_size)
            result += data[:to_read]
            remaining -= to_read
            if to_read < chunk_size:
                self.chunks[i] = (data[to_read:], chunk_size - to_read)
        self.position += len(result)
        return result

    def read_chunk(self):
        for data, _ in self.chunks:
            yield data


class MultipartParser:
    def __init__(self, boundary: bytes, callbacks: Dict[str, Any]):
        self.boundary = boundary
        self.callbacks = callbacks
        self.buffer = b""
        self.state = "start"

    def write(self, data: bytes):
        self.buffer += data
        self._parse()

    def finalize(self):
        if self.buffer:
            self._parse()

    def _parse(self):
        while self.buffer:
            if self.state == "start":
                if self.boundary in self.buffer:
                    idx = self.buffer.find(self.boundary)
                    self.buffer = self.buffer[idx + len(self.boundary):]
                    if self.buffer.startswith(b"--"):
                        return
                    if self.buffer.startswith(b"\r\n"):
                        self.buffer = self.buffer[2:]
                    elif self.buffer.startswith(b"\n"):
                        self.buffer = self.buffer[1:]
                    self.state = "headers"
                else:
                    break
            elif self.state == "headers":
                if b"\r\n\r\n" in self.buffer:
                    idx = self.buffer.find(b"\r\n\r\n")
                    header_data = self.buffer[:idx]
                    self.buffer = self.buffer[idx + 4:]
                    self._parse_headers(header_data)
                    if self.buffer.startswith(b"\r\n"):
                        self.buffer = self.buffer[2:]
                    elif self.buffer.startswith(b"\n"):
                        self.buffer = self.buffer[1:]
                    self.state = "data"
                else:
                    break
            elif self.state == "data":
                boundary_pos = self.buffer.find(self.boundary)
                if boundary_pos >= 0:
                    part_data = self.buffer[:boundary_pos]
                    if part_data.endswith(b"--"):
                        part_data = part_data[:-2]
                    while part_data.endswith(b"\r\n"):
                        part_data = part_data[:-2]
                    while part_data.endswith(b"\n"):
                        part_data = part_data[:-1]
                    self.callbacks["on_part_data"](part_data, 0, len(part_data))
                    self.callbacks["on_part_end"]()
                    self.buffer = self.buffer[boundary_pos + len(self.boundary):]
                    if self.buffer.startswith(b"--"):
                        self.callbacks["on_end"]()
                        return
                    if self.buffer.startswith(b"\r\n"):
                        self.buffer = self.buffer[2:]
                    elif self.buffer.startswith(b"\n"):
                        self.buffer = self.buffer[1:]
                    self.state = "headers"
                else:
                    break

    def _parse_headers(self, header_data: bytes):
        lines = header_data.split(b"\r\n")
        for line in lines:
            if b":" in line:
                idx = line.find(b":")
                field = line[:idx]
                value = line[idx+1:]
                self.callbacks["on_header_field"](field, 0, len(field))
                self.callbacks["on_header_value"](value, 0, len(value))
        self.callbacks["on_header_end"]()
        self.callbacks["on_headers_finished"]()


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
            self._handle_list(query_string=self.path[len("/list"):])
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
            attachment_download = build_attachment_schema(mcptype, download_config, "download")
        attachment_upload = None
        if upload_config:
            attachment_upload = build_attachment_schema(mcptype, upload_config, "upload")
        response = {"name": mcptype, "tools": tools}
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
        auth_key = self.headers.get("X-MCP-Auth-Key")
        if not auth_key:
            self.send_error(401, "Authentication required")
            return
        auth_parts = parse_auth_key(auth_key)
        if not auth_parts:
            self.send_error(401, "Invalid auth key format")
            return
        agent_id, agent_key = auth_parts
        sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else get_auth_key_separator(auth_key)
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
        use_all_params = "all_parameters" in params
        if use_json and use_schema:
            self.send_error(400, "--json and --schema cannot be used together")
            return
        cmd = ["mcporter", "list"]
        if name:
            cmd.append(name)
        if use_json:
            cmd.append("--json")
        elif use_schema:
            cmd.append("--schema")
        if use_all_params:
            cmd.append("--all-parameters")
        available_secrets = get_available_secrets(agent_id) if not name else set()
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
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
                else:
                    secrets_key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
                if not name and secrets_key not in available_secrets:
                    continue
                env_flags.extend(["--env", f"{env_var}=gloves://{secrets_key}"])
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
            with open(tmp_stdout_path, "wb") as stdout_f, open(tmp_stderr_path, "wb") as stderr_f:
                proc = subprocess.Popen(exec_cmd, stdout=stdout_f, stderr=stderr_f)
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
        os.unlink(tmp_stdout_path)
        os.unlink(tmp_stderr_path)
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
            inject_attachment_tools(data, include_schema=use_schema)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            output = stdout
            if use_schema:
                meta_parts = []
                mcptypes_to_append = [name] if name else list(MCP_ENV_MAP.keys())
                for mt in mcptypes_to_append:
                    meta_schema = build_attachment_text_schema_for_mcptype(mt)
                    if meta_schema:
                        meta_parts.append(meta_schema)
                if meta_parts:
                    output = stdout + "\n" + "\n".join(meta_parts)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(output.encode("utf-8"))

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
        logger.info("Incoming request: tool=%s auth_key=%s", tool, "present" if auth_key else "absent")
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
                mcptype = tool.split(".")[0] if "." in tool else tool
                sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else get_auth_key_separator(auth_key)
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
                exec_cmd = ["gloves", "--agent", agent_id, "run", *env_flags, "--", *cmd]
            else:
                logger.warning("Invalid auth key format: %s", auth_key)
        try:
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
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
        response = {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
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
        env_vars = get_env_vars_for_mcptype(mcptype)
        if isinstance(env_vars, list) and len(env_vars) > 0 and token:
            extra_secrets[env_vars[0]] = token
        logger.debug("_handle_download_attachment: mcptype=%s, agent_id=%s, download_type=%s, token=%s", mcptype, agent_id, download_type, mask_token(token))
        try:
            if download_type == "rest_api":
                stream, filename, content_type = download_via_rest(config, args, token, extra_secrets)
            elif download_type == "mcp_tool_redirect":
                stream, filename, content_type = download_via_mcp_redirect(config, args, token, extra_secrets)
            elif download_type == "mcp_tool":
                stream, filename, content_type = download_via_mcp_direct(config, args, token, extra_secrets)
            else:
                self.send_error(400, f"Unknown attachment download type: {download_type}")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(filename, safe='')}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for chunk in stream:
                self.wfile.write(chunk)
        except (ConnectionResetError, BrokenPipeError, OSError, MCPToolError) as e:
            if isinstance(e, BrokenPipeError):
                logger.error("Download failed: client disconnected (broken pipe)")
            elif isinstance(e, ConnectionResetError):
                logger.error("Download failed: connection reset by client")
            else:
                logger.error("Download failed: %s", e)
            try:
                self.send_error(502, f"Download failed: {str(e)}")
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.debug("Client disconnected before error response could be sent")

    def _handle_upload_attachment(self):
        logger.debug("_handle_upload_attachment: headers=%s", dict(self.headers))
        auth_valid, auth_key, agent_id, agent_key = self._validate_upload_auth()
        if not auth_valid:
            return
        boundary = self._parse_upload_boundary()
        if not boundary:
            return
        mcptype, args = self._parse_upload_target_args()
        if not mcptype:
            return
        logger.debug("_handle_upload_attachment: mcptype=%s, args=%s", mcptype, args)
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
            result = self._execute_upload(upload_type, config, args, file_stream, agent_id, agent_key, mcptype)
            self._send_upload_response(result, args.get("name") or args.get("filename", "upload"))
        except (ConnectionResetError, BrokenPipeError, OSError, MCPToolError) as e:
            logger.error("Upload failed: %s", e)
            try:
                self.send_error(502, f"Upload failed: {str(e)}")
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.debug("Client disconnected before error response could be sent")

    def _validate_upload_auth(self):
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
        mcptype = self.headers.get("X-Target-Platform")
        if not mcptype:
            self.send_error(400, "Missing X-Target-Platform header")
            return None, None
        target_args_raw = self.headers.get("X-Target-Args", "{}")
        try:
            args = json.loads(target_args_raw)
        except json.JSONDecodeError:
            try:
                args_json = base64.b64decode(target_args_raw).decode("utf-8")
                args = json.loads(args_json)
            except Exception as e:
                self.send_error(400, f"Invalid X-Target-Args: {e}")
                return None, None
        if not isinstance(args, dict):
            self.send_error(400, "X-Target-Args must be a JSON object")
            return None, None
        return mcptype, args

    def _extract_upload_file(self, boundary):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty body")
            return None
        if MAX_UPLOAD_SIZE is not None and content_length > MAX_UPLOAD_SIZE:
            self.send_error(413, f"Upload size {content_length} exceeds limit {MAX_UPLOAD_SIZE}")
            return None
        body = self.rfile.read(content_length)
        result = self._extract_file_from_multipart(body, boundary)
        if not result:
            self.send_error(400, "No file part in multipart request")
            return None
        return result[0]

    def _execute_upload(self, upload_type, config, args, file_stream, agent_id, agent_key, mcptype):
        token = resolve_token(mcptype, agent_id, agent_key)
        extra_secrets = resolve_extra_secrets(mcptype, agent_id, agent_key)
        env_vars = get_env_vars_for_mcptype(mcptype)
        if isinstance(env_vars, list) and len(env_vars) > 0 and token:
            extra_secrets[env_vars[0]] = token
        logger.debug("_execute_upload: mcptype=%s, upload_type=%s", mcptype, upload_type)
        if upload_type == "rest_api":
            if hasattr(file_stream, "read_chunk"):
                file_data = b"".join(file_stream.read_chunk())
                file_stream = io.BytesIO(file_data)
            return upload_via_rest(config, args, file_stream, args.get("name") or args.get("filename", "upload"), args.get("content_type") or "application/octet-stream", token, extra_secrets)
        raise MCPToolError(f"Unknown attachment upload type: {upload_type}")

    def _send_upload_response(self, result, filename):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True, "id": result.get("id", result.get("attach", {}).get("id", "")), "url": result.get("url", result.get("download_url", "")), "filename": filename}).encode("utf-8"))

    def _parse_boundary(self, content_type: str) -> Optional[bytes]:
        if "boundary=" in content_type:
            boundary_start = content_type.find("boundary=") + len("boundary=")
            boundary_end = content_type.find(";", boundary_start)
            if boundary_end == -1:
                boundary_end = len(content_type)
            boundary_value = content_type[boundary_start:boundary_end].strip().strip('"')
            return boundary_value.encode()
        return None

    def _extract_file_from_multipart(self, body: bytes, boundary: bytes) -> Optional[Tuple[ChunkedFileReference, str]]:
        files = {}
        current_filename = None
        current_header_field = bytearray()
        current_file_chunks = []
        current_file_size = 0

        def on_part_begin():
            nonlocal current_filename, current_file_chunks, current_file_size
            current_filename = None
            current_file_chunks = []
            current_file_size = 0

        def on_header_field(data, start, end):
            nonlocal current_header_field
            current_header_field.extend(data[start:end])

        def on_header_value(data, start, end):
            nonlocal current_filename, current_header_field
            field = current_header_field.decode("utf-8", errors="replace")
            value = data[start:end].decode("utf-8", errors="replace")
            current_header_field = bytearray()
            if field.lower() == "content-disposition" and "filename=" in value:
                for part in value.split(";"):
                    part = part.strip()
                    if part.startswith("filename="):
                        current_filename = part.split("=", 1)[1].strip().strip('"')

        def on_header_end():
            pass

        def on_headers_finished():
            pass

        def on_part_data(data, start, end):
            nonlocal current_file_size
            if current_filename:
                current_file_chunks.append((data, start, end))
                current_file_size += end - start

        def on_part_end():
            nonlocal current_filename, current_file_chunks, current_file_size, files
            if current_filename:
                files[current_filename] = (current_file_chunks, current_file_size)

        def on_end():
            pass

        callbacks = {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_end": on_end,
        }

        parser = MultipartParser(boundary, callbacks=callbacks)
        parser.write(body)
        parser.finalize()

        if not files:
            return None

        filename, (chunks, total_size) = next(iter(files.items()))
        chunk_refs = [(data[start:end], end - start) for data, start, end in chunks]
        return ChunkedFileReference(chunk_refs, total_size), filename

    def log_message(self, format, *args):
        pass


__all__ = ["ChunkedFileReference", "MCPorterProxyHandler"]
