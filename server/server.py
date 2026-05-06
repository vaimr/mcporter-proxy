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

import logging
import os
import signal
import sys
from http.server import HTTPServer
from socketserver import ThreadingMixIn

# Module import: use relative imports
from .utils import mask_token, mask_headers_for_log, substitute_template, _camel_to_kebab
from .config import (
    get_env_map_path,
    validate_config,
    load_env_map,
    get_env_vars_for_mcptype,
    get_attachment_download_config,
    get_attachment_upload_config,
    MCP_ENV_MAP,
)
from .auth import (
    parse_auth_key,
    get_mcptype,
    get_auth_key_separator,
    get_secrets_keys,
    get_available_secrets,
    resolve_token,
    resolve_extra_secrets,
)
from .schema import run_mcporter_list_schema, _parse_mcporter_schema_output, format_tool_as_text_schema
from .tools import (
    build_attachment_schema,
    transform_meta_tool,
    build_attachment_text_schema_for_mcptype,
    inject_attachment_tools,
    add_attachment_tools_to_server,
    _get_download_args_properties,
    _get_upload_args_properties,
)
from .download import (
    MCPToolError,
    base64_a85_decode_stream,
    build_headers,
    download_via_rest,
    download_via_mcp_redirect,
    download_via_mcp_direct,
)
from .upload import (
    build_upload_headers,
    upload_via_rest,
    upload_via_rest_streaming,
)
from .handlers import ChunkedFileReference, MCPorterProxyHandler

LOG_LEVEL = os.environ.get("MCPORTER_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mcporter-proxy")


def main():
    port = int(os.environ.get("MCPORTER_PROXY_PORT", "8080"))

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadingHTTPServer(("0.0.0.0", port), MCPorterProxyHandler)

    def shutdown_handler(_signum, _frame):
        logger.info("Received shutdown signal, closing server...")
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    print(f"mcporter-proxy listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()