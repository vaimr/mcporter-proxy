"""Configuration loading and validation for mcporter-proxy."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcporter-proxy")

SCRIPT_DIR = Path(__file__).parent
ENV_MAP_PATH = SCRIPT_DIR / "mcp_env_map.json"

DEFAULT_SECRETS_PREFIX = "agents"
SECRETS_PREFIX = os.environ.get("MCPROXY_SECRETS_PREFIX", DEFAULT_SECRETS_PREFIX)
SECRETS_SEPARATOR = os.environ.get("MCPROXY_SECRETS_SEPARATOR", "")


def get_env_map_path() -> Path:
    return Path(os.environ.get("MCP_ENV_MAP_PATH", ENV_MAP_PATH))


def validate_config(config: Dict[str, Any], mcptype: str) -> List[str]:
    """Validate attachment_download and attachment_upload config for a mcptype."""
    errors = []
    if isinstance(config, list):
        return errors

    ad = config.get("attachment_download")
    if ad:
        download_type = ad.get("type")
        if download_type not in ("rest_api", "mcp_tool_redirect", "mcp_tool"):
            errors.append(f"Invalid download type '{download_type}' for {mcptype}")
        elif download_type == "rest_api":
            if not ad.get("url_template"):
                errors.append(f"Missing url_template for {mcptype} download")
        elif download_type in ("mcp_tool_redirect", "mcp_tool"):
            if not ad.get("tool_name"):
                errors.append(f"Missing tool_name for {mcptype} download")

    au = config.get("attachment_upload")
    if au:
        upload_type = au.get("type")
        if upload_type not in ("rest_api", "mcp_tool"):
            errors.append(f"Invalid upload type '{upload_type}' for {mcptype}")
        elif upload_type == "rest_api":
            if not au.get("url_template"):
                errors.append(f"Missing url_template for {mcptype} upload")
            if not au.get("headers"):
                errors.append(f"Missing headers for {mcptype} upload")
        elif upload_type == "mcp_tool":
            if not au.get("tool_name"):
                errors.append(f"Missing tool_name for {mcptype} upload")

    return errors


def load_env_map() -> Dict[str, Any]:
    try:
        env_map_path = get_env_map_path()
        with open(env_map_path, encoding="utf-8") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    all_errors = []
    for mcptype, cfg in config.items():
        errors = validate_config(cfg, mcptype)
        all_errors.extend(errors)

    if all_errors:
        for err in all_errors:
            logger.error("Config validation: %s", err)
        raise ValueError(f"Invalid config: {'; '.join(all_errors)}")

    return config


MCP_ENV_MAP = load_env_map()
logger.info("Loaded MCP env map: %s", MCP_ENV_MAP)


def get_env_vars_for_mcptype(mcptype: str) -> List[str]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return []
    if isinstance(config, list):
        return config
    if isinstance(config, dict) and "env" in config:
        return config["env"]
    return []


def get_attachment_download_config(mcptype: str) -> Optional[Dict[str, Any]]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return None
    if isinstance(config, list):
        return None
    return config.get("attachment_download")


def get_attachment_upload_config(mcptype: str) -> Optional[Dict[str, Any]]:
    config = MCP_ENV_MAP.get(mcptype)
    if not config:
        return None
    if isinstance(config, list):
        return None
    return config.get("attachment_upload")


__all__ = [
    "MCP_ENV_MAP",
    "SECRETS_PREFIX",
    "SECRETS_SEPARATOR",
    "get_env_map_path",
    "get_env_vars_for_mcptype",
    "get_attachment_download_config",
    "get_attachment_upload_config",
]
