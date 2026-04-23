#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection

SERVER_PORT = 9904
SERVER_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server", "server.py"
)
ENV_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server", "mcp_env_map.json"
)


class TestServerAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_dir = tempfile.mkdtemp()
        cls.mock_mcporter_path = os.path.join(cls.mock_dir, "mcporter")
        with open(cls.mock_mcporter_path, "w") as f:
            f.write("""#!/bin/bash
echo '{"result": "mock"}'
exit 0
""")
        os.chmod(cls.mock_mcporter_path, 0o755)

        cls.mock_gloves_path = os.path.join(cls.mock_dir, "gloves")
        with open(cls.mock_gloves_path, "w") as f:
            f.write("""#!/bin/bash
echo "mock_token_from_gloves"
exit 0
""")
        os.chmod(cls.mock_gloves_path, 0o755)

        cls.env = os.environ.copy()
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "test_tool_*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.server_process = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.wait()
        os.unlink(cls.mock_mcporter_path)
        os.unlink(cls.mock_gloves_path)
        os.rmdir(cls.mock_dir)

    def test_request_without_auth(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        body = json.dumps({"tool": "test_tool_foo", "args": {}})
        conn.request(
            "POST", "/call", body=body, headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode())
        self.assertIn("stdout", data)
        conn.close()

    def test_request_with_auth(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        body = json.dumps({"tool": "test_tool_bar", "args": {}})
        conn.request(
            "POST",
            "/call",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-MCP-Auth-Key": "agent123-secret",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode())
        self.assertIn("stdout", data)
        conn.close()

    def test_forbidden_tool(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        body = json.dumps({"tool": "forbidden_tool", "args": {}})
        conn.request(
            "POST", "/call", body=body, headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 403)
        conn.close()

    def test_wrong_path(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "POST", "/wrong", body="{}", headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        conn.close()


class TestEnvMap(unittest.TestCase):
    def test_env_map_loads(self):
        with open(ENV_MAP_PATH) as f:
            env_map = json.load(f)
        self.assertIn("github", env_map)
        self.assertIn("gitlab", env_map)


if __name__ == "__main__":
    unittest.main()
