"""
environment_builder.py — Unified Test Execution Environment Builder
Creates temporary directories with all source files and test code co-located
so that imports resolve naturally when pytest or Jest runs.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TEMP_DIR = os.getenv("TEMP_DIR", "temp")

_PACKAGE_JSON = {
    "name": "test-env",
    "version": "1.0.0",
    "scripts": {"test": "jest"},
    "devDependencies": {"jest": "^29.0.0"},
    "jest": {
        "testEnvironment": "node",
        "testTimeout": int(os.getenv("JEST_TIMEOUT_MS", "30000")),
    },
}


def _make_temp_dir() -> str:
    """Create a uniquely-named temp directory under TEMP_DIR."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    ts = int(time.time() * 1000)
    path = os.path.join(TEMP_DIR, f"session_{ts}")
    os.makedirs(path, exist_ok=True)
    return path


def build_environment(uploaded_files: list, test_code: str, temp_dir: str = "") -> str:
    """
    Copy all uploaded Python source files into temp_dir and write the test code.

    Args:
        uploaded_files: absolute paths to the uploaded .py source files
        test_code:      generated test code as a string
        temp_dir:       directory to use; creates a new one if empty

    Returns:
        Absolute path to the written test file inside temp_dir.
    """
    if not temp_dir:
        temp_dir = _make_temp_dir()

    os.makedirs(temp_dir, exist_ok=True)

    from .utils import sanitize_module_name
    for fp in uploaded_files:
        safe_name = sanitize_module_name(Path(fp).stem) + ".py"
        dest = os.path.join(temp_dir, safe_name)
        shutil.copy2(fp, dest)

    test_file = os.path.join(temp_dir, "test_generated.py")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    return test_file


def build_js_environment(uploaded_files: list, test_code: str, temp_dir: str = "") -> str:
    """
    Copy all uploaded JS source files into temp_dir, write the test code,
    and create a minimal package.json for Jest.

    Returns:
        Absolute path to the written .test.js file inside temp_dir.
    """
    if not temp_dir:
        temp_dir = _make_temp_dir()

    os.makedirs(temp_dir, exist_ok=True)

    for fp in uploaded_files:
        dest = os.path.join(temp_dir, Path(fp).name)
        shutil.copy2(fp, dest)

    # Write package.json so Jest is discoverable
    pkg_path = os.path.join(temp_dir, "package.json")
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(_PACKAGE_JSON, f, indent=2)

    test_file = os.path.join(temp_dir, "test_generated.test.js")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    return test_file


def cleanup_environment(temp_dir: str) -> None:
    """Remove the temp directory and all its contents."""
    try:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass
