#!/usr/bin/env python3
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from http.client import HTTPConnection
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

SERVER_PORT = 9904
SERVER_PORT_DOWNLOAD = 9905
SERVER_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server", "server.py"
)
ENV_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server", "mcp_env_map.json"
)
BACKUP_ENV_MAP_PATH = os.path.join(tempfile.gettempdir(), "mcp_env_map.json.backup")


class TestEnvMap(unittest.TestCase):
    def setUp(self):
        with open(ENV_MAP_PATH, "r") as f:
            self.original_env_map = f.read()
        self.env_map = json.loads(self.original_env_map)

    def tearDown(self):
        with open(ENV_MAP_PATH, "w") as f:
            f.write(self.original_env_map)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_env_map_structure(self):
        self.assertIn("github", self.env_map)
        self.assertIn("gitlab", self.env_map)
        self.assertIn("confluence", self.env_map)
        self.assertIn("jira", self.env_map)

    def test_github_config(self):
        github = self.env_map["github"]
        self.assertIsInstance(github, dict)
        self.assertIn("env", github)
        self.assertIn("attachment_download", github)
        self.assertEqual(github["env"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertEqual(github["attachment_download"]["type"], "rest_api")
        self.assertIn("url_template", github["attachment_download"])

    def test_gitlab_config(self):
        gitlab = self.env_map["gitlab"]
        self.assertIsInstance(gitlab, dict)
        self.assertIn("env", gitlab)
        self.assertIn("attachment_download", gitlab)

    def test_confluence_config(self):
        confluence = self.env_map["confluence"]
        self.assertIsInstance(confluence, dict)
        self.assertEqual(confluence["attachment_download"]["type"], "mcp_tool_redirect")
        self.assertIn("tool_name", confluence["attachment_download"])

    def test_jira_config(self):
        jira = self.env_map["jira"]
        self.assertIsInstance(jira, dict)
        self.assertEqual(jira["attachment_download"]["type"], "rest_api")


class TestTemplateSubstitution(unittest.TestCase):
    def test_substitute_template_simple(self):
        from server.server import substitute_template

        result = substitute_template("Hello {name}", {"name": "World"})
        self.assertEqual(result, "Hello World")

    def test_substitute_template_multiple(self):
        from server.server import substitute_template

        result = substitute_template(
            "https://api.github.com/repos/{owner}/{repo}",
            {"owner": "user", "repo": "repo"},
        )
        self.assertEqual(result, "https://api.github.com/repos/user/repo")

    def test_substitute_template_token(self):
        from server.server import substitute_template

        result = substitute_template(
            "Authorization: Bearer {token}", {"token": "secret123"}
        )
        self.assertEqual(result, "Authorization: Bearer secret123")

    def test_substitute_template_missing_key(self):
        from server.server import substitute_template

        result = substitute_template("Hello {name}", {})
        self.assertEqual(result, "Hello {name}")

    def test_substitute_template_numeric_value(self):
        from server.server import substitute_template

        result = substitute_template("Page {page_id}", {"page_id": 123})
        self.assertEqual(result, "Page 123")


class TestGetEnvVarsForMcptype(unittest.TestCase):
    def setUp(self):
        with open(ENV_MAP_PATH, "r") as f:
            self.original_env_map = f.read()
        import importlib
        import server.server

        importlib.reload(server.server)

    def tearDown(self):
        with open(ENV_MAP_PATH, "w") as f:
            f.write(self.original_env_map)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_get_env_vars_github(self):
        from server.server import get_env_vars_for_mcptype

        result = get_env_vars_for_mcptype("github")
        self.assertEqual(result, ["GITHUB_PERSONAL_ACCESS_TOKEN"])

    def test_get_env_vars_unknown(self):
        from server.server import get_env_vars_for_mcptype

        result = get_env_vars_for_mcptype("unknown_mcptype")
        self.assertEqual(result, [])


class TestGetAttachmentDownloadConfig(unittest.TestCase):
    def setUp(self):
        with open(ENV_MAP_PATH, "r") as f:
            self.original_env_map = f.read()
        import importlib
        import server.server

        importlib.reload(server.server)

    def tearDown(self):
        with open(ENV_MAP_PATH, "w") as f:
            f.write(self.original_env_map)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_get_attachment_download_config_github(self):
        from server.server import get_attachment_download_config

        config = get_attachment_download_config("github")
        self.assertIsNotNone(config)
        self.assertEqual(config["type"], "rest_api")
        self.assertIn("url_template", config)

    def test_get_attachment_download_config_unknown(self):
        from server.server import get_attachment_download_config

        config = get_attachment_download_config("unknown")
        self.assertIsNone(config)


class TestGenerateMultipart(unittest.TestCase):
    def test_generate_multipart_basic(self):
        from server.server import generate_multipart

        data = b"Hello World"
        stream = io.BytesIO(data)
        chunks = list(generate_multipart(stream, "test.txt", "text/plain"))

        result = b"".join(chunks)
        self.assertIn(b"--simpleboundary", result)
        self.assertIn(
            b'Content-Disposition: form-data; name="file"; filename="test.txt"', result
        )
        self.assertIn(b"Content-Type: text/plain", result)
        self.assertIn(b"Hello World", result)
        self.assertIn(b"--simpleboundary--", result)

    def test_generate_multipart_custom_boundary(self):
        from server.server import generate_multipart

        data = b"Test content"
        stream = io.BytesIO(data)
        chunks = list(
            generate_multipart(
                stream, "file.pdf", "application/pdf", boundary="customboundary"
            )
        )

        result = b"".join(chunks)
        self.assertIn(b"--customboundary", result)
        self.assertIn(b"--customboundary--", result)
        self.assertNotIn(b"--simpleboundary", result)


class TestDownloadAttachmentEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ENV_MAP_PATH, "r") as f:
            cls.original_env_map = f.read()

        test_config = {
            "github": {
                "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
                "attachment_download": {
                    "type": "rest_api",
                    "method": "GET",
                    "url_template": "https://httpbin.org/get",
                    "headers": {"Authorization": "Bearer {token}"},
                },
            },
            "confluence_test": {
                "env": ["CONFLUENCE_TOKEN"],
                "attachment_download": {
                    "type": "mcp_tool_redirect",
                    "tool_name": "test_tool_download",
                    "tool_args_mapping": {"file_id": "{attachment_id}"},
                    "download_url_field": "download_url",
                    "headers": {"Authorization": "Bearer {token}"},
                },
            },
            "mcp_tool_test": {
                "env": ["TEST_TOKEN"],
                "attachment_download": {
                    "type": "mcp_tool",
                    "tool_name": "test_tool_direct",
                    "tool_args_mapping": {"file_id": "{file_id}"},
                },
            },
        }
        with open(ENV_MAP_PATH, "w") as f:
            json.dump(test_config, f)

        import importlib
        import server.server

        importlib.reload(server.server)

        cls.mock_dir = tempfile.mkdtemp()
        cls.mock_mcporter_path = os.path.join(cls.mock_dir, "mcporter")
        with open(cls.mock_mcporter_path, "w") as f:
            f.write("""#!/bin/bash
echo '{"download_url": "https://example.com/file.pdf"}'
exit 0
""")
        os.chmod(cls.mock_mcporter_path, 0o755)

        cls.mock_gloves_path = os.path.join(cls.mock_dir, "gloves")
        with open(cls.mock_gloves_path, "w") as f:
            f.write("""#!/bin/bash
if [[ "$*" == *"get"* ]]; then
    echo "test_token_123"
fi
exit 0
""")
        os.chmod(cls.mock_gloves_path, 0o755)

        cls.env = os.environ.copy()
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT_DOWNLOAD)
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
        with open(ENV_MAP_PATH, "w") as f:
            f.write(cls.original_env_map)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_download_requires_auth(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD)
        body = json.dumps({"mcptype": "github", "args": {"asset_id": "123"}})
        conn.request(
            "POST",
            "/download-attachment",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)
        conn.close()

    def test_download_invalid_auth_key_format(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD)
        body = json.dumps({"mcptype": "github", "args": {"asset_id": "123"}})
        conn.request(
            "POST",
            "/download-attachment",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-MCP-Auth-Key": "invalid_no_separator",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)
        conn.close()

    def test_download_missing_mcptype(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD)
        body = json.dumps({"args": {"asset_id": "123"}})
        conn.request(
            "POST",
            "/download-attachment",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-MCP-Auth-Key": "agent-key",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        self.assertIn(b"mcptype", resp.read().lower())
        conn.close()

    def test_download_unknown_mcptype(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD)
        body = json.dumps({"mcptype": "unknown_platform", "args": {"id": "123"}})
        conn.request(
            "POST",
            "/download-attachment",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-MCP-Auth-Key": "agent-key",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        self.assertIn(b"unknown_platform", resp.read())
        conn.close()

    @unittest.skipIf(
        sys.version_info >= (3, 14),
        "HTTPSConnection tuple timeout broken in Python 3.14+",
    )
    def test_download_success_response_format(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD)
        body = json.dumps({"mcptype": "github", "args": {"asset_id": "123"}})
        conn.request(
            "POST",
            "/download-attachment",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-MCP-Auth-Key": "agent-key",
            },
        )
        resp = conn.getresponse()
        response_body = resp.read()
        if resp.status != 200:
            print(
                f"DEBUG: Response status {resp.status}, body: {response_body.decode('utf-8', errors='replace')[:500]}"
            )
        self.assertEqual(
            resp.status,
            200,
            f"Expected 200, got {resp.status}: {response_body.decode('utf-8', errors='replace')[:200]}",
        )
        content_type = resp.getheader("Content-Type", "")
        self.assertIn("multipart/form-data", content_type)
        conn.close()


class TestDownloadAttachmentMocked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ENV_MAP_PATH, "r") as f:
            cls.original_env_map = f.read()

        simple_config = {"github": ["GITHUB_TOKEN"], "gitlab": ["GITLAB_TOKEN"]}
        with open(ENV_MAP_PATH, "w") as f:
            json.dump(simple_config, f)

        import importlib
        import server.server

        importlib.reload(server.server)

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
        with open(ENV_MAP_PATH, "w") as f:
            f.write(cls.original_env_map)
        import importlib
        import server.server

        importlib.reload(server.server)

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


class TestMaxBase64Size(unittest.TestCase):
    def test_base64_size_limit_exceeded(self):
        from server.server import download_via_mcp_direct
        import unittest.mock

        mock_result = unittest.mock.MagicMock()
        mock_result.returncode = 0
        large_content = "x" * (6 * 1024 * 1024)
        mock_result.stdout = json.dumps({"file_content": large_content, "base64": True})

        config = {
            "tool_name": "test_tool",
            "tool_args_mapping": {},
            "max_base64_size": 5 * 1024 * 1024,
        }

        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(Exception) as ctx:
                download_via_mcp_direct(
                    config,
                    {
                        "filename": "large.bin",
                        "content_type": "application/octet-stream",
                    },
                    None,
                    {},
                )
            self.assertIn("exceeds limit", str(ctx.exception))

    def test_base64_size_within_limit(self):
        from server.server import download_via_mcp_direct
        import unittest.mock

        mock_result = unittest.mock.MagicMock()
        mock_result.returncode = 0
        small_content = "SGVsbG8gV29ybGQ="
        mock_result.stdout = json.dumps({"file_content": small_content, "base64": True})

        config = {
            "tool_name": "test_tool",
            "tool_args_mapping": {},
            "max_base64_size": 5 * 1024 * 1024,
        }

        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            stream, filename, content_type = download_via_mcp_direct(
                config,
                {"filename": "small.bin", "content_type": "application/octet-stream"},
                None,
                {},
            )
            chunks = list(stream)
            self.assertEqual(b"".join(chunks), b"Hello World")

    def test_mcp_tool_with_download_url_field(self):
        from server.server import download_via_mcp_direct
        import unittest.mock

        mock_result = unittest.mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"download_url": "https://example.com/file.pdf"}
        )

        mock_response = unittest.mock.MagicMock()
        mock_response.getheader.side_effect = lambda name, default=None: {
            "Content-Type": "application/octet-stream",
        }.get(name, default)
        mock_response.read.side_effect = [b"pdf content", b""]

        mock_conn = unittest.mock.MagicMock()
        mock_conn.getresponse.return_value = mock_response

        config = {
            "tool_name": "test_tool",
            "tool_args_mapping": {},
            "download_url_field": "download_url",
            "headers": {"Authorization": "Bearer {token}"},
        }

        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            with unittest.mock.patch(
                "http.client.HTTPSConnection", return_value=mock_conn
            ):
                stream, filename, content_type = download_via_mcp_direct(
                    config,
                    {"filename": "doc.pdf", "content_type": "application/pdf"},
                    "test_token",
                    {},
                )
                chunks = list(stream)
                self.assertEqual(b"".join(chunks), b"pdf content")
                self.assertEqual(filename, "doc.pdf")


class TestHealthEndpoint(unittest.TestCase):
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
exec "$@"
exit 0
""")
        os.chmod(cls.mock_gloves_path, 0o755)

        cls.env = os.environ.copy()
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT)
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

    def test_health_endpoint(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode())
        self.assertEqual(data.get("status"), "ok")
        conn.close()


class TestConfigValidation(unittest.TestCase):
    def test_invalid_type_validation(self):
        from server.server import validate_config

        config = {"attachment_download": {"type": "invalid_type"}}
        errors = validate_config(config, "test_mcptype")
        self.assertTrue(any("download type" in e.lower() for e in errors))

    def test_rest_api_missing_url_template(self):
        from server.server import validate_config

        config = {"attachment_download": {"type": "rest_api"}}
        errors = validate_config(config, "test_mcptype")
        self.assertTrue(any("url_template" in e for e in errors))

    def test_mcp_tool_missing_tool_name(self):
        from server.server import validate_config

        config = {"attachment_download": {"type": "mcp_tool"}}
        errors = validate_config(config, "test_mcptype")
        self.assertTrue(any("tool_name" in e for e in errors))

    def test_valid_config_no_errors(self):
        from server.server import validate_config

        config = {
            "attachment_download": {
                "type": "rest_api",
                "url_template": "https://example.com/{id}",
            }
        }
        errors = validate_config(config, "test_mcptype")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
