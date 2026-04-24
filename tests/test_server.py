#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

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
            f.write(f"""#!/bin/bash
# Parse gloves run --env VAR=gloves://key -- command args
while [[ $# -gt 0 ]]; do
    case $1 in
        --env)
            shift
            env_spec="$1"
            ;;
        --)
            shift
            break
            ;;
        *)
            shift
            ;;
    esac
    shift
done
# Execute remaining command (mcporter)
exec "$@"
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
        self.assertIsInstance(env_map["github"], list)
        self.assertIsInstance(env_map["gitlab"], list)
        self.assertEqual(env_map["github"][0], "GITHUB_PERSONAL_ACCESS_TOKEN")
        self.assertEqual(env_map["gitlab"][0], "GITLAB_TOKEN")


class TestAuthKeyParsing(unittest.TestCase):
    def test_parse_auth_key_with_dash(self):
        from server.server import parse_auth_key

        result = parse_auth_key("agent-key123")
        self.assertEqual(result, ("agent", "key123"))

    def test_parse_auth_key_with_slash(self):
        from server.server import parse_auth_key

        result = parse_auth_key("agent/key123")
        self.assertEqual(result, ("agent", "key123"))

    def test_parse_auth_key_invalid(self):
        from server.server import parse_auth_key

        result = parse_auth_key("invalid_no_separator")
        self.assertIsNone(result)

    def test_parse_auth_key_empty(self):
        from server.server import parse_auth_key

        result = parse_auth_key("")
        self.assertIsNone(result)


class TestSecretsKeyFormat(unittest.TestCase):
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
            f.write(f"""#!/bin/bash
while [[ $# -gt 0 ]]; do
    case $1 in
        --env)
            shift
            echo "$1" > /tmp/env_spec.txt
            ;;
        --)
            shift
            break
            ;;
    esac
    shift
done
exec "$@"
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
        if os.path.exists("/tmp/env_spec.txt"):
            os.unlink("/tmp/env_spec.txt")

    def test_secrets_key_default_format(self):
        if os.path.exists("/tmp/env_spec.txt"):
            os.unlink("/tmp/env_spec.txt")
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
        conn.close()

        with open("/tmp/env_spec.txt") as f:
            env_spec = f.read().strip()
        self.assertIn("=gloves://", env_spec)
        _, key = env_spec.split("=gloves://")
        self.assertEqual(key, "agents-agent123-test_tool_bar-secret")


class TestArrayEnvVarsLogic(unittest.TestCase):
    def test_secrets_keys_for_array_env_vars(self):
        agent_id = "agent456"
        agent_key = "mykey"
        mcptype = "github"
        env_vars = ["GITHUB_TOKEN", "GITHUB_TOKEN_1", "GITHUB_TOKEN_2"]
        expected_keys = [
            "agents/agent456/github/mykey",
            "agents/agent456/github/mykey/1",
            "agents/agent456/github/mykey/2",
        ]
        for i, env_var in enumerate(env_vars):
            if i == 0:
                secrets_key = f"agents/{agent_id}/{mcptype}/{agent_key}"
            else:
                secrets_key = f"agents/{agent_id}/{mcptype}/{agent_key}/{i}"
            self.assertEqual(secrets_key, expected_keys[i])
            self.assertEqual(
                f"{env_var}=gloves://{secrets_key}",
                f"{env_vars[i]}=gloves://{expected_keys[i]}",
            )


if __name__ == "__main__":
    unittest.main()
