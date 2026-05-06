"""Authentication and secrets resolution for mcporter-proxy."""

import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Tuple

try:
    from server.config import MCP_ENV_MAP, SECRETS_PREFIX, SECRETS_SEPARATOR, get_env_vars_for_mcptype
except ImportError:
    from config import MCP_ENV_MAP, SECRETS_PREFIX, SECRETS_SEPARATOR, get_env_vars_for_mcptype

logger = logging.getLogger("mcporter-proxy")

UPSTREAM_CONNECT_TIMEOUT = 10
UPSTREAM_READ_TIMEOUT = 300
UPSTREAM_TIMEOUT = max(UPSTREAM_CONNECT_TIMEOUT, UPSTREAM_READ_TIMEOUT)


def parse_auth_key(auth_key: str) -> Optional[Tuple[str, str]]:
    if not auth_key:
        logger.warning("Auth key is empty")
        return None
    if "-" in auth_key:
        parts = auth_key.split("-", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    if "/" in auth_key:
        parts = auth_key.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    logger.warning(
        "Invalid auth key format: expected <agentId>-<agentKey> or <agentId>/<agentKey>, got: %s",
        auth_key,
    )
    return None


def get_mcptype(tool: str) -> str:
    return tool.split(".")[0] if "." in tool else tool


def get_auth_key_separator(auth_key: str) -> str:
    if "/" in auth_key:
        return "/"
    if "-" in auth_key:
        return "-"
    return "/"


def get_secrets_keys(
    mcptype: str, agent_id: str, agent_key: str
) -> List[Tuple[int, str]]:
    env_vars = get_env_vars_for_mcptype(mcptype)
    if not env_vars:
        return [(0, f"{SECRETS_PREFIX}/{agent_id}/{mcptype}/{agent_key}")]
    sep = SECRETS_SEPARATOR if SECRETS_SEPARATOR else "/"
    keys = []
    for i, _ in enumerate(env_vars):
        if i == 0:
            key = f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}"
        else:
            key = (
                f"{SECRETS_PREFIX}{sep}{agent_id}{sep}{mcptype}{sep}{agent_key}{sep}{i}"
            )
        keys.append((i, key))
    return keys


def get_available_secrets(agent_id: str) -> set:
    try:
        result = subprocess.run(
            ["gloves", "--agent", agent_id, "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            secrets = json.loads(result.stdout)
            return {s["id"] for s in secrets if s.get("kind") == "secret"}
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
        json.JSONDecodeError,
    ) as e:
        logger.error("Failed to list available secrets: %s", e)
    return set()


def resolve_token(mcptype: str, agent_id: str, agent_key: str) -> Optional[str]:
    keys = get_secrets_keys(mcptype, agent_id, agent_key)
    if not keys:
        return None
    secrets_key = keys[0][1]
    try:
        result = subprocess.run(
            ["gloves", "--agent", agent_id, "get", secrets_key],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("Failed to resolve token: %s", e)
    return None


def resolve_extra_secrets(
    mcptype: str, agent_id: str, agent_key: str
) -> Dict[str, str]:
    keys = get_secrets_keys(mcptype, agent_id, agent_key)
    secrets = {}
    for i, key in keys:
        if i == 0:
            continue
        try:
            result = subprocess.run(
                ["gloves", "--agent", agent_id, "get", key],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                env_vars = get_env_vars_for_mcptype(mcptype)
                if i < len(env_vars):
                    secrets[env_vars[i]] = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return secrets


__all__ = [
    "UPSTREAM_TIMEOUT",
    "parse_auth_key",
    "get_mcptype",
    "get_auth_key_separator",
    "get_secrets_keys",
    "get_available_secrets",
    "resolve_token",
    "resolve_extra_secrets",
]
