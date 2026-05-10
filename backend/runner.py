"""
runner.py — pytest Test Executor
Saves generated test code to a temporary file and executes it with pytest via subprocess.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

from .utils import sanitize_module_name

load_dotenv()

TEST_OUTPUT_DIR = os.getenv("TEST_OUTPUT_DIR", "generated_tests")
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))


def run_tests(test_code: str, source_file_path: str) -> dict:
    """
    Write test_code to a file alongside a copy of the source file, then run pytest.

    Returns:
        {
            passed: list[str],
            failed: list[str],
            errors: dict[str, str],
            all_passed: bool,
            test_file: str,
            output: str,
        }
    """
    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)

    timestamp = int(time.time() * 1000)
    source_path = Path(source_file_path)
    # Sanitize so filenames like "import random.py" become valid Python identifiers
    module_name = sanitize_module_name(source_path.stem)

    test_filename = f"test_{module_name}_{timestamp}.py"
    test_file = os.path.join(TEST_OUTPUT_DIR, test_filename)

    # Copy source file with the sanitized name so "from <module_name> import *" resolves
    dest_source = os.path.join(TEST_OUTPUT_DIR, f"{module_name}.py")
    shutil.copy2(source_file_path, dest_source)

    # Replace any <module_name> placeholder the LLM may have left
    test_code = test_code.replace("<module_name>", module_name)

    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    try:
        result = subprocess.run(
            ["pytest", test_filename, "--tb=short", "-v", "--no-header"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            cwd=TEST_OUTPUT_DIR,
        )
        return _parse_output(result.stdout, result.stderr, test_file)
    except subprocess.TimeoutExpired:
        return {
            "passed": [],
            "failed": ["timeout_error"],
            "errors": {"timeout_error": f"Tests timed out after {TIMEOUT_SECONDS}s"},
            "all_passed": False,
            "test_file": test_file,
            "output": f"TIMEOUT after {TIMEOUT_SECONDS}s",
        }
    except FileNotFoundError:
        return {
            "passed": [],
            "failed": ["setup_error"],
            "errors": {"setup_error": "pytest not found — run: pip install pytest"},
            "all_passed": False,
            "test_file": test_file,
            "output": "pytest not found",
        }


def _parse_output(stdout: str, stderr: str, test_file: str) -> dict:
    passed = []
    failed = []
    errors = {}

    # Parse verbose pytest lines:  PASSED / FAILED / ERROR
    for line in stdout.splitlines():
        p = re.search(r"::(test_\w+)\s+PASSED", line)
        f = re.search(r"::(test_\w+)\s+FAILED", line)
        e = re.search(r"::(test_\w+)\s+ERROR", line)
        if p:
            passed.append(p.group(1))
        elif f:
            failed.append(f.group(1))
        elif e:
            failed.append(e.group(1))

    # Extract short error messages from FAILURES block
    failure_blocks = re.findall(
        r"_{5,}\s+(test_\w+)\s+_{5,}(.*?)(?=_{5,}|\Z)", stdout, re.DOTALL
    )
    for test_name, block in failure_blocks:
        # Grab the E-prefixed lines
        e_lines = re.findall(r"^E\s+(.+)$", block, re.MULTILINE)
        errors[test_name] = "; ".join(e_lines[:5]) if e_lines else block.strip()[:300]

    # Handle collection errors (no tests ran at all)
    if not passed and not failed:
        combined = (stdout + stderr).strip()
        if combined:
            errors["collection_error"] = combined[:600]
            failed.append("collection_error")

    # Supplement with stderr import/syntax errors
    if stderr and ("ImportError" in stderr or "SyntaxError" in stderr or "ModuleNotFoundError" in stderr):
        errors.setdefault("import_error", stderr[:500])
        if "import_error" not in failed:
            failed.insert(0, "import_error")

    all_passed = len(failed) == 0 and len(passed) > 0

    return {
        "passed": list(dict.fromkeys(passed)),   # deduplicate, preserve order
        "failed": list(dict.fromkeys(failed)),
        "errors": errors,
        "all_passed": all_passed,
        "test_file": test_file,
        "output": stdout,
    }
