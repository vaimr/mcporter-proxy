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

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

SERVER_PORT = 9904
SERVER_PORT_DOWNLOAD = 9905
SERVER_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server", "server.py"
)
SERVER_MODULE_CMD = [sys.executable, "-m", "server.server"]
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


class TestTransformMetaTool(unittest.TestCase):
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

    def test_transform_meta_tool_atlassian_download(self):
        from server.server import transform_meta_tool

        internal_tool = {
            "name": "atlassian.attachment_download",
            "description": "Download file attachment from platform",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mcptype": {"const": "atlassian"},
                    "args": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "filename": {"type": "string"},
                        },
                    },
                },
            },
            "_proxy_endpoint": "POST /download-attachment",
            "_proxy_direction": "download",
        }

        result = transform_meta_tool(internal_tool)

        self.assertEqual(result["name"], "atlassian.attachment_download")
        self.assertEqual(
            result["description"], "Download file attachment from platform"
        )
        self.assertIn("inputSchema", result)
        self.assertIn("options", result)

        self.assertNotIn("_proxy_endpoint", result)
        self.assertNotIn("_proxy_direction", result)
        self.assertNotIn("mcptype", result["inputSchema"]["properties"])
        self.assertNotIn("args", result["inputSchema"]["properties"])

        props = result["inputSchema"]["properties"]
        self.assertIn("page_id", props)
        self.assertIn("filename", props)

        options = {o["property"]: o for o in result["options"]}
        self.assertIn("page_id", options)
        self.assertIn("filename", options)
        self.assertEqual(options["page_id"]["cliName"], "page-id")
        self.assertEqual(options["filename"]["cliName"], "filename")

    def test_transform_meta_tool_github_upload(self):
        from server.server import transform_meta_tool

        internal_tool = {
            "name": "github.attachment_upload",
            "description": "Upload file attachment to platform",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mcptype": {"const": "github"},
                    "args": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "upload_url": {"type": "string"},
                            "name": {"type": "string"},
                            "file": {"type": "string"},
                        },
                    },
                },
            },
            "_proxy_endpoint": "POST /upload-attachment",
            "_proxy_direction": "upload",
        }

        result = transform_meta_tool(internal_tool)

        self.assertEqual(result["name"], "github.attachment_upload")
        props = result["inputSchema"]["properties"]
        self.assertIn("owner", props)
        self.assertIn("repo", props)
        self.assertIn("upload_url", props)
        self.assertIn("name", props)
        self.assertIn("file", props)
        self.assertEqual(len(props), 5)

        options = result["options"]
        self.assertEqual(len(options), 5)

    def test_transform_meta_tool_passes_through_regular_tool(self):
        from server.server import transform_meta_tool

        regular_tool = {
            "name": "confluence_get_page_images",
            "description": "Get all images attached to a Confluence page",
            "inputSchema": {
                "type": "object",
                "properties": {"content_id": {"type": "string"}},
            },
        }

        result = transform_meta_tool(regular_tool)

        self.assertEqual(result["name"], "confluence_get_page_images")
        self.assertIn("content_id", result["inputSchema"]["properties"])
        self.assertIn("options", result)
        self.assertEqual(len(result["options"]), 0)

    def test_transform_meta_tool_camel_to_kebab(self):
        from server.server import _camel_to_kebab

        self.assertEqual(_camel_to_kebab("pageId"), "page-id")
        self.assertEqual(_camel_to_kebab("content_id"), "content-id")
        self.assertEqual(_camel_to_kebab("uploadURL"), "upload-u-r-l")
        self.assertEqual(_camel_to_kebab("someID"), "some-i-d")

    def test_transform_meta_tool_without_schema(self):
        from server.server import transform_meta_tool

        internal_tool = {
            "name": "atlassian.attachment_download",
            "description": "Download file attachment from platform",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mcptype": {"const": "atlassian"},
                    "args": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "filename": {"type": "string"},
                        },
                    },
                },
            },
            "_proxy_endpoint": "POST /download-attachment",
            "_proxy_direction": "download",
        }

        result = transform_meta_tool(internal_tool, include_schema=False)

        self.assertEqual(result["name"], "atlassian.attachment_download")
        self.assertEqual(
            result["description"], "Download file attachment from platform"
        )
        self.assertNotIn("inputSchema", result)
        self.assertNotIn("options", result)

    def test_transform_meta_tool_with_schema(self):
        from server.server import transform_meta_tool

        internal_tool = {
            "name": "atlassian.attachment_download",
            "description": "Download file attachment from platform",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mcptype": {"const": "atlassian"},
                    "args": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "filename": {"type": "string"},
                        },
                    },
                },
            },
            "_proxy_endpoint": "POST /download-attachment",
            "_proxy_direction": "download",
        }

        result = transform_meta_tool(internal_tool, include_schema=True)

        self.assertEqual(result["name"], "atlassian.attachment_download")
        self.assertEqual(
            result["description"], "Download file attachment from platform"
        )
        self.assertIn("inputSchema", result)
        self.assertIn("options", result)
        self.assertIn("page_id", result["inputSchema"]["properties"])

    def test_transform_meta_tool_regular_tool_always_has_options(self):
        from server.server import transform_meta_tool

        regular_tool = {
            "name": "some_tool",
            "description": "A regular tool",
            "inputSchema": {"type": "object", "properties": {}},
        }

        result = transform_meta_tool(regular_tool, include_schema=False)

        self.assertEqual(result["name"], "some_tool")
        self.assertIn("options", result)
        self.assertEqual(len(result["options"]), 0)


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


class TestDownloadAttachmentEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
            "atlassian": {
                "env": ["ATLASSIAN_API_TOKEN"],
                "attachment_download": {
                    "type": "mcp_tool",
                    "tool_name": "test_tool_direct",
                    "tool_args_mapping": {"attachment_id": "{attachment_id}"},
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
        cls.temp_env_map = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(test_config, cls.temp_env_map)
        cls.temp_env_map.close()

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
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT_DOWNLOAD)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "test_tool_*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.env["MCP_ENV_MAP_PATH"] = cls.temp_env_map.name
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
        cls.server_process.wait()
        os.unlink(cls.mock_mcporter_path)
        os.unlink(cls.mock_gloves_path)
        os.rmdir(cls.mock_dir)
        os.unlink(cls.temp_env_map.name)
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
        conn.close()


class TestMultipartParsing(unittest.TestCase):
    def test_extract_file_from_multipart_basic(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MCPorterProxyHandler.__new__(MCPorterProxyHandler)

        body = (
            b"--simpleboundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            b"Hello World"
            b"\r\n--simpleboundary--\r\n"
        )
        boundary = b"simpleboundary"

        result = mock_handler._extract_file_from_multipart(body, boundary)

        self.assertIsNotNone(result)
        stream, filename = result
        self.assertEqual(filename, "test.txt")
        content = stream.read()
        self.assertEqual(content, b"Hello World")

    def test_extract_file_from_multipart_with_large_content(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MCPorterProxyHandler.__new__(MCPorterProxyHandler)

        large_content = b"x" * (2 * 1024 * 1024)
        body = (
            b"--simpleboundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="large.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + large_content
            + b"\r\n--simpleboundary--\r\n"
        )
        boundary = b"simpleboundary"

        result = mock_handler._extract_file_from_multipart(body, boundary)

        self.assertIsNotNone(result)
        stream, filename = result
        self.assertEqual(filename, "large.bin")
        content = stream.read()
        self.assertEqual(len(content), len(large_content))
        self.assertEqual(content, large_content)

    def test_extract_file_from_multipart_cyrillic_filename(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MCPorterProxyHandler.__new__(MCPorterProxyHandler)

        body = (
            b"--simpleboundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="\320\224\320\276\320\272\321\203\320\274\320\265\320\275\321\202.webm"\r\n'
            b"Content-Type: video/webm\r\n\r\n"
            b"video_content_here"
            b"\r\n--simpleboundary--\r\n"
        )
        boundary = b"simpleboundary"

        result = mock_handler._extract_file_from_multipart(body, boundary)

        self.assertIsNotNone(result)
        stream, filename = result
        self.assertEqual(
            filename,
            "\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442.webm",
        )

    def test_extract_file_from_multipart_no_file(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MCPorterProxyHandler.__new__(MCPorterProxyHandler)

        body = (
            b"--simpleboundary\r\n"
            b'Content-Disposition: form-data; name="notfile"\r\n\r\n'
            b"Hello World"
            b"\r\n--simpleboundary--\r\n"
        )
        boundary = b"simpleboundary"

        result = mock_handler._extract_file_from_multipart(body, boundary)

        self.assertIsNone(result)


class TestUploadChunkedBehavior(unittest.TestCase):
    def test_upload_via_rest_sends_body_with_content_length(self):
        from unittest.mock import MagicMock, patch, call
        import io
        from server.server import upload_via_rest

        config = {
            "method": "POST",
            "url_template": "https://example.com/upload",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {}
        large_content = b"x" * (300 * 1024)
        file_stream = io.BytesIO(large_content)
        filename = "large.bin"
        content_type = "application/octet-stream"
        token = "test_token"
        extra_secrets = {}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_conn.getresponse.return_value = mock_response

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

            send_calls = mock_conn.send.call_args_list
            self.assertEqual(len(send_calls), 1, "Should send body in single call")

            putheader_calls = {
                c[0][0].lower(): c[0][1] for c in mock_conn.putheader.call_args_list
            }
            self.assertIn("content-length", putheader_calls)
            self.assertNotIn("transfer-encoding", putheader_calls)


class TestMultipartEncoder(unittest.TestCase):
    def test_multipart_encoder_produces_valid_content_type(self):
        from requests_toolbelt import MultipartEncoder
        import io

        encoder = MultipartEncoder(
            fields={"file": ("test.txt", io.BytesIO(b"Hello"), "text/plain")}
        )
        self.assertIn("multipart/form-data", encoder.content_type)
        self.assertIn("boundary=", encoder.content_type)

    def test_multipart_encoder_iter_chunks(self):
        from requests_toolbelt import MultipartEncoder
        import io

        large_data = b"x" * (300 * 1024)
        encoder = MultipartEncoder(
            fields={
                "file": (
                    "large.bin",
                    io.BytesIO(large_data),
                    "application/octet-stream",
                )
            }
        )

        chunks = []
        while True:
            chunk = encoder.read(262144)
            if not chunk:
                break
            chunks.append(chunk)
        self.assertGreater(len(chunks), 1, "Should produce multiple chunks")

        total_bytes = b"".join(chunks)
        self.assertIn(b"large.bin", total_bytes)
        self.assertIn(b"Content-Disposition", total_bytes)


class TestUploadThresholdRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_config = {
            "atlassian": {
                "env": ["ATLASSIAN_API_TOKEN"],
                "attachment_upload": {
                    "type": "rest_api",
                    "method": "POST",
                    "url_template": "https://httpbin.org/post",
                    "headers": {
                        "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                        "X-Atlassian-Token": "no-check",
                    },
                },
            },
        }
        cls.temp_env_map = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(test_config, cls.temp_env_map)
        cls.temp_env_map.close()

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
            f.write("""#!/bin/bash
if [[ "$*" == *"get"* ]]; then
    echo "test_token_123"
fi
exit 0
""")
        os.chmod(cls.mock_gloves_path, 0o755)

        cls.env = os.environ.copy()
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(9907)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.env["MCP_ENV_MAP_PATH"] = cls.temp_env_map.name
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
        cls.server_process.wait()
        os.unlink(cls.mock_mcporter_path)
        os.unlink(cls.mock_gloves_path)
        os.rmdir(cls.mock_dir)
        os.unlink(cls.temp_env_map.name)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_small_upload_uses_bytes_parser(self):
        conn = HTTPConnection("localhost", 9907)
        body = b'--simpleboundary\r\nContent-Disposition: form-data; name="file"; filename="small.txt"\r\nContent-Type: text/plain\r\n\r\nSmall content\r\n--simpleboundary--\r\n'
        conn.request(
            "POST",
            "/upload-attachment",
            body=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=simpleboundary",
                "Content-Length": str(len(body)),
                "X-MCP-Auth-Key": "agent-key",
                "X-Target-Platform": "atlassian",
                "X-Target-Args": json.dumps(
                    {"page_id": "217090082", "name": "small.txt"}
                ),
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()

    def test_large_upload_uses_streaming_parser(self):
        conn = HTTPConnection("localhost", 9907)
        large_content = b"x" * (2 * 1024 * 1024)
        body = (
            b'--simpleboundary\r\nContent-Disposition: form-data; name="file"; filename="large.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
            + large_content
            + b"\r\n--simpleboundary--\r\n"
        )
        conn.request(
            "POST",
            "/upload-attachment",
            body=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=simpleboundary",
                "Content-Length": str(len(body)),
                "X-MCP-Auth-Key": "agent-key",
                "X-Target-Platform": "atlassian",
                "X-Target-Args": json.dumps(
                    {"page_id": "217090082", "name": "large.bin"}
                ),
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
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

    def test_download_atlassian_attachment(self):
        conn = HTTPConnection("localhost", SERVER_PORT_DOWNLOAD, timeout=3)
        body = json.dumps(
            {"mcptype": "atlassian", "args": {"attachment_id": "test-attachment-123"}}
        )
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
        print(
            f"DEBUG: Response status {resp.status}, body: {response_body.decode('utf-8', errors='replace')[:500]}"
        )
        self.assertEqual(
            resp.status,
            200,
            f"Expected 200, got {resp.status}: {response_body.decode('utf-8', errors='replace')[:200]}",
        )
        conn.close()


class TestDownloadAttachmentMocked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        simple_config = {"github": ["GITHUB_TOKEN"], "gitlab": ["GITLAB_TOKEN"]}
        cls.temp_env_map = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(simple_config, cls.temp_env_map)
        cls.temp_env_map.close()

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
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "test_tool_*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.env["MCP_ENV_MAP_PATH"] = cls.temp_env_map.name
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
        cls.server_process.wait()
        os.unlink(cls.mock_mcporter_path)
        os.unlink(cls.mock_gloves_path)
        os.rmdir(cls.mock_dir)
        os.unlink(cls.temp_env_map.name)
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
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT)
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
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


class TestBuildUploadHeaders(unittest.TestCase):
    """Test build_upload_headers function with ${ENV_VAR} syntax for Atlassian."""

    def test_build_upload_headers_with_env_var_syntax(self):
        from server.server import build_upload_headers

        headers_template = {
            "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
            "X-Atlassian-Token": "no-check",
        }
        token = "primary_token"
        extra_secrets = {"ATLASSIAN_TOKEN": "resolved_token_from_gloves"}
        args = {}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer resolved_token_from_gloves")
        self.assertEqual(headers["X-Atlassian-Token"], "no-check")

    def test_build_upload_headers_preserves_bearer_prefix(self):
        from server.server import build_upload_headers

        headers_template = {"Authorization": "Bearer ${ATLASSIAN_TOKEN}"}
        token = "secret"
        extra_secrets = {"ATLASSIAN_TOKEN": "my_api_token"}
        args = {}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer my_api_token")
        self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_build_upload_headers_with_multiple_env_vars(self):
        from server.server import build_upload_headers

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

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer token1")
        self.assertEqual(headers["X-Custom"], "Token1=extra1, Token2=extra2")

    def test_build_upload_headers_env_var_fallback_to_os_environ(self):
        import os
        from server.server import build_upload_headers

        os.environ["TEST_OS_VAR"] = "os_value"
        headers_template = {"X-Test": "Value=${TEST_OS_VAR}"}
        token = "token"
        extra_secrets = {}
        args = {}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["X-Test"], "Value=os_value")
        del os.environ["TEST_OS_VAR"]

    def test_build_upload_headers_with_args_substitution(self):
        from server.server import build_upload_headers

        headers_template = {"X-Filename": "{filename}"}
        token = "token"
        extra_secrets = {}
        args = {"filename": "report.pdf"}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["X-Filename"], "report.pdf")

    def test_build_upload_headers_with_page_id(self):
        from server.server import build_upload_headers

        headers_template = {
            "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
            "X-Page-Id": "{page_id}",
        }
        token = "secret"
        extra_secrets = {"ATLASSIAN_TOKEN": "atlassian_token_123"}
        args = {"page_id": "217090082", "name": "test.md"}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(headers["Authorization"], "Bearer atlassian_token_123")
        self.assertEqual(headers["X-Page-Id"], "217090082")

    def test_build_upload_headers_basic_auth(self):
        from server.server import build_upload_headers

        headers_template = {"Authorization": "Basic {basic_auth}"}
        token = "api_token"
        extra_secrets = {"email": "user@example.com"}
        args = {}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        import base64

        expected = base64.b64encode(b"user@example.com:api_token").decode()
        self.assertEqual(headers["Authorization"], f"Basic {expected}")

    def test_build_upload_headers_with_token_placeholder(self):
        """Test that {token} placeholder is substituted directly.

        This reproduces the bug where mcptype 'atlassian' uses:
          "Authorization": "Bearer {token}"
        but {token} was NOT being substituted because build_upload_headers
        only handled ${ENV_VAR} syntax, not {token}.
        """
        from server.server import build_upload_headers

        headers_template = {
            "Authorization": "Bearer {token}",
            "X-Atlassian-Token": "no-check",
        }
        token = "MySecretToken123"
        extra_secrets = {}
        args = {"page_id": "217090082", "name": "test.md"}

        headers = build_upload_headers(headers_template, token, extra_secrets, args)
        self.assertEqual(
            headers["Authorization"],
            "Bearer MySecretToken123",
            "{token} should be replaced with the token value",
        )
        self.assertEqual(headers["X-Atlassian-Token"], "no-check")


class TestUploadViaRestHTTPHeaders(unittest.TestCase):
    """Test that upload_via_rest sends correct HTTP headers."""

    def test_upload_via_rest_sends_bearer_prefix(self):
        import io
        from unittest.mock import MagicMock, patch
        from server.server import upload_via_rest

        config = {
            "method": "POST",
            "url_template": "https://example.com/rest/api/content/{page_id}/child/attachment",
            "headers": {
                "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                "X-Atlassian-Token": "no-check",
            },
        }
        args = {"page_id": "12345", "name": "test.md"}
        file_stream = io.BytesIO(b"test content")
        filename = "test.md"
        content_type = "application/octet-stream"
        token = "primary_token"
        extra_secrets = {"ATLASSIAN_TOKEN": "MySecretToken123"}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.reason = "OK"
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_response.getheader.return_value = "application/json"
        mock_conn.getresponse.return_value = mock_response

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

            putheader_calls = {
                c[0][0].lower(): c[0][1] for c in mock_conn.putheader.call_args_list
            }
            self.assertIn("authorization", putheader_calls)
            auth_value = putheader_calls["authorization"]
            self.assertTrue(
                auth_value.startswith("Bearer "),
                f"Authorization should start with 'Bearer ', got: {auth_value}",
            )
            self.assertEqual(auth_value, "Bearer MySecretToken123")

    def test_upload_via_rest_handles_connection_reset(self):
        import io
        from unittest.mock import MagicMock, patch
        from server.server import upload_via_rest, MCPToolError

        config = {
            "method": "POST",
            "url_template": "https://example.com/rest/api/content/{page_id}/child/attachment",
            "headers": {
                "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                "X-Atlassian-Token": "no-check",
            },
        }
        args = {"page_id": "12345", "name": "test.md"}
        file_stream = io.BytesIO(b"test content")
        filename = "test.md"
        content_type = "application/octet-stream"
        token = "primary_token"
        extra_secrets = {"ATLASSIAN_TOKEN": "MySecretToken123"}

        mock_conn = MagicMock()
        mock_conn.send.return_value = None
        mock_conn.send.side_effect = ConnectionResetError(
            "[Errno 104] Connection reset by peer"
        )

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            with self.assertRaises(MCPToolError) as context:
                upload_via_rest(
                    config,
                    args,
                    file_stream,
                    filename,
                    content_type,
                    token,
                    extra_secrets,
                )
            self.assertIn("Connection reset", str(context.exception))


class TestUploadHeadersContentLength(unittest.TestCase):
    """Test that upload_via_rest sends correct Content-Length header."""

    def test_upload_via_rest_sends_content_length_not_chunked(self):
        import io
        from unittest.mock import MagicMock, patch
        from server.server import upload_via_rest

        config = {
            "method": "POST",
            "url_template": "https://example.com/rest/api/content/{page_id}/child/attachment",
            "headers": {
                "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                "X-Atlassian-Token": "no-check",
            },
        }
        args = {"page_id": "12345", "name": "test.md"}
        file_stream = io.BytesIO(b"test content")
        filename = "test.md"
        content_type = "application/octet-stream"
        token = "primary_token"
        extra_secrets = {"ATLASSIAN_TOKEN": "MySecretToken123"}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.reason = "OK"
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_conn.getresponse.return_value = mock_response

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

            putheader_calls = {
                c[0][0].lower(): c[0][1] for c in mock_conn.putheader.call_args_list
            }
            self.assertIn(
                "content-length",
                putheader_calls,
                f"Content-Length should be sent. Got: {putheader_calls}",
            )
            self.assertNotIn(
                "transfer-encoding",
                putheader_calls,
                f"Transfer-Encoding should not be sent. Got: {putheader_calls}",
            )


class TestStreamingUpload(unittest.TestCase):
    """Test streaming upload - data flows from client to upstream without full buffering."""

    def test_streaming_upload_does_not_buffer_entire_file(self):
        import io
        from unittest.mock import MagicMock, patch, call
        from server.server import upload_via_rest_streaming

        config = {
            "method": "POST",
            "url_template": "https://example.com/upload",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {}
        large_content = b"x" * (1024 * 1024)
        file_stream = io.BytesIO(large_content)
        filename = "large.bin"
        content_type = "application/octet-stream"
        token = "test_token"
        extra_secrets = {}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_conn.getresponse.return_value = mock_response

        sent_chunks = []

        def capture_send(data):
            sent_chunks.append(len(data))

        mock_conn.send.side_effect = capture_send

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest_streaming(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

        total_sent = sum(sent_chunks)
        self.assertGreater(
            total_sent,
            len(large_content),
            "Should send at least the file content (plus chunked encoding overhead)",
        )
        self.assertGreater(
            len(sent_chunks), 1, "Should send in multiple chunks for streaming"
        )

    def test_streaming_upload_uses_chunked_transfer_encoding(self):
        import io
        from unittest.mock import MagicMock, patch
        from server.server import upload_via_rest_streaming

        config = {
            "method": "POST",
            "url_template": "https://example.com/upload",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {}
        file_stream = io.BytesIO(b"test content")
        filename = "test.bin"
        content_type = "application/octet-stream"
        token = "test_token"
        extra_secrets = {}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_conn.getresponse.return_value = mock_response

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest_streaming(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

        putheader_calls = {
            c[0][0].lower(): c[0][1] for c in mock_conn.putheader.call_args_list
        }
        self.assertIn("transfer-encoding", putheader_calls)
        self.assertNotIn("content-length", putheader_calls)
        self.assertEqual(putheader_calls["transfer-encoding"], "chunked")

    def test_streaming_upload_sends_correct_chunked_format(self):
        import io
        from unittest.mock import MagicMock, patch
        from server.server import upload_via_rest_streaming

        config = {
            "method": "POST",
            "url_template": "https://example.com/upload",
            "headers": {"Authorization": "Bearer {token}"},
        }
        args = {}
        file_stream = io.BytesIO(b"ABC")
        filename = "test.txt"
        content_type = "text/plain"
        token = "test_token"
        extra_secrets = {}

        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.side_effect = [b'{"success": true}', b""]
        mock_conn.getresponse.return_value = mock_response

        sent_data = b""

        def capture_send(data):
            nonlocal sent_data
            sent_data += data

        mock_conn.send.side_effect = capture_send

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            upload_via_rest_streaming(
                config, args, file_stream, filename, content_type, token, extra_secrets
            )

        self.assertIn(
            b"3\r\nABC\r\n",
            sent_data,
            "Should have chunked format: length\\r\\ndata\\r\\n",
        )
        self.assertTrue(
            sent_data.endswith(b"0\r\n\r\n"),
            "Should end with final chunk 0\r\n\r\n",
        )


class TestChunkedFileReference(unittest.TestCase):
    def test_chunked_file_reference_read_returns_correct_data(self):
        from server.server import ChunkedFileReference

        chunks = [(b"Hello ", 6), (b"World!", 6)]
        ref = ChunkedFileReference(chunks, total_size=12)
        data = ref.read()
        self.assertEqual(data, b"Hello World!")
        self.assertEqual(ref.read(), b"")

    def test_chunked_file_reference_read_with_size(self):
        from server.server import ChunkedFileReference

        chunks = [(b"Hello World!", 12)]
        ref = ChunkedFileReference(chunks, total_size=12)
        part = ref.read(5)
        self.assertEqual(part, b"Hello")
        part = ref.read(10)
        self.assertEqual(part, b" World!")

    def test_chunked_file_reference_iteration(self):
        from server.server import ChunkedFileReference

        chunks = [(b"AB", 2), (b"CD", 2), (b"EF", 2)]
        ref = ChunkedFileReference(chunks, total_size=6)
        result = b"".join(ref.read_chunk())
        self.assertEqual(result, b"ABCDEF")

    def test_chunked_file_reference_iteration(self):
        from server.server import ChunkedFileReference

        chunks = [(b"AB", 2), (b"CD", 2), (b"EF", 2)]
        ref = ChunkedFileReference(chunks, total_size=6)
        result = b"".join(ref.read_chunk())
        self.assertEqual(result, b"ABCDEF")

    def test_streaming_parser_no_full_copy(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MCPorterProxyHandler.__new__(MCPorterProxyHandler)

        file_content = b"X" * (512 * 1024)
        body = (
            b"--simpleboundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + file_content
            + b"\r\n--simpleboundary--\r\n"
        )
        boundary = b"simpleboundary"

        result = mock_handler._extract_file_from_multipart(body, boundary)

        self.assertIsNotNone(result)
        stream, filename = result

        read_data = stream.read()
        self.assertEqual(read_data, file_content)
        self.assertEqual(len(read_data), len(file_content))


class TestExecuteUploadMcptype(unittest.TestCase):
    """Test that _execute_upload correctly uses mcptype for token resolution."""

    def test_execute_upload_mcptype_is_not_in_args(self):
        args = {"page_id": "217090082", "name": "test.md"}
        mcptype_from_args = args.get("mcptype", "")
        print(f"mcptype from args: {repr(mcptype_from_args)}")
        self.assertEqual(
            mcptype_from_args,
            "",
            "args does NOT contain 'mcptype' key - it's only in the headers, not in X-Target-Args",
        )

    def test_execute_upload_resolves_token_with_correct_mcptype(self):
        from unittest.mock import MagicMock, patch
        import io

        with (
            patch("server.auth.resolve_token") as mock_resolve_token,
            patch("server.auth.resolve_extra_secrets") as mock_resolve_extra_secrets,
            patch("server.server.get_attachment_upload_config") as mock_get_config,
            patch("server.server.get_env_vars_for_mcptype") as mock_get_env_vars,
            patch("server.handlers.upload_via_rest") as mock_upload,
        ):
            mock_resolve_token.return_value = "resolved_secret_token"
            mock_resolve_extra_secrets.return_value = {}
            mock_get_env_vars.return_value = ["CONFLUENCE_API_TOKEN"]
            mock_upload.return_value = {"success": True}

            mock_get_config.return_value = {
                "type": "rest_api",
                "method": "POST",
                "url_template": "https://example.com/rest/api/content/{page_id}/child/attachment",
                "headers": {
                    "Authorization": "Bearer ${CONFLUENCE_API_TOKEN}",
                    "X-Atlassian-Token": "no-check",
                },
            }

            from server.server import MCPorterProxyHandler

            handler = MagicMock(spec=MCPorterProxyHandler)
            handler._execute_upload = MCPorterProxyHandler._execute_upload.__get__(
                handler, MCPorterProxyHandler
            )

            config = mock_get_config.return_value
            args = {"page_id": "217090082", "name": "test.md"}
            file_stream = io.BytesIO(b"test file content")
            agent_id = "test-agent"
            agent_key = "test-key"

            handler._execute_upload(
                "rest_api", config, args, file_stream, agent_id, agent_key, "confluence"
            )

            resolve_token_call = mock_resolve_token.call_args
            mcptype_passed_to_resolve = resolve_token_call[0][0]
            print(f"mcptype passed to resolve_token: {repr(mcptype_passed_to_resolve)}")

            self.assertNotEqual(
                mcptype_passed_to_resolve,
                "",
                "mcptype should not be empty - it should be 'confluence' or similar, not args.get('mcptype', '')",
            )
            self.assertEqual(
                mcptype_passed_to_resolve,
                "confluence",
                "mcptype should be 'confluence' as passed explicitly",
            )

            upload_call_args = mock_upload.call_args
            extra_secrets_passed = upload_call_args[0][6]
            print(f"extra_secrets passed to upload_via_rest: {extra_secrets_passed}")
            self.assertIn(
                "CONFLUENCE_API_TOKEN",
                extra_secrets_passed,
                "primary token should be added to extra_secrets as CONFLUENCE_API_TOKEN",
            )
            self.assertEqual(
                extra_secrets_passed["CONFLUENCE_API_TOKEN"],
                "resolved_secret_token",
                "primary token should be stored under CONFLUENCE_API_TOKEN key",
            )

    def test_handle_upload_attachment_passes_mcptype_to_execute(self):
        from unittest.mock import MagicMock, patch, PropertyMock

        with (
            patch("server.auth.resolve_token") as mock_resolve_token,
            patch("server.auth.resolve_extra_secrets") as mock_resolve_extra_secrets,
            patch("server.server.get_attachment_upload_config") as mock_get_config,
            patch("server.handlers.upload_via_rest") as mock_upload,
        ):
            mock_resolve_token.return_value = "resolved_secret_token"
            mock_resolve_extra_secrets.return_value = {
                "ATLASSIAN_TOKEN": "gloves_token_123"
            }
            mock_upload.return_value = {"success": True}

            mock_get_config.return_value = {
                "type": "rest_api",
                "method": "POST",
                "url_template": "https://example.com/rest/api/content/{page_id}/child/attachment",
                "headers": {
                    "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                    "X-Atlassian-Token": "no-check",
                },
            }

            from server.server import MCPorterProxyHandler

            handler = MagicMock(spec=MCPorterProxyHandler)
            handler._execute_upload = MCPorterProxyHandler._execute_upload.__get__(
                handler, MCPorterProxyHandler
            )

            args = {"page_id": "217090082", "name": "test.md"}
            file_stream = io.BytesIO(b"test file content")
            agent_id = "test-agent"
            agent_key = "test-key"

            handler._execute_upload(
                "rest_api",
                mock_get_config.return_value,
                args,
                file_stream,
                agent_id,
                agent_key,
                "confluence",
            )

            resolve_token_call = mock_resolve_token.call_args
            mcptype_passed = resolve_token_call[0][0] if resolve_token_call else None
            print(f"DEBUG: mcptype passed to resolve_token: {repr(mcptype_passed)}")
            self.assertIsNotNone(
                mcptype_passed, "resolve_token should have been called"
            )
            self.assertNotEqual(
                mcptype_passed, "", "mcptype should not be empty string"
            )
            self.assertEqual(
                mcptype_passed,
                "confluence",
                "mcptype should be explicitly passed as 'confluence'",
            )


class TestParseUploadTargetArgs(unittest.TestCase):
    def test_parse_upload_target_args_with_cyrillic_and_spaces(self):
        import base64
        from server.server import MCPorterProxyHandler

        args_json = '{"page_id":217090082,"name":"Запись встречи 26.06.2025 14-08-21 - запись.webm"}'
        args_b64 = base64.b64encode(args_json.encode()).decode()

        mock_handler = MagicMock(spec=MCPorterProxyHandler)
        mock_handler.headers = {
            "X-Target-Platform": "atlassian",
            "X-Target-Args": args_b64,
        }

        result = MCPorterProxyHandler._parse_upload_target_args(mock_handler)
        mcptype, args = result

        self.assertEqual(mcptype, "atlassian")
        self.assertEqual(args["page_id"], 217090082)
        self.assertEqual(
            args["name"],
            "Запись встречи 26.06.2025 14-08-21 - запись.webm",
        )

    def test_parse_upload_target_args_plain_json_still_works(self):
        from server.server import MCPorterProxyHandler

        mock_handler = MagicMock(spec=MCPorterProxyHandler)
        mock_handler.headers = {
            "X-Target-Platform": "github",
            "X-Target-Args": '{"owner":"octocat","repo":"hello-world"}',
        }

        mcptype, args = MCPorterProxyHandler._parse_upload_target_args(mock_handler)

        self.assertEqual(mcptype, "github")
        self.assertEqual(args["owner"], "octocat")
        self.assertEqual(args["repo"], "hello-world")


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


class TestListEndpoint(unittest.TestCase):
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
            atlassian_tools = [
                {
                    "name": "atlassian.confluence_delete_attachment",
                    "description": "Delete attachment",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "atlassian.confluence_get_page_images",
                    "description": "Get page images",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "atlassian.jira_search",
                    "description": "Search Jira",
                    "inputSchema": {"type": "object"},
                },
            ]
            atlassian_tools_json = json.dumps(atlassian_tools)
            atlassian_response = (
                '{"mode": "server", "name": "atlassian", "status": "ok", "durationMs": 50, "transport": "HTTP http://atlassian:3333/mcp", "source": {"kind": "local"}, "tools": '
                + atlassian_tools_json
                + "}"
            )
            list_response = (
                '{"mode": "list", "counts": {"ok": 3, "auth": 0, "offline": 0, "http": 0, "error": 0}, "servers": ['
                + '{"name": "github", "status": "ok", "durationMs": 50, "transport": "HTTP http://github:3000/mcp", "source": {"kind": "local"}, "tools": [{"name": "list_repos", "description": "List repositories", "inputSchema": {"type": "object"}}]},'
                + '{"name": "gitlab", "status": "ok", "durationMs": 40, "transport": "HTTP http://gitlab:3333/mcp", "source": {"kind": "local"}, "tools": []},'
                + '{"name": "atlassian", "status": "ok", "durationMs": 40, "transport": "HTTP http://atlassian:3333/mcp", "source": {"kind": "local"}, "tools": '
                + atlassian_tools_json
                + "}]}"
            )
            script = f"""#!/bin/bash
            if [[ "$*" == *"json"* ]]; then
                if [[ "$*" == *"github"* ]]; then
                    echo '{github_response}'
                elif [[ "$*" == *"atlassian"* ]]; then
                    echo '{atlassian_response}'
                else
                    echo '{list_response}'
                fi
            elif [[ "$*" == *"schema"* ]]; then
                if [[ "$*" == *"github"* ]]; then
                    echo 'github'
                    echo ''
                    echo '  /**'
                    echo '   * List repositories'
                    echo '   */'
                    echo '  function list_repos(visibility: string);'
                elif [[ "$*" == *"atlassian"* ]]; then
                    echo 'atlassian'
                    echo ''
                    echo '  /**'
                    echo '   * Delete attachment'
                    echo '   */'
                    echo '  function confluence_delete_attachment(page_id: string);'
                else
                    echo 'mcporter 0.9.0 — Listing 3 server(s)'
                    echo '- github (12 tools, 0.1s)'
                    echo '- gitlab (8 tools, 0.1s)'
                    echo '- atlassian (15 tools, 0.1s)'
                fi
            else
                echo "mcporter 0.9.0 — Listing 3 server(s)"
                echo "- github (12 tools, 0.1s)"
                echo "- gitlab (8 tools, 0.1s)"
                echo "- atlassian (15 tools, 0.1s)"
                echo "✔ Listed 3 servers (3 healthy; 0 errors)."
            fi
            exit 0
            """
            f.write(script)
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
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(SERVER_PORT)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "test_tool_*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "WARNING"
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
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
        self.assertGreater(len(body["servers"]), 0)
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

    def test_attachment_tools_have_name_and_description_only_without_schema_flag(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request("GET", "/list?json=true", headers={"X-MCP-Auth-Key": "agent-key"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        github_server = next(
            (s for s in body.get("servers", []) if s["name"] == "github"), None
        )
        self.assertIsNotNone(github_server)

        download_tool = next(
            (
                t
                for t in github_server.get("tools", [])
                if t["name"] == "github.attachment_download"
            ),
            None,
        )
        self.assertIsNotNone(download_tool)

        self.assertIn("name", download_tool)
        self.assertIn("description", download_tool)
        self.assertNotIn("inputSchema", download_tool)
        self.assertNotIn("options", download_tool)
        self.assertNotIn("_proxy_endpoint", download_tool)
        self.assertNotIn("_proxy_direction", download_tool)

    def test_list_with_schema_flag(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?schema=true",
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

    def test_list_json_returns_meta_tools_with_name_and_description_only(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET", "/list/github?json=true", headers={"X-MCP-Auth-Key": "agent-key"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        tools = body.get("tools", [])
        meta_tool = next(
            (t for t in tools if t.get("name") == "github.attachment_download"), None
        )
        self.assertIsNotNone(meta_tool)
        self.assertIn("name", meta_tool)
        self.assertIn("description", meta_tool)
        self.assertNotIn("inputSchema", meta_tool)
        self.assertNotIn("options", meta_tool)

    def test_list_json_with_schema_returns_meta_tools_with_full_schema(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?json=true&schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        tools = body.get("tools", [])
        meta_tool = next(
            (t for t in tools if t.get("name") == "github.attachment_download"), None
        )
        self.assertIsNotNone(meta_tool)
        self.assertIn("name", meta_tool)
        self.assertIn("description", meta_tool)
        self.assertIn("inputSchema", meta_tool)
        self.assertIn("options", meta_tool)
        self.assertIn("owner", meta_tool["inputSchema"]["properties"])

    def test_list_json_returns_upload_meta_tool_name_description_only(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET", "/list/github?json=true", headers={"X-MCP-Auth-Key": "agent-key"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        tools = body.get("tools", [])
        meta_tool = next(
            (t for t in tools if t.get("name") == "github.attachment_upload"), None
        )
        self.assertIsNotNone(meta_tool)
        self.assertEqual(meta_tool["name"], "github.attachment_upload")
        self.assertIn("description", meta_tool)
        self.assertNotIn("inputSchema", meta_tool)
        self.assertNotIn("options", meta_tool)

    def test_list_json_with_schema_returns_upload_meta_tool_with_options(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?json=true&schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read().decode())
        tools = body.get("tools", [])
        meta_tool = next(
            (t for t in tools if t.get("name") == "github.attachment_upload"), None
        )
        self.assertIsNotNone(meta_tool)
        self.assertIn("options", meta_tool)
        option_names = [o["property"] for o in meta_tool["options"]]
        self.assertIn("owner", option_names)
        self.assertIn("repo", option_names)
        self.assertIn("name", option_names)

    def test_list_schema_flag_only_returns_text_with_meta_tools_appended(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        content_type = resp.getheader("Content-Type", "")
        self.assertIn("text/plain", content_type)
        body = resp.read().decode()
        self.assertIn("github", body)
        self.assertIn("list_repos", body)
        self.assertIn("github.attachment_download", body)
        self.assertIn("github.attachment_upload", body)
        self.assertIn("owner", body)
        self.assertIn("repo", body)

    def test_list_json_and_schema_together_returns_error(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/github?json=true&schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        body = resp.read().decode()
        self.assertIn("json", body.lower())
        self.assertIn("schema", body.lower())

    def test_list_atlassian_with_schema_flag_appends_attachment_tools(self):
        conn = HTTPConnection("localhost", SERVER_PORT)
        conn.request(
            "GET",
            "/list/atlassian?schema=true",
            headers={"X-MCP-Auth-Key": "agent-key"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        content_type = resp.getheader("Content-Type", "")
        self.assertIn("text/plain", content_type)
        body = resp.read().decode()
        self.assertIn("atlassian", body)
        self.assertIn("confluence_delete_attachment", body)


class TestUploadAttachment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_config = {
            "atlassian": {
                "env": ["ATLASSIAN_API_TOKEN"],
                "attachment_upload": {
                    "type": "rest_api",
                    "method": "POST",
                    "url_template": "https://httpbin.org/post",
                    "headers": {
                        "Authorization": "Bearer ${ATLASSIAN_TOKEN}",
                        "X-Atlassian-Token": "no-check",
                    },
                },
            },
        }
        cls.temp_env_map = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(test_config, cls.temp_env_map)
        cls.temp_env_map.close()

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
            f.write("""#!/bin/bash
if [[ "$*" == *"get"* ]]; then
    echo "test_token_123"
fi
exit 0
""")
        os.chmod(cls.mock_gloves_path, 0o755)

        cls.env = os.environ.copy()
        cls.env["PYTHONPATH"] = PROJECT_ROOT
        cls.env["PATH"] = cls.mock_dir + ":" + os.environ.get("PATH", "")
        cls.env["MCPORTER_PROXY_PORT"] = str(9906)
        cls.env["MCPORTER_PROXY_ALLOWED_TOOLS"] = "*"
        cls.env["MCPORTER_PROXY_LOG_LEVEL"] = "DEBUG"
        cls.env["MCP_ENV_MAP_PATH"] = cls.temp_env_map.name
        cls.server_process = subprocess.Popen(
            SERVER_MODULE_CMD,
            env=cls.env,
        )
        time.sleep(0.5)
        if cls.server_process.poll() is not None:
            raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server_process.kill()
        cls.server_process.wait()
        os.unlink(cls.mock_mcporter_path)
        os.unlink(cls.mock_gloves_path)
        os.rmdir(cls.mock_dir)
        os.unlink(cls.temp_env_map.name)
        import importlib
        import server.server

        importlib.reload(server.server)

    def test_upload_requires_auth(self):
        conn = HTTPConnection("localhost", 9906)
        body = b'--simpleboundary\r\nContent-Disposition: form-data; name="file"; filename="test.txt"\r\nContent-Type: text/plain\r\n\r\nHello World\r\n--simpleboundary--\r\n'
        conn.request(
            "POST",
            "/upload-attachment",
            body=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=simpleboundary",
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 401)
        conn.close()

    def test_upload_missing_x_target_platform(self):
        conn = HTTPConnection("localhost", 9906)
        body = b'--simpleboundary\r\nContent-Disposition: form-data; name="file"; filename="test.txt"\r\nContent-Type: text/plain\r\n\r\nHello World\r\n--simpleboundary--\r\n'
        conn.request(
            "POST",
            "/upload-attachment",
            body=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=simpleboundary",
                "Content-Length": str(len(body)),
                "X-MCP-Auth-Key": "agent-key",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        conn.close()

    def test_upload_success(self):
        from unittest.mock import patch, MagicMock

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.reason = "OK"
        mock_response.read.side_effect = [
            b'{"id": "123", "url": "https://example.com/file"}',
            b"",
        ]
        mock_response.getheader.side_effect = lambda name, default=None: {
            "Content-Type": "application/json",
        }.get(name, default)

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_response

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            conn = HTTPConnection("localhost", 9906)
            body = b'--simpleboundary\r\nContent-Disposition: form-data; name="file"; filename="test.txt"\r\nContent-Type: text/plain\r\n\r\nHello World\r\n--simpleboundary--\r\n'
            conn.request(
                "POST",
                "/upload-attachment",
                body=body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=simpleboundary",
                    "Content-Length": str(len(body)),
                    "X-MCP-Auth-Key": "agent-key",
                    "X-Target-Platform": "atlassian",
                    "X-Target-Args": json.dumps(
                        {"page_id": "217090082", "name": "test.md"}
                    ),
                },
            )
            resp = conn.getresponse()
            self.assertEqual(
                resp.status,
                200,
                f"Expected 200, got {resp.status}: {resp.read().decode('utf-8', errors='replace')}",
            )
            conn.close()

    def test_upload_missing_file_in_multipart(self):
        conn = HTTPConnection("localhost", 9906)
        body = b'--simpleboundary\r\nContent-Disposition: form-data; name="notfile"\r\n\r\nHello World\r\n--simpleboundary--\r\n'
        conn.request(
            "POST",
            "/upload-attachment",
            body=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=simpleboundary",
                "Content-Length": str(len(body)),
                "X-MCP-Auth-Key": "agent-key",
                "X-Target-Platform": "atlassian",
                "X-Target-Args": json.dumps(
                    {"page_id": "217090082", "name": "test.md"}
                ),
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        conn.close()
