"""
utils.py — Shared helpers for the AI Test Case Generator backend.
"""
import os
import re
from pathlib import Path


def sanitize_filename(filename: str) -> str:
    """Strip dangerous characters from a filename (path traversal prevention)."""
    return re.sub(r"[^\w\-_\.]", "_", Path(filename).name)


def sanitize_module_name(stem: str) -> str:
    """
    Convert any filename stem into a valid Python identifier.

    Examples:
        "import random"  -> "import_random"
        "my-module"      -> "my_module"
        "123abc"         -> "_123abc"
        "calc"           -> "calc"
    """
    # Replace every non-alphanumeric character with underscore
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", stem)
    # Collapse runs of underscores, strip leading/trailing
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    # Identifiers can't start with a digit
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "module"


def ensure_dirs(*dirs: str) -> None:
    """Create directories if they don't already exist."""
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len characters, appending ellipsis if cut."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
