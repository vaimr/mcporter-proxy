"""Schema parsing and formatting for mcporter tools."""

import json
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mcporter-proxy")


def run_mcporter_list_schema(
    mcptype: str,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cmd = ["mcporter", "list", mcptype, "--schema"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return [], f"mcporter returned {result.returncode}: {result.stderr}"
        return _parse_mcporter_schema_output(result.stdout), None
    except FileNotFoundError:
        return [], "mcporter not found"
    except subprocess.TimeoutExpired:
        return [], "mcporter timed out"
    except Exception as e:
        return [], str(e)


def _parse_mcporter_schema_output(output: str) -> List[Dict[str, Any]]:
    tools = []
    current_tool: Optional[Dict[str, Any]] = None
    json_buffer: List[str] = []
    in_json = False
    json_depth = 0
    pending_desc_lines: List[str] = []
    pending_desc_active = False

    for line in output.splitlines():
        line = line.rstrip()
        if not in_json:
            if pending_desc_active and line.strip() == "*/":
                pending_desc_active = False
                continue
            elif pending_desc_active:
                if line.startswith("   *"):
                    line = line[4:]
                    if line.startswith("*"):
                        line = line[1:]
                    line = line.strip()
                elif line.strip().startswith("*"):
                    line = line.strip()[1:].strip()
                else:
                    line = line.strip()
                if line:
                    pending_desc_lines.append(line)
                continue

        if line.startswith("  /**"):
            pending_desc_active = True
            pending_desc_lines = []
            continue
        elif "Examples:" in line or line.startswith("  ---"):
            pending_desc_lines = []
            pending_desc_active = False
            continue

        if re.match(r"^\s*function\s+", line):
            func_match = re.match(r"^\s*function\s+(\w+)\s*\(([^)]*)\)", line)
            if func_match:
                if pending_desc_lines:
                    description = "\n".join(pending_desc_lines).strip()
                else:
                    description = ""
                current_tool = {
                    "name": func_match.group(1),
                    "description": description,
                    "inputSchema": {},
                }
                pending_desc_lines = []
                pending_desc_active = False
            continue

        if current_tool is not None and not in_json:
            if "{" in line:
                in_json = True
                json_depth = line.count("{") - line.count("}")
                json_buffer = [line]
                continue

        if in_json:
            json_buffer.append(line)
            for char in line:
                if char == "{":
                    json_depth += 1
                elif char == "}":
                    json_depth -= 1
            if json_depth == 0:
                in_json = False
                json_str = "\n".join(json_buffer)
                json_str = json_str.rstrip(",").rstrip()
                try:
                    schema = json.loads(json_str)
                    current_tool["inputSchema"] = schema
                except json.JSONDecodeError:
                    pass
                json_buffer = []
                tools.append(current_tool)
                current_tool = None

    return tools


def format_tool_as_text_schema(tool: Dict[str, Any]) -> str:
    name = tool.get("name", "")
    description = tool.get("description", "")
    input_schema = tool.get("inputSchema", {})
    properties = input_schema.get("properties", {})
    args_props = properties.get("args", {}).get("properties", {})
    if not args_props:
        args_props = {k: v for k, v in properties.items() if k != "mcptype"}

    lines = [f"  function {name}(", f"      /*{description}*/"]
    for prop_name, prop_value in args_props.items():
        if prop_name in ("mcptype", "_proxy_endpoint", "_proxy_direction"):
            continue
        if not isinstance(prop_value, dict):
            continue
        prop_type = prop_value.get("type", "string")
        lines.append(f"      {prop_name}: {prop_type},")
    lines.append("  );")
    return "\n".join(lines)


__all__ = [
    "run_mcporter_list_schema",
    "_parse_mcporter_schema_output",
    "format_tool_as_text_schema",
]
