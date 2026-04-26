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
TEST_ENV_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "test_config.json"
)
BACKUP_ENV_MAP_PATH = os.path.join(tempfile.gettempdir(), "mcp_env_map.json.backup")


class TestEnvMap(unittest.TestCase):
    def setUp(self):
        os.environ["MCP_ENV_MAP_PATH"] = TEST_ENV_MAP_PATH
        import importlib
        import server.server

        importlib.reload(server.server)

    def tearDown(self):
        if "MCP_ENV_MAP_PATH" in os.environ:
            del os.environ["MCP_ENV_MAP_PATH"]
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_env_map_structure(self):
        from server.server import MCP_ENV_MAP

        self.assertIn("github", MCP_ENV_MAP)
        self.assertIn("gitlab", MCP_ENV_MAP)
        self.assertIn("confluence", MCP_ENV_MAP)
        self.assertIn("jira", MCP_ENV_MAP)

    def test_github_config(self):
        from server.server import MCP_ENV_MAP

        github = MCP_ENV_MAP["github"]
        self.assertIsInstance(github, dict)
        self.assertIn("env", github)
        self.assertIn("attachment_download", github)
        self.assertEqual(github["env"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertEqual(github["attachment_download"]["type"], "rest_api")
        self.assertIn("url_template", github["attachment_download"])

    def test_gitlab_config(self):
        from server.server import MCP_ENV_MAP

        gitlab = MCP_ENV_MAP["gitlab"]
        self.assertIsInstance(gitlab, dict)
        self.assertIn("env", gitlab)
        self.assertIn("attachment_download", gitlab)

    def test_confluence_config(self):
        from server.server import MCP_ENV_MAP

        confluence = MCP_ENV_MAP["confluence"]
        self.assertIsInstance(confluence, dict)
        self.assertEqual(confluence["attachment_download"]["type"], "mcp_tool_redirect")
        self.assertIn("tool_name", confluence["attachment_download"])

    def test_jira_config(self):
        from server.server import MCP_ENV_MAP

        jira = MCP_ENV_MAP["jira"]
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
        os.environ["MCP_ENV_MAP_PATH"] = TEST_ENV_MAP_PATH
        import importlib
        import server.server

        importlib.reload(server.server)

    def tearDown(self):
        if "MCP_ENV_MAP_PATH" in os.environ:
            del os.environ["MCP_ENV_MAP_PATH"]
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
        os.environ["MCP_ENV_MAP_PATH"] = TEST_ENV_MAP_PATH
        import importlib
        import server.server

        importlib.reload(server.server)

    def tearDown(self):
        if "MCP_ENV_MAP_PATH" in os.environ:
            del os.environ["MCP_ENV_MAP_PATH"]
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
    def test_download_success_raw_content_no_multipart_boundary(self):
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
        content_disposition = resp.getheader("Content-Disposition", "")
        self.assertIn("attachment", content_disposition.lower())
        self.assertIn("filename*=", content_disposition)
        self.assertNotIn("multipart/form-data", content_type)
        self.assertNotIn(
            "--simpleboundary", response_body.decode("utf-8", errors="replace")
        )
        self.assertNotIn(
            "Content-Disposition: form-data",
            response_body.decode("utf-8", errors="replace"),
        )
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


class TestDownloadViaRest(unittest.TestCase):
    """Test download_via_rest function with HTTP redirect handling."""

    def test_download_via_rest_no_redirect(self):
        from server.server import download_via_rest
        from unittest.mock import patch, MagicMock

        config = {
            "method": "GET",
            "url_template": "https://example.com/file/{id}",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {"id": "123", "filename": "test.txt"}

        mock_response = MagicMock()
        mock_response.headers = {
            "Content-Type": "text/plain",
            "Content-Disposition": 'attachment; filename="test.txt"',
        }
        mock_response.read = MagicMock(side_effect=[b"file content", b""])

        with patch("urllib.request.urlopen", return_value=mock_response):
            gen, filename, content_type = download_via_rest(
                config, args, "token123", {}
            )

            chunks = list(gen)
            self.assertEqual(b"".join(chunks), b"file content")
            self.assertEqual(filename, "test.txt")
            self.assertEqual(content_type, "text/plain")

    def test_download_via_rest_with_redirect(self):
        from server.server import download_via_rest
        from unittest.mock import patch, MagicMock

        config = {
            "method": "GET",
            "url_template": "http://example.com/file/{id}",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {
            "id": "123"
        }  # no filename in args, should fallback to Content-Disposition

        mock_response = MagicMock()
        mock_response.headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="downloaded.txt"',
        }
        mock_response.read = MagicMock(side_effect=[b"redirected content", b""])

        mock_request = MagicMock()
        mock_request.get_full_url = MagicMock(
            return_value="https://example.com/file/123"
        )

        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            gen, filename, content_type = download_via_rest(
                config, args, "token123", {}
            )

            chunks = list(gen)
            self.assertEqual(b"".join(chunks), b"redirected content")
            self.assertEqual(filename, "downloaded.txt")

    def test_download_via_rest_filename_from_url(self):
        from server.server import download_via_rest
        from unittest.mock import patch, MagicMock

        config = {
            "method": "GET",
            "url_template": "https://example.com/downloads/report.pdf",
            "headers": {},
        }
        args = {}

        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "application/pdf"}
        mock_response.read = MagicMock(side_effect=[b"PDF content", b""])

        with patch("urllib.request.urlopen", return_value=mock_response):
            gen, filename, content_type = download_via_rest(config, args, None, {})

            chunks = list(gen)
            self.assertEqual(filename, "report.pdf")

    def test_download_via_rest_post_with_body(self):
        from server.server import download_via_rest
        from unittest.mock import patch, MagicMock

        config = {
            "method": "POST",
            "url_template": "https://example.com/api/download",
            "headers": {"X-Api-Key": "secret"},
            "body_template": '{"file_id": "{id}"}',
        }
        args = {"id": "456"}

        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "application/octet-stream"}
        mock_response.read = MagicMock(side_effect=[b"post response", b""])

        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            gen, filename, content_type = download_via_rest(config, args, None, {})

            chunks = list(gen)
            self.assertEqual(b"".join(chunks), b"post response")
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            self.assertEqual(request.get_method(), "POST")


class TestBuildHeaders(unittest.TestCase):
    """Test build_headers function with ${ENV_VAR} syntax."""

    def test_build_headers_with_token_placeholder(self):
        from server.server import build_headers

        headers_template = {"Authorization": "Bearer {token}"}
        token = "secret_token_value"
        extra_secrets = {}
        args = {}

        headers = build_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer secret_token_value")

    def test_build_headers_with_env_var_syntax(self):
        from server.server import build_headers

        headers_template = {"Authorization": "Bearer ${ATLASSIAN_TOKEN}"}
        token = "primary_token"
        extra_secrets = {"ATLASSIAN_TOKEN": "resolved_token_from_gloves"}
        args = {}

        headers = build_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer resolved_token_from_gloves")

    def test_build_headers_with_multiple_env_vars(self):
        from server.server import build_headers

        headers_template = {
            "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
            "X-Custom": "Token1=${TOKEN_1}, Token2=${TOKEN_2}",
        }
        token = "primary_token"
        extra_secrets = {
            "ATLASSIAN_TOKEN": "token1",
            "TOKEN_1": "extra1",
            "TOKEN_2": "extra2",
        }
        args = {}

        headers = build_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer token1")
        self.assertEqual(headers["X-Custom"], "Token1=extra1, Token2=extra2")

    def test_build_headers_env_var_fallback_to_os_environ(self):
        import os
        from server.server import build_headers

        os.environ["TEST_OS_VAR"] = "os_value"
        headers_template = {"X-Test": "Value=${TEST_OS_VAR}"}
        token = "token"
        extra_secrets = {}  # not in extra_secrets
        args = {}

        headers = build_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["X-Test"], "Value=os_value")
        del os.environ["TEST_OS_VAR"]

    def test_build_headers_with_basic_auth(self):
        from server.server import build_headers

        headers_template = {"Authorization": "Basic {basic_auth}"}
        token = "api_token"
        extra_secrets = {"email": "user@example.com"}
        args = {}

        headers = build_headers(headers_template, token, extra_secrets, args)
        # Basic auth is email:token base64 encoded
        import base64

        expected = base64.b64encode(b"user@example.com:api_token").decode()
        self.assertEqual(headers["Authorization"], f"Basic {expected}")

    def test_build_headers_with_args_substitution(self):
        from server.server import build_headers

        headers_template = {"X-Filename": "{filename}"}
        token = "token"
        extra_secrets = {}
        args = {"filename": "report.pdf"}

        headers = build_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["X-Filename"], "report.pdf")

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


class TestSchemaEndpoint(unittest.TestCase):
    def test_parse_mcporter_schema_output_basic(self):
        from server.server import _parse_mcporter_schema_output

        sample = """cognee

  /**
   * Transform ingested data into a knowledge graph.
   */
  function cognify(data: string);
      {
        "type": "object",
        "properties": {
          "data": {"type": "string"}
        },
        "required": ["data"],
        "title": "cognifyArguments"
      }

  Examples:
"""
        tools = _parse_mcporter_schema_output(sample)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "cognify")
        self.assertEqual(
            tools[0]["description"], "Transform ingested data into a knowledge graph."
        )
        self.assertEqual(tools[0]["inputSchema"]["type"], "object")
        self.assertIn("data", tools[0]["inputSchema"]["properties"])

    def test_parse_mcporter_schema_output_multiple_tools(self):
        from server.server import _parse_mcporter_schema_output

        sample = """cognee

  /**
   * First tool description.
   */
  function tool_one(param: string);
      {
        "type": "object",
        "properties": {
          "param": {"type": "string"}
        }
      }

  /**
   * Second tool description.
   */
  function tool_two(value: number);
      {
        "type": "object",
        "properties": {
          "value": {"type": "number"}
        }
      }

  Examples:
"""
        tools = _parse_mcporter_schema_output(sample)
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["name"], "tool_one")
        self.assertEqual(tools[1]["name"], "tool_two")

    def test_parse_mcporter_schema_output_no_description(self):
        from server.server import _parse_mcporter_schema_output

        sample = """cognee

  function no_desc(param: string);
      {
        "type": "object",
        "properties": {
          "param": {"type": "string"}
        }
      }

  Examples:
"""
        tools = _parse_mcporter_schema_output(sample)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "no_desc")
        self.assertEqual(tools[0]["description"], "")

    def test_build_attachment_schema_download(self):
        from server.server import build_attachment_schema

        schema = build_attachment_schema("github", {}, "download")
        self.assertEqual(schema["name"], "github.attachment_download")
        self.assertEqual(schema["_proxy_endpoint"], "POST /download-attachment")
        self.assertEqual(schema["_proxy_direction"], "download")
        self.assertIn("args", schema["inputSchema"]["properties"])
        args_props = schema["inputSchema"]["properties"]["args"]["properties"]
        self.assertIn("owner", args_props)
        self.assertIn("repo", args_props)
        self.assertIn("asset_id", args_props)

    def test_build_attachment_schema_upload(self):
        from server.server import build_attachment_schema

        schema = build_attachment_schema("github", {}, "upload")
        self.assertEqual(schema["name"], "github.attachment_upload")
        self.assertEqual(schema["_proxy_endpoint"], "POST /upload-attachment")
        self.assertIn("file", schema["inputSchema"]["properties"]["args"]["properties"])

    def test_build_attachment_schema_confluence(self):
        from server.server import build_attachment_schema

        schema = build_attachment_schema("confluence", {}, "download")
        args_props = schema["inputSchema"]["properties"]["args"]["properties"]
        self.assertIn("page_id", args_props)
        self.assertIn("filename", args_props)

    def test_build_attachment_schema_jira(self):
        from server.server import build_attachment_schema

        schema = build_attachment_schema("jira", {}, "upload")
        args_props = schema["inputSchema"]["properties"]["args"]["properties"]
        self.assertIn("issue_key", args_props)
        self.assertIn("name", args_props)
        self.assertIn("file", args_props)

    def test_run_mcporter_list_schema_file_not_found(self):
        from server.server import run_mcporter_list_schema

        tools, error = run_mcporter_list_schema("nonexistent")
        self.assertEqual(tools, [])
        self.assertIn("not found", error)

    def test_run_mcporter_list_schema_empty_output(self):
        from server.server import _parse_mcporter_schema_output

        tools = _parse_mcporter_schema_output("")
        self.assertEqual(tools, [])

    def test_run_mcporter_list_schema_malformed_json(self):
        from server.server import _parse_mcporter_schema_output

        sample = """cognee

  function broken(data: string);
      {
        "type": "object",
        "properties": {
          "data": {"type": "string"
        }
      }

  Examples:
"""
        tools = _parse_mcporter_schema_output(sample)
        self.assertEqual(len(tools), 0)

    def test_get_download_args_properties_cognee(self):
        from server.server import _get_download_args_properties

        props = _get_download_args_properties("cognee")
        self.assertIn("dataset_name", props)
        self.assertEqual(props["dataset_name"]["type"], "string")

    def test_get_upload_args_properties_cognee(self):
        from server.server import _get_upload_args_properties

        props = _get_upload_args_properties("cognee")
        self.assertIn("data", props)
        self.assertIn("dataset_name", props)

    def test_build_attachment_schema_mcptype_const(self):
        from server.server import build_attachment_schema

        schema = build_attachment_schema("gitlab", {}, "download")
        mcptype_const = schema["inputSchema"]["properties"]["mcptype"]
        self.assertEqual(mcptype_const, {"const": "gitlab"})


class TestInjectAttachmentTools(unittest.TestCase):
    def test_inject_to_list_mode(self):
        from server.server import inject_attachment_tools
        from unittest.mock import patch

        mock_config = {
            "type": "rest_api",
            "url_template": "https://example.com/{id}",
        }

        data = {
            "mode": "list",
            "servers": [
                {"name": "github", "tools": [{"name": "list_repos"}]},
                {"name": "gitlab", "tools": []},
            ],
        }
        with (
            patch(
                "server.server.get_attachment_download_config", return_value=mock_config
            ),
            patch(
                "server.server.get_attachment_upload_config", return_value=mock_config
            ),
        ):
            inject_attachment_tools(data)

        github_tools = data["servers"][0]["tools"]
        self.assertTrue(
            any(t["name"] == "github.attachment_download" for t in github_tools)
        )
        self.assertTrue(
            any(t["name"] == "github.attachment_upload" for t in github_tools)
        )

    def test_inject_to_server_mode(self):
        from server.server import inject_attachment_tools
        from unittest.mock import patch

        mock_config = {
            "type": "rest_api",
            "url_template": "https://example.com/{id}",
        }

        data = {"mode": "server", "name": "github", "tools": [{"name": "list_repos"}]}
        with (
            patch(
                "server.server.get_attachment_download_config", return_value=mock_config
            ),
            patch(
                "server.server.get_attachment_upload_config", return_value=mock_config
            ),
        ):
            inject_attachment_tools(data)
        self.assertTrue(
            any(t["name"] == "github.attachment_download" for t in data["tools"])
        )

    def test_inject_no_name_server(self):
        from server.server import inject_attachment_tools

        data = {"mode": "server", "tools": []}
        inject_attachment_tools(data)
        self.assertEqual(data["tools"], [])

    def test_inject_non_dict_data(self):
        from server.server import inject_attachment_tools

        data = ["not", "a", "dict"]
        inject_attachment_tools(data)

    def test_inject_unknown_mcptype_no_attachments(self):
        from server.server import inject_attachment_tools

        data = {"mode": "server", "name": "nonexistent", "tools": []}
        inject_attachment_tools(data)
        self.assertEqual(len(data["tools"]), 0)

    def test_preserve_existing_tools(self):
        from server.server import inject_attachment_tools
        from unittest.mock import patch

        mock_config = {
            "type": "rest_api",
            "url_template": "https://example.com/{id}",
        }

        data = {
            "mode": "server",
            "name": "github",
            "tools": [{"name": "list_repos"}, {"name": "create_gist"}],
        }
        with (
            patch(
                "server.server.get_attachment_download_config", return_value=mock_config
            ),
            patch(
                "server.server.get_attachment_upload_config", return_value=mock_config
            ),
        ):
            inject_attachment_tools(data)
        self.assertEqual(len(data["tools"]), 4)
        tool_names = [t["name"] for t in data["tools"]]
        self.assertIn("list_repos", tool_names)
        self.assertIn("create_gist", tool_names)


if __name__ == "__main__":
    unittest.main()

    @classmethod
    def setUpClass(cls):
        cls.mock_dir = tempfile.mkdtemp()
        cls.mock_mcporter_path = os.path.join(cls.mock_dir, "mcporter")
        with open(cls.mock_mcporter_path, "w") as f:
            tools = []
            for i in range(500):
                tools.append({"name": f"tool_{i}", "description": "x" * 100})
            tools_json = json.dumps(tools)
            github_response = (
                '{"mode": "server", "name": "github", "status": "ok", "durationMs": 50, "transport": "HTTP http://github:3000/mcp", "source": {"kind": "local"}, "tools": '
                + tools_json
                + "}"
            )
            list_response = '{"mode": "list", "counts": {"ok": 2, "auth": 0, "offline": 0, "http": 0, "error": 0}, "servers": [{"name": "github", "status": "ok", "durationMs": 50, "transport": "HTTP http://github:3000/mcp", "source": {"kind": "local"}, "tools": [{"name": "list_repos", "description": "List repositories", "inputSchema": {"type": "object"}}]}, {"name": "gitlab", "status": "ok", "durationMs": 40, "transport": "HTTP http://gitlab:3333/mcp", "source": {"kind": "local"}, "tools": []}]}'
            script = f"""#!/bin/bash
if [[ "$*" == *"json"* ]]; then
    if [[ "$*" == *"github"* ]]; then
        echo '{github_response}'
    else
        echo '{list_response}'
    fi
else
    echo "mcporter 0.9.0 — Listing 2 server(s)"
    echo "- github (12 tools, 0.1s)"
    echo "- gitlab (8 tools, 0.1s)"
    echo "✔ Listed 2 servers (2 healthy; 0 errors)."
fi
exit 0
"""
            f.write(script)
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

    def test_list_requires_auth(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)
        conn.close()

    def test_list_invalid_auth_key_format(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list", headers={"X-MCP-Auth-Key": "invalid_no_separator"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)
        conn.close()

    def test_list_text_output(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list", headers={"X-MCP-Auth-Key": "agent-key"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        content_type = resp.getheader("Content-Type", "")
        self.assertIn("text/plain", content_type)
        body = resp.read().decode()
        self.assertIn("mcporter", body)
        self.assertIn("github", body)
        conn.close()

    def test_list_json_output(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list?json=true", headers={"X-MCP-Auth-Key": "agent-key"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        content_type = resp.getheader("Content-Type", "")
        self.assertIn("application/json", content_type)
        body = json.loads(resp.read().decode())
        self.assertEqual(body.get("mode"), "list")
        self.assertIn("servers", body)
        self.assertEqual(len(body["servers"]), 2)
        conn.close()

    def test_list_with_name_json(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET", "/list/github?json=true", headers={"X-MCP-Auth-Key": "agent-key"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        self.assertEqual(body.get("mode"), "server")
        self.assertEqual(body.get("name"), "github")
        conn.close()

    def test_list_injects_attachment_tools(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list?json=true", headers={"X-MCP-Auth-Key": "agent-key"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        github_server = next(
            (s for s in body.get("servers", []) if s["name"] == "github"), None
        )
        self.assertIsNotNone(github_server)
        tool_names = [t["name"] for t in github_server.get("tools", [])]
        self.assertIn("github.attachment_download", tool_names)
        self.assertIn("github.attachment_upload", tool_names)

    def test_list_with_schema_flag(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?json=true&schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        self.assertEqual(body.get("name"), "github")
        conn.close()

    def test_list_large_output_above_64kb(self):
        large_json = json.dumps(
            {
                "mode": "server",
                "name": "github",
                "status": "ok",
                "tools": [
                    {"name": f"tool_{i}", "description": "x" * 1000} for i in range(500)
                ],
            }
        )
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET", "/list/github?json=true", headers={"X-MCP-Auth-Key": "agent-key"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        self.assertEqual(body.get("mode"), "server")
        self.assertEqual(body.get("name"), "github")
        self.assertGreater(len(body.get("tools", [])), 400)
