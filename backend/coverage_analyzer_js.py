"""
coverage_analyzer_js.py — Istanbul/nyc Coverage Analyzer for JavaScript
Runs Jest with --coverage and parses Istanbul's coverage-final.json.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "60"))

# On Windows, subprocess cannot resolve bare 'npx' — must use 'npx.cmd'
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"
TEST_OUTPUT_DIR = os.getenv("TEST_OUTPUT_DIR", "generated_tests")


def measure_js_coverage(test_file: str, source_file: str) -> dict:
    """
    Run Jest with --coverage on test_file and parse Istanbul's JSON report.

    Returns:
        {
            percentage: float,
            covered_lines: list[int],
            uncovered_lines: list[int],
            total_lines: int,
        }
    """
    test_dir = str(Path(test_file).parent)
    test_filename = Path(test_file).name
    coverage_dir = os.path.join(test_dir, "coverage")
    coverage_json = os.path.join(coverage_dir, "coverage-final.json")

    try:
        subprocess.run(
            [
                _NPX, "jest",
                test_filename,
                "--coverage",
                "--coverageDirectory=coverage",
                "--coverageReporters=json",
                "--forceExit",
                "--silent",
            ],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            cwd=test_dir,
        )

        if os.path.exists(coverage_json):
            with open(coverage_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = _parse_istanbul_json(data, source_file)
        else:
            result = {
                "percentage": 0.0,
                "covered_lines": [],
                "uncovered_lines": [],
                "total_lines": 0,
                "error": "Istanbul coverage-final.json not generated — ensure Jest 29+ is installed",
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
        # Clean up coverage directory
        try:
            if os.path.exists(coverage_dir):
                shutil.rmtree(coverage_dir, ignore_errors=True)
        except Exception:
            pass

    return result


def _parse_istanbul_json(data: dict, source_file: str) -> dict:
    """Parse Istanbul coverage-final.json to extract line coverage for the source file."""
    source_name = Path(source_file).name
    source_stem = Path(source_file).stem

    target = None
    for key in data:
        k_path = Path(key)
        if k_path.name == source_name or k_path.stem == source_stem:
            target = data[key]
            break

    if target is None and data:
        # Fall back to the first (and likely only) entry
        target = next(iter(data.values()))

    if target is None:
        return {
            "percentage": 0.0,
            "covered_lines": [],
            "uncovered_lines": [],
            "total_lines": 0,
        }

    # Istanbul's `s` = statement map, `statementMap` = line positions
    statements = target.get("s", {})
    statement_map = target.get("statementMap", {})

    covered_lines = set()
    uncovered_lines = set()

    for stmt_id, count in statements.items():
        location = statement_map.get(str(stmt_id), {})
        start = location.get("start", {})
        line = start.get("line")
        if line is None:
            continue
        if count > 0:
            covered_lines.add(line)
        else:
            uncovered_lines.add(line)

    # Lines covered AND uncovered — uncovered only if never covered
    uncovered_lines -= covered_lines
    total = len(covered_lines) + len(uncovered_lines)
    pct = (len(covered_lines) / total * 100) if total > 0 else 0.0

    return {
        "percentage": round(pct, 2),
        "covered_lines": sorted(covered_lines),
        "uncovered_lines": sorted(uncovered_lines),
        "total_lines": total,
    }


def get_js_uncovered_lines(coverage_data: dict) -> list:
    """Return the list of uncovered line numbers from a JS coverage result dict."""
    return coverage_data.get("uncovered_lines", [])
