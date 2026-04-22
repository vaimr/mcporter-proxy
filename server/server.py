#!/usr/bin/env python3
"""
HTTP proxy for mcporter – securely executes mcporter commands from sandboxed agents.
Configuration via environment variables:
  - MCPORTER_PROXY_PORT: listening port (default 8080)
  - MCPORTER_PROXY_ALLOWED_TOOLS: comma-separated list of tool patterns (supports * wildcard)
  - MCPORTER_PROXY_TIMEOUT: execution timeout in seconds (default 120)
  - MCPORTER_PROXY_LOG_LEVEL: log level (DEBUG, INFO, WARNING, ERROR, CRITICAL; default INFO)
"""

import json
import logging
import os
import re
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Optional

# Configure logging
LOG_LEVEL = os.environ.get("MCPORTER_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mcporter-proxy")


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

    def do_POST(self):
        if self.path != "/call":
            logger.debug(f"404 for path: {self.path}")
            self.send_error(404, "Endpoint not found")
            return

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

        logger.info(f"Incoming request: tool={tool}")

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

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {self.timeout}s: {tool}")
            self.send_error(504, f"Command timed out after {self.timeout}s")
            return
        except FileNotFoundError:
            logger.error("mcporter executable not found")
            self.send_error(500, "mcporter executable not found")
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

    def log_message(self, format, *args):
        # Suppress default HTTP logging - we use structured logging instead
        pass


def main():
    port = int(os.environ.get("MCPORTER_PROXY_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), MCPorterProxyHandler)
    print(f"mcporter-proxy listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
