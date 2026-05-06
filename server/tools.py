"""Attachment tool schema building and injection."""

from typing import Any, Dict, List, Optional

try:
    from server.config import get_attachment_download_config, get_attachment_upload_config
except ImportError:
    from config import get_attachment_download_config, get_attachment_upload_config
try:
    from server.schema import format_tool_as_text_schema
except ImportError:
    from schema import format_tool_as_text_schema
try:
    from server.utils import _camel_to_kebab
except ImportError:
    from utils import _camel_to_kebab


def build_attachment_schema(
    mcptype: str,
    config: Dict[str, Any],
    direction: str,
) -> Dict[str, Any]:
    endpoint = (
        "/download-attachment" if direction == "download" else "/upload-attachment"
    )
    method = "POST"
    if direction == "download":
        desc = "Download file attachment from platform"
        args_props = _get_download_args_properties(mcptype)
    else:
        desc = "Upload file attachment to platform"
        args_props = _get_upload_args_properties(mcptype)

    return {
        "name": f"{mcptype}.attachment_{direction}",
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": {
                "mcptype": {"const": mcptype},
                "args": {
                    "type": "object",
                    "properties": args_props,
                },
            },
        },
        "_proxy_endpoint": f"{method} {endpoint}",
        "_proxy_direction": direction,
    }


def _get_download_args_properties(mcptype: str) -> Dict[str, Any]:
    if mcptype == "github":
        return {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "asset_id": {"type": "string"},
            "filename": {"type": "string"},
        }
    elif mcptype == "gitlab":
        return {
            "project_id": {"type": "string"},
            "file_path": {"type": "string"},
            "ref": {"type": "string"},
        }
    elif mcptype in ("confluence", "atlassian"):
        return {
            "page_id": {"type": "string"},
            "filename": {"type": "string"},
        }
    elif mcptype == "jira":
        return {
            "issue_key": {"type": "string"},
            "attachment_id": {"type": "string"},
        }
    elif mcptype == "cognee":
        return {
            "dataset_name": {"type": "string"},
        }
    else:
        return {
            "id": {"type": "string"},
            "filename": {"type": "string"},
        }


def _get_upload_args_properties(mcptype: str) -> Dict[str, Any]:
    if mcptype == "github":
        return {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "upload_url": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "gitlab":
        return {
            "project_id": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype in ("confluence", "atlassian"):
        return {
            "page_id": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "jira":
        return {
            "issue_key": {"type": "string"},
            "name": {"type": "string"},
            "file": {"type": "string"},
        }
    elif mcptype == "cognee":
        return {
            "data": {"type": "string"},
            "dataset_name": {"type": "string"},
        }
    else:
        return {
            "name": {"type": "string"},
            "file": {"type": "string"},
        }


def transform_meta_tool(
    tool: Dict[str, Any], include_schema: bool = True
) -> Dict[str, Any]:
    if "_proxy_endpoint" not in tool and "_proxy_direction" not in tool:
        result = dict(tool)
        result["options"] = []
        return result

    name = tool.get("name", "")
    description = tool.get("description", "")

    if not include_schema:
        return {"name": name, "description": description}

    input_schema = tool.get("inputSchema", {})
    properties = input_schema.get("properties", {})

    args_props = properties.get("args", {}).get("properties", {})
    if not args_props:
        args_props = {k: v for k, v in properties.items() if k != "mcptype"}

    flat_props = {}
    options = []
    for prop_name, prop_value in args_props.items():
        if prop_name in ("mcptype", "_proxy_endpoint", "_proxy_direction"):
            continue
        if not isinstance(prop_value, dict):
            continue

        required = prop_value.get("required", False)
        prop_type = prop_value.get("type", "string")
        prop_desc = prop_value.get("description", "")

        flat_props[prop_name] = {"type": prop_type}
        if prop_desc:
            flat_props[prop_name]["description"] = prop_desc

        options.append(
            {
                "property": prop_name,
                "cliName": _camel_to_kebab(prop_name),
                "description": prop_desc,
                "required": required,
                "type": prop_type,
                "placeholder": f"<{prop_name}>",
                "exampleValue": "",
            }
        )

    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": flat_props},
        "options": options,
    }


def build_attachment_text_schema_for_mcptype(mcptype: str) -> Optional[str]:
    download_config = get_attachment_download_config(mcptype)
    upload_config = get_attachment_upload_config(mcptype)

    lines = []
    if download_config:
        download_tool = build_attachment_schema(mcptype, download_config, "download")
        lines.append(format_tool_as_text_schema(download_tool))
    if upload_config:
        upload_tool = build_attachment_schema(mcptype, upload_config, "upload")
        lines.append(format_tool_as_text_schema(upload_tool))

    if not lines:
        return None
    return "\n\nMeta-tools (attachment tools):\n" + "\n".join(lines) + "\n"


def add_attachment_tools_to_server(
    server: Dict[str, Any], include_schema: bool = True
) -> None:
    name = server.get("name")
    if not name:
        return

    download_config = get_attachment_download_config(name)
    upload_config = get_attachment_upload_config(name)

    tools = server.get("tools", [])

    if download_config:
        download_tool = build_attachment_schema(name, download_config, "download")
        download_tool = transform_meta_tool(
            download_tool, include_schema=include_schema
        )
        tools.append(download_tool)

    if upload_config:
        upload_tool = build_attachment_schema(name, upload_config, "upload")
        upload_tool = transform_meta_tool(upload_tool, include_schema=include_schema)
        tools.append(upload_tool)

    server["tools"] = tools


def inject_attachment_tools(data: Any, include_schema: bool = True) -> None:
    if not isinstance(data, dict):
        return

    if data.get("mode") == "server":
        add_attachment_tools_to_server(data, include_schema=include_schema)
        return

    servers = data.get("servers", [])
    if isinstance(servers, list):
        for server in servers:
            add_attachment_tools_to_server(server, include_schema=include_schema)


__all__ = [
    "build_attachment_schema",
    "transform_meta_tool",
    "inject_attachment_tools",
    "add_attachment_tools_to_server",
]
