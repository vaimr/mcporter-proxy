import base64
import http.client
import json
import logging
import os
import re
import secrets
import urllib.parse
from typing import Any, Dict, Optional

from requests_toolbelt import MultipartEncoder

try:
    from server.utils import mask_headers_for_log, mask_token, substitute_template
except ImportError:
    from utils import mask_headers_for_log, mask_token, substitute_template

logger = logging.getLogger("mcporter-proxy")

UPSTREAM_TIMEOUT = 300


def build_upload_headers(
    headers_template: Dict[str, str],
    token: Optional[str],
    extra_secrets: Dict[str, str],
    args: Dict[str, Any],
) -> Dict[str, str]:
    headers = {}
    for key, template in headers_template.items():
        value = template
        if "{basic_auth}" in value:
            email = extra_secrets.get("email", "") or extra_secrets.get(
                "JIRA_EMAIL", ""
            )
            auth_string = f"{email}:{token}"
            value = value.replace(
                "{basic_auth}", base64.b64encode(auth_string.encode()).decode()
            )
        if token and "{token}" in value:
            value = value.replace("{token}", token)
        for match in re.finditer(r"\$\{([^}]+)\}", value):
            env_name = match.group(1)
            env_value = extra_secrets.get(env_name, os.environ.get(env_name, ""))
            value = value.replace(f"${{{env_name}}}", env_value)
        for arg_key, arg_val in args.items():
            value = value.replace(f"{{{arg_key}}}", str(arg_val))
        headers[key] = value
    return headers


def upload_via_rest(
    config: Dict[str, Any],
    args: Dict[str, Any],
    file_stream,
    filename: str,
    content_type: str,
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Dict[str, Any]:
    method = config.get("method", "POST").upper()
    url_template = config.get("url_template", "")
    headers_template = config.get("headers", {})

    url = substitute_template(
        url_template, {**args, "token": token or "", **extra_secrets}
    )
    headers = build_upload_headers(headers_template, token, extra_secrets, args)

    logger.debug(
        "upload_via_rest: token=%s, extra_secrets_keys=%s, headers=%s",
        mask_token(token),
        list(extra_secrets.keys()),
        mask_headers_for_log(headers),
    )

    parsed_url = urllib.parse.urlparse(url)

    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_TIMEOUT,
    )
    conn.connect()
    conn.putrequest(method, parsed_url.path, skip_host=True)
    if parsed_url.query:
        conn.putheader("X-Original-URI", f"{parsed_url.path}?{parsed_url.query}")
    conn.putheader("Host", parsed_url.netloc)
    for k, v in headers.items():
        if k.lower() != "content-type":
            conn.putheader(k, v)

    encoder = MultipartEncoder(fields={"file": (filename, file_stream, content_type)})

    encoded_body = encoder.read()
    body_length = len(encoded_body)

    conn.putheader("Content-Type", encoder.content_type)
    conn.putheader("Content-Length", str(body_length))

    conn.endheaders()

    try:
        conn.send(encoded_body)
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        conn.close()
        try:
            from server.download import MCPToolError
        except ImportError:
            from download import MCPToolError

        raise MCPToolError(f"Upload failed: connection error during send: {e}")

    response = conn.getresponse()
    response_body = response.read().decode("utf-8", errors="replace")
    conn.close()

    if response.status >= 400:
        try:
            from server.download import MCPToolError
        except ImportError:
            from download import MCPToolError

        raise MCPToolError(
            f"Upload failed: {response.status} {response.reason}: {response_body[:500]}"
        )

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError:
        result = {"raw_response": response_body}

    return result


def upload_via_rest_streaming(
    config: Dict[str, Any],
    args: Dict[str, Any],
    file_stream,
    filename: str,
    content_type: str,
    token: Optional[str],
    extra_secrets: Dict[str, str],
) -> Dict[str, Any]:
    method = config.get("method", "POST").upper()
    url_template = config.get("url_template", "")
    headers_template = config.get("headers", {})

    url = substitute_template(
        url_template, {**args, "token": token or "", **extra_secrets}
    )
    headers = build_upload_headers(headers_template, token, extra_secrets, args)

    logger.debug(
        "upload_via_rest_streaming: token=%s, extra_secrets_keys=%s, headers=%s",
        mask_token(token),
        list(extra_secrets.keys()),
        mask_headers_for_log(headers),
    )

    parsed_url = urllib.parse.urlparse(url)

    conn = http.client.HTTPSConnection(
        parsed_url.netloc,
        timeout=UPSTREAM_TIMEOUT,
    )
    conn.connect()
    conn.putrequest(method, parsed_url.path, skip_host=True)
    if parsed_url.query:
        conn.putheader("X-Original-URI", f"{parsed_url.path}?{parsed_url.query}")
    conn.putheader("Host", parsed_url.netloc)
    for k, v in headers.items():
        if k.lower() != "content-type":
            conn.putheader(k, v)

    boundary = secrets.token_hex(16)
    content_type_header = f"multipart/form-data; boundary={boundary}"
    conn.putheader("Content-Type", content_type_header)
    conn.putheader("Transfer-Encoding", "chunked")

    conn.endheaders()

    try:
        header_chunk = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        chunk_len = len(header_chunk)
        conn.send(f"{chunk_len:x}\r\n".encode())
        conn.send(header_chunk)

        CHUNK_SIZE = 262144
        while True:
            data = file_stream.read(CHUNK_SIZE)
            if not data:
                break
            chunk_size = len(data)
            conn.send(f"{chunk_size:x}\r\n".encode())
            conn.send(data)
            conn.send(b"\r\n")

        footer = f"--{boundary}--\r\n"
        footer_bytes = footer.encode()
        conn.send(f"{len(footer_bytes):x}\r\n".encode())
        conn.send(footer_bytes)
        conn.send(b"\r\n")

        conn.send(b"0\r\n\r\n")
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        conn.close()
        try:
            from server.download import MCPToolError
        except ImportError:
            from download import MCPToolError

        raise MCPToolError(f"Upload failed: connection error during send: {e}")

    response = conn.getresponse()
    response_body = response.read().decode("utf-8", errors="replace")
    conn.close()

    if response.status >= 400:
        try:
            from server.download import MCPToolError
        except ImportError:
            from download import MCPToolError

        raise MCPToolError(f"Upload failed: {response.status} {response.reason}: {response_body[:500]}")

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError:
        result = {"raw_response": response_body}

    return result


__all__ = [
    "upload_via_rest",
    "upload_via_rest_streaming",
    "build_upload_headers",
]
