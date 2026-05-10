"""
runner_js.py — Jest Test Executor
Saves generated JS test code and runs Jest via Node.js subprocess.
Parses Jest's --json output for structured pass/fail results.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

NODE_PATH = os.getenv("NODE_PATH", "node")
JEST_TIMEOUT_MS = int(os.getenv("JEST_TIMEOUT_MS", "30000"))
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "60"))
TEST_OUTPUT_DIR = os.getenv("TEST_OUTPUT_DIR", "generated_tests")

# On Windows, subprocess cannot resolve bare 'npx' — must use 'npx.cmd'
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"

_PACKAGE_JSON = {
    "name": "test-env",
    "version": "1.0.0",
    "scripts": {"test": "jest"},
    "devDependencies": {"jest": "^29.0.0"},
    "jest": {
        "testEnvironment": "node",
        "testTimeout": JEST_TIMEOUT_MS,
    },
}


def run_js_tests(test_code: str, source_file_path: str) -> dict:
    """
    Write test_code to a .test.js file alongside the source file and run Jest.

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
    module_name = source_path.stem

    test_filename = f"{module_name}_{timestamp}.test.js"
    test_file = os.path.join(TEST_OUTPUT_DIR, test_filename)
    results_file = os.path.join(TEST_OUTPUT_DIR, f"jest_results_{timestamp}.json")

    # Copy source file to test dir
    dest_source = os.path.join(TEST_OUTPUT_DIR, source_path.name)
    shutil.copy2(source_file_path, dest_source)

    # Write package.json so Jest works
    pkg_path = os.path.join(TEST_OUTPUT_DIR, "package.json")
    if not os.path.exists(pkg_path):
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump(_PACKAGE_JSON, f, indent=2)

    # Fix any <module_name> placeholder
    test_code = test_code.replace("<module_name>", module_name)

    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    try:
        result = subprocess.run(
            [
                _NPX, "jest",
                test_filename,
                "--json",
                f"--outputFile={Path(results_file).name}",
                "--no-coverage",
                "--forceExit",
            ],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            cwd=TEST_OUTPUT_DIR,
        )
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        return _parse_jest_output(results_file, test_file, stderr)
    except subprocess.TimeoutExpired:
        return {
            "passed": [],
            "failed": ["timeout_error"],
            "errors": {"timeout_error": f"Jest timed out after {TIMEOUT_SECONDS}s"},
            "all_passed": False,
            "test_file": test_file,
            "output": "TIMEOUT",
        }
    except FileNotFoundError:
        return {
            "passed": [],
            "failed": ["setup_error"],
            "errors": {"setup_error": "npx/jest not found — ensure Node.js 18+ and Jest are installed"},
            "all_passed": False,
            "test_file": test_file,
            "output": "Jest not found",
        }
    finally:
        # Clean up results JSON
        try:
            if os.path.exists(results_file):
                os.remove(results_file)
        except Exception:
            pass


def run_js_tests_in_dir(test_filename: str, working_dir: str) -> dict:
    """Run Jest on an existing test file inside a pre-built working directory."""
    test_file = os.path.join(working_dir, test_filename)
    results_path = os.path.join(working_dir, "jest_results.json")

    try:
        result = subprocess.run(
            [
                _NPX, "jest",
                test_filename,
                "--json",
                "--outputFile=jest_results.json",
                "--no-coverage",
                "--forceExit",
            ],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            cwd=working_dir,
        )
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        return _parse_jest_output(results_path, test_file, stderr)
    except subprocess.TimeoutExpired:
        return {
            "passed": [],
            "failed": ["timeout_error"],
            "errors": {"timeout_error": f"Jest timed out after {TIMEOUT_SECONDS}s"},
            "all_passed": False,
            "test_file": test_file,
            "output": "TIMEOUT",
        }
    except FileNotFoundError:
        return {
            "passed": [],
            "failed": ["setup_error"],
            "errors": {"setup_error": "npx/jest not found — ensure Node.js 18+ is installed"},
            "all_passed": False,
            "test_file": test_file,
            "output": "Jest not found",
        }
    finally:
        try:
            if os.path.exists(results_path):
                os.remove(results_path)
        except Exception:
            pass


def _parse_jest_output(results_file: str, test_file: str, stderr: str) -> dict:
    """Parse Jest --json output file into the unified result dict."""
    passed = []
    failed = []
    errors = {}
    raw_output = ""
    name_map = {}  # safe_name → original test title (used for surgical removal)

    if os.path.exists(results_file):
        try:
            with open(results_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for suite in data.get("testResults", []):
                for assertion in suite.get("assertionResults", []):
                    full_name = assertion.get("fullName") or assertion.get("title", "unknown_test")
                    title = assertion.get("title", full_name)  # just the test() argument, no describe prefix
                    # Normalise to identifier-safe name for display
                    safe_name = full_name.replace(" ", "_").replace("/", "_")[:80]
                    name_map[safe_name] = title
                    status = assertion.get("status", "")
                    if status == "passed":
                        passed.append(safe_name)
                    else:
                        failed.append(safe_name)
                        msgs = assertion.get("failureMessages", [])
                        errors[safe_name] = msgs[0][:400] if msgs else "Unknown error"

            raw_output = str(data.get("numPassedTests", "")) + " passed"
        except Exception as e:
            errors["parse_error"] = str(e)
    else:
        # No results file — likely a setup or syntax error
        if stderr:
            errors["jest_error"] = stderr[:600]
            failed.append("setup_error")
        raw_output = stderr

    all_passed = len(failed) == 0 and len(passed) > 0

    return {
        "passed": list(dict.fromkeys(passed)),
        "failed": list(dict.fromkeys(failed)),
        "errors": errors,
        "all_passed": all_passed,
        "test_file": test_file,
        "output": raw_output,
        "name_map": name_map,   # safe_name → original title, used for surgical test removal
    }
