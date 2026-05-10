"""
coverage_analyzer.py — Code Coverage Measurer
Runs pytest with coverage.py and returns line-level coverage metrics.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from .utils import sanitize_module_name

load_dotenv()

TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))


def measure_coverage(test_file: str, source_file: str) -> dict:
    """
    Run pytest with --cov on test_file and return coverage metrics.

    Returns:
        {
            percentage: float,
            covered_lines: list[int],
            uncovered_lines: list[int],
            total_lines: int,
        }
    """
    # Use the same sanitized name the runner used when copying the source file
    module_name = sanitize_module_name(Path(source_file).stem)
    test_dir = str(Path(test_file).parent)
    test_filename = Path(test_file).name
    coverage_json_path = os.path.join(test_dir, "coverage.json")

    try:
        subprocess.run(
            [
                "pytest",
                test_filename,
                f"--cov={module_name}",
                "--cov-report=json",
                "-q",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            cwd=test_dir,
        )

        if os.path.exists(coverage_json_path):
            with open(coverage_json_path, "r", encoding="utf-8") as f:
                cov_data = json.load(f)
            result = _parse_coverage_json(cov_data, module_name, source_file)
        else:
            result = {
                "percentage": 0.0,
                "covered_lines": [],
                "uncovered_lines": [],
                "total_lines": 0,
                "error": "coverage.json not generated — ensure pytest-cov is installed",
            }

    except subprocess.TimeoutExpired:
        result = {
            "percentage": 0.0,
            "covered_lines": [],
            "uncovered_lines": [],
            "total_lines": 0,
            "error": "Coverage measurement timed out",
        }
    finally:
        # Clean up coverage artefacts
        for artefact in (coverage_json_path, os.path.join(test_dir, ".coverage")):
            try:
                if os.path.exists(artefact):
                    os.remove(artefact)
            except Exception:
                pass

    return result


def _parse_coverage_json(cov_data: dict, module_name: str, source_file: str) -> dict:
    files = cov_data.get("files", {})
    sanitized_py = f"{module_name}.py"  # e.g. "import_random.py"

    target = None
    for file_path, file_data in files.items():
        fp = Path(file_path)
        if module_name in fp.stem or fp.name == sanitized_py or fp.name == Path(source_file).name:
            target = file_data
            break

    if target is None:
        totals = cov_data.get("totals", {})
        return {
            "percentage": round(totals.get("percent_covered", 0.0), 2),
            "covered_lines": [],
            "uncovered_lines": [],
            "total_lines": totals.get("num_statements", 0),
        }

    executed = target.get("executed_lines", [])
    missing = target.get("missing_lines", [])
    total = len(executed) + len(missing)
    pct = (len(executed) / total * 100) if total > 0 else 0.0

    return {
        "percentage": round(pct, 2),
        "covered_lines": sorted(executed),
        "uncovered_lines": sorted(missing),
        "total_lines": total,
    }


def get_uncovered_lines(coverage_data: dict) -> list:
    """Return the list of uncovered line numbers from a coverage result dict."""
    return coverage_data.get("uncovered_lines", [])
