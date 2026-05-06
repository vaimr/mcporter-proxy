"""Utility functions for mcporter-proxy."""

import os
import re
import urllib.parse
from typing import Any, Dict


def mask_token(token: str) -> str:
    """Mask sensitive token for logging."""
    if not token:
        return "<none>"
    prefix = "Bearer "
    if token.startswith(prefix):
        token_part = token[len(prefix) :]
        if len(token_part) <= 12:
            return prefix + "*" * len(token_part)
        prefix_len = min(6, len(token_part) - 10)
        suffix_len = 8
        return f"{prefix}{token_part[:prefix_len]}...{token_part[-suffix_len:]}"
    token_len = len(token)
    if token_len <= 12:
        return "*" * token_len
    prefix_len = min(6, token_len - 10)
    suffix_len = 8
    return f"{token[:prefix_len]}...{token[-suffix_len:]}"


def mask_headers_for_log(headers: Dict[str, str]) -> Dict[str, str]:
    """Mask sensitive headers for logging."""
    masked = {}
    sensitive = {"authorization", "cookie", "x-auth-token", "x-api-key"}
    for key, value in headers.items():
        if key.lower() in sensitive:
            masked[key] = mask_token(value)
        else:
            masked[key] = value
    return masked


def substitute_template(template: str, values: Dict[str, str]) -> str:
    """Substitute {placeholder} values in template string."""
    result = template
    for key, value in values.items():
        encoded = urllib.parse.quote(str(value), safe="-_.~")
        result = result.replace(f"{{{key}}}", encoded)
    return result


def _camel_to_kebab(name: str) -> str:
    """Convert camelCase to kebab-case."""
    result = []
    for i, char in enumerate(name):
        if char == "_":
            if result and result[-1] != "-":
                result.append("-")
        elif char.isupper() and i > 0:
            if result and result[-1] != "-":
                result.append("-")
            result.append(char.lower())
        else:
            result.append(char.lower())
    return "".join(result).rstrip("-")


__all__ = [
    "mask_token",
    "mask_headers_for_log",
    "substitute_template",
    "_camel_to_kebab",
]
