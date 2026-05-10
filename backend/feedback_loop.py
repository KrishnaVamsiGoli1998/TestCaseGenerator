"""
feedback_loop.py — Main Pipeline Orchestrator
Routes to Python or JavaScript pipeline; supports single-file and multi-file uploads.

Loop A: Iterative error-fix loop (test failures → re-prompt LLM)
Loop B: Coverage-guided re-prompting loop (uncovered lines → re-prompt LLM)
"""
import ast
import os
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from . import generator
from . import parser as py_parser
from . import runner as py_runner
from . import coverage_analyzer as py_coverage
from . import parser_js, runner_js, coverage_analyzer_js
from . import dependency_detector, dependency_detector_js
from . import environment_builder
from .utils import sanitize_module_name

load_dotenv()

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))
MAX_COVERAGE_ITERATIONS = int(os.getenv("MAX_COVERAGE_ITERATIONS", "3"))
COVERAGE_THRESHOLD = float(os.getenv("COVERAGE_THRESHOLD", "80"))
MAX_FILES = int(os.getenv("MAX_FILES_PER_SESSION", "10"))


# ── Public entry point ────────────────────────────────────────────────────────

def run_pipeline(file_paths, language: str = "python") -> dict:
    """
    Run the full AI test generation pipeline.

    Args:
        file_paths: str (single file, backward compat) or list[str]
        language:   'python' or 'javascript'

    Returns the results dict (§5.3 of SRS).
    """
    if isinstance(file_paths, str):
        file_paths = [file_paths]

    if language == "javascript":
        return _run_js_pipeline(file_paths)
    else:
        return _run_python_pipeline(file_paths)


# ── Python Pipeline ───────────────────────────────────────────────────────────

def _run_python_pipeline(file_paths: list) -> dict:
    start_time = time.time()
    iteration_log = []
    temp_dir = ""

    try:
        # ── Step 1: Dependency ordering ─────────────────────────────────────
        classification = None
        if len(file_paths) > 1:
            graph = dependency_detector.build_dependency_graph(file_paths)
            try:
                ordered_names = dependency_detector.topological_sort(graph)
            except ValueError as e:
                return _error_result(file_paths, start_time, str(e), "python")

            path_map = {Path(fp).stem: fp for fp in file_paths}
            ordered_files = [path_map[n] for n in ordered_names if n in path_map]

            # Classify files as dependent vs standalone based on the dep graph
            file_stems   = [Path(fp).stem for fp in ordered_files]
            cls_stems    = _classify_files(file_stems, graph)
            stem_to_name = {Path(fp).stem: Path(fp).name for fp in ordered_files}
            classification = {
                "standalone": [stem_to_name.get(s, s + ".py") for s in cls_stems["standalone"]],
                "dependent":  [stem_to_name.get(s, s + ".py") for s in cls_stems["dependent"]],
            }
        else:
            ordered_files = file_paths

        # ── Step 2: Extract functions from all files (in dependency order) ──
        all_functions = []        # list of (func_meta, module_name)
        raw_module_metadata = {}  # raw_stem → [names], used for per-module dep context
        for fp in ordered_files:
            raw_stem    = Path(fp).stem
            module_name = sanitize_module_name(raw_stem)
            funcs = py_parser.extract_functions(fp)
            funcs = [f for f in funcs if "error" not in f]
            raw_module_metadata[raw_stem] = [f["name"] for f in funcs]
            for fn in funcs:
                all_functions.append((fn, module_name))

        if not all_functions:
            return _error_result(file_paths, start_time, "No valid functions found in any uploaded file", "python")

        # ── Per-module filtered dep context ──────────────────────────────────
        # Each file only receives context for the modules it actually imports,
        # preventing the LLM from hallucinating cross-imports for standalone files.
        per_module_dep_context: dict = {}
        if len(file_paths) > 1:
            for fp in ordered_files:
                raw_stem  = Path(fp).stem
                san_stem  = sanitize_module_name(raw_stem)
                file_deps = graph.get(raw_stem, [])
                if file_deps:
                    ctx_parts = [
                        f"- {dep}.py contains: {', '.join(raw_module_metadata.get(dep, []))}"
                        for dep in file_deps if raw_module_metadata.get(dep)
                    ]
                    if ctx_parts:
                        per_module_dep_context[san_stem] = "\n".join(ctx_parts)

        # ── Step 3: Generate initial tests ──────────────────────────────────
        header_imports = _build_python_header(ordered_files)
        source_modules = {sanitize_module_name(Path(fp).stem) for fp in ordered_files}
        test_parts = []

        for func_meta, module_name in all_functions:
            prompt  = generator.build_prompt(func_meta, module_name=module_name)
            file_ctx = per_module_dep_context.get(module_name, "")
            if file_ctx:
                prompt += (
                    f"\n\nIMPORTANT — exact class/function names available in modules that {module_name} imports:\n"
                    f"{file_ctx}\n"
                    f"Use ONLY these names. Do NOT invent or assume any class or function name "
                    f"not listed above (e.g. do not use ProductCatalog, UserSystem, AdminManager "
                    f"or any other name unless it appears in the list). Call them directly — do not mock them.\n"
                )
            raw = generator.call_llm(prompt)
            code = generator.scrub_prose(generator.extract_code(raw), "python")
            clean = _strip_python_imports(code, source_modules)
            if _is_valid_python(clean):
                test_parts.append(clean)

        combined_code = header_imports + "\n\n".join(test_parts)

        # ── Step 4: Build environment + run initial tests ────────────────────
        temp_dir = ""
        if len(file_paths) > 1:
            temp_dir = environment_builder._make_temp_dir()
            test_file = environment_builder.build_environment(ordered_files, combined_code, temp_dir)
            run_result = _run_pytest_in_dir(Path(test_file).name, temp_dir, test_file)
        else:
            run_result = py_runner.run_tests(combined_code, ordered_files[0])
            test_file = run_result.get("test_file", "")

        # ── Step 5: Loop A — Iterative Error Fix ────────────────────────────
        iterations_fix = 0
        for iteration in range(1, MAX_ITERATIONS + 1):
            if run_result["all_passed"]:
                break

            iterations_fix = iteration
            errors = run_result.get("errors", {})
            error_detail = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])

            # Detect collection errors (import/syntax failure — fixing makes it worse).
            # Regenerate tests from scratch rather than patching broken code.
            is_collection_error = (
                "collection_error" in run_result.get("failed", [])
                or "collected 0 items" in run_result.get("output", "")
                or "ERROR collecting" in run_result.get("output", "")
            )

            if is_collection_error:
                # Re-run the initial generation pipeline fresh; skip any syntactically broken part
                test_parts = []
                for func_meta, module_name in all_functions:
                    prompt = generator.build_prompt(func_meta, module_name=module_name)
                    raw = generator.call_llm(prompt)
                    code = generator.scrub_prose(generator.extract_code(raw), "python")
                    clean = _strip_python_imports(code, source_modules)
                    if _is_valid_python(clean):
                        test_parts.append(clean)
                fixed_code = header_imports + "\n\n".join(test_parts)
                iteration_log.append({
                    "iteration": iteration, "event": "collection_error",
                    "detail": "pytest could not import test module — regenerating from scratch",
                    "action": "regenerated",
                })
            else:
                try:
                    with open(test_file, "r", encoding="utf-8") as f:
                        current_code = f.read()
                except Exception:
                    current_code = combined_code

                all_source = "\n\n".join(fn["source_code"] for fn, _ in all_functions)
                combined_meta = {"name": "project", "args": [], "type_hints": {}, "return_type": "",
                                 "docstring": "", "source_code": all_source, "start_line": 1, "end_line": 9999}
                error_str = run_result.get("output", "") + "\n" + str(errors)
                fix_prompt = generator.build_fix_prompt(combined_meta, current_code, error_str[:2000])
                candidate = generator.scrub_prose(
                    generator.extract_code(generator.call_llm(fix_prompt)), "python"
                )
                fixed_code = candidate if _is_valid_python(candidate) else current_code
                iteration_log.append({
                    "iteration": iteration, "event": "test_failed",
                    "detail": error_detail[:200], "action": "regenerated",
                })

            if len(file_paths) > 1:
                test_file = environment_builder.build_environment(ordered_files, fixed_code, temp_dir)
                run_result = _run_pytest_in_dir(Path(test_file).name, temp_dir, test_file)
            else:
                run_result = py_runner.run_tests(fixed_code, ordered_files[0])
                test_file = run_result.get("test_file", test_file)

        # ── Step 6: Coverage measurement ─────────────────────────────────────
        if len(file_paths) > 1:
            coverage_data = _measure_python_coverage_dir(test_file, temp_dir, ordered_files)
        else:
            coverage_data = py_coverage.measure_coverage(test_file, ordered_files[0])
        coverage_pct = coverage_data.get("percentage", 0.0)

        # ── Step 6b: Prune stubborn failing tests ────────────────────────────────
        # If Loop A exhausted all iterations yet some tests still fail, remove the
        # unfixable tests so the final file is failure-free.  Loop B then handles
        # any coverage gap that results from the removal.
        # Guard: at least one passing test must remain (prevents empty file).
        _system_errors = {"collection_error", "timeout_error", "setup_error", "import_error"}
        if (
            not run_result["all_passed"]
            and run_result.get("passed")           # at least one passing test remains
        ):
            stubborn = [f for f in run_result.get("failed", []) if f not in _system_errors]
            if stubborn:
                try:
                    with open(test_file, "r", encoding="utf-8") as _f:
                        _current = _f.read()
                except Exception:
                    _current = combined_code

                pruned = _remove_failing_tests_python(_current, set(stubborn))

                if len(file_paths) > 1:
                    test_file = environment_builder.build_environment(ordered_files, pruned, temp_dir)
                    run_result = _run_pytest_in_dir(Path(test_file).name, temp_dir, test_file)
                    coverage_data = _measure_python_coverage_dir(test_file, temp_dir, ordered_files)
                else:
                    run_result = py_runner.run_tests(pruned, ordered_files[0])
                    test_file = run_result.get("test_file", test_file)
                    coverage_data = py_coverage.measure_coverage(test_file, ordered_files[0])

                coverage_pct = coverage_data.get("percentage", coverage_pct)
                iteration_log.append({
                    "iteration": iterations_fix + 1,
                    "event": "tests_pruned",
                    "detail": f"Removed {len(stubborn)} unfixable test(s): {', '.join(stubborn[:3])}",
                    "action": f"test file is now failure-free · coverage {coverage_pct:.1f}%",
                })

        # ── Step 7: Loop B — Coverage Gap Loop ──────────────────────────────
        # Only run when all tests pass — adding more tests to a broken file
        # cascades failures and drops coverage further (as seen in practice).
        iterations_cov = 0
        for cov_iter in range(1, MAX_COVERAGE_ITERATIONS + 1):
            if coverage_pct >= COVERAGE_THRESHOLD:
                break
            if not run_result.get("all_passed"):
                break  # still failures present — augmenting will make things worse
            uncovered = py_coverage.get_uncovered_lines(coverage_data)
            if not uncovered:
                break

            iterations_cov = cov_iter
            additional = []
            for func_meta, module_name in all_functions:
                func_uncovered = [ln for ln in uncovered
                                  if func_meta["start_line"] <= ln <= func_meta["end_line"]]
                if not func_uncovered:
                    continue
                cov_prompt = generator.build_coverage_prompt(func_meta, func_uncovered)
                extra = generator.scrub_prose(
                    generator.extract_code(generator.call_llm(cov_prompt)), "python"
                )
                additional.append(_strip_python_imports(extra, source_modules))

            if not additional:
                break

            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    existing = f.read()
                augmented = existing + "\n\n" + "\n\n".join(additional)
            except Exception:
                augmented = combined_code + "\n\n" + "\n\n".join(additional)

            prev_pct = coverage_pct
            if len(file_paths) > 1:
                test_file = environment_builder.build_environment(ordered_files, augmented, temp_dir)
                run_result = _run_pytest_in_dir(Path(test_file).name, temp_dir, test_file)
                coverage_data = _measure_python_coverage_dir(test_file, temp_dir, ordered_files)
            else:
                run_result = py_runner.run_tests(augmented, ordered_files[0])
                test_file = run_result.get("test_file", test_file)
                coverage_data = py_coverage.measure_coverage(test_file, ordered_files[0])
            new_pct = coverage_data.get("percentage", prev_pct)

            if new_pct < prev_pct:
                # Augmented tests hurt coverage — revert to the previous file
                try:
                    with open(test_file, "w", encoding="utf-8") as f:
                        f.write(existing)
                except Exception:
                    pass
                iteration_log.append({
                    "iteration": iterations_fix + cov_iter, "event": "coverage_gap",
                    "detail": f"Coverage was {prev_pct:.1f}% — augmented tests reduced it to {new_pct:.1f}%",
                    "action": f"reverted augmentation · holding at {prev_pct:.1f}%",
                })
                break  # no point trying again

            coverage_pct = new_pct
            iteration_log.append({
                "iteration": iterations_fix + cov_iter, "event": "coverage_gap",
                "detail": f"Coverage was {prev_pct:.1f}%, uncovered: {uncovered[:5]}",
                "action": f"re-prompted — coverage now {coverage_pct:.1f}%",
            })

        # For multi-file: copy test out of temp_dir before cleanup so /download works
        if temp_dir and test_file and os.path.exists(test_file):
            out_dir = os.getenv("TEST_OUTPUT_DIR", "generated_tests")
            os.makedirs(out_dir, exist_ok=True)
            saved = os.path.join(out_dir, Path(test_file).name)
            shutil.copy2(test_file, saved)
            test_file = saved

        return _build_result(file_paths, run_result, coverage_pct, iterations_fix,
                             iterations_cov, iteration_log, test_file, start_time, "python",
                             classification=classification)

    finally:
        if temp_dir:
            environment_builder.cleanup_environment(temp_dir)


# ── JavaScript Pipeline ───────────────────────────────────────────────────────

def _run_js_pipeline(file_paths: list) -> dict:
    start_time = time.time()
    iteration_log = []
    temp_dir = ""

    try:
        # ── Step 1: Dependency ordering ──────────────────────────────────────
        classification = None
        if len(file_paths) > 1:
            graph = dependency_detector_js.build_js_dependency_graph(file_paths)
            try:
                ordered_names = dependency_detector_js.topological_sort_js(graph)
            except ValueError as e:
                return _error_result(file_paths, start_time, str(e), "javascript")
            path_map = {Path(fp).stem: fp for fp in file_paths}
            ordered_files = [path_map[n] for n in ordered_names if n in path_map]

            # Classify files as dependent vs standalone based on the dep graph
            file_stems   = [Path(fp).stem for fp in ordered_files]
            cls_stems    = _classify_files(file_stems, graph)
            stem_to_name = {Path(fp).stem: Path(fp).name for fp in ordered_files}
            classification = {
                "standalone": [stem_to_name.get(s, s + ".js") for s in cls_stems["standalone"]],
                "dependent":  [stem_to_name.get(s, s + ".js") for s in cls_stems["dependent"]],
            }
        else:
            ordered_files = file_paths

        # ── Step 2: Extract functions from all files ──────────────────────────
        all_functions = []
        for fp in ordered_files:
            module_name = Path(fp).stem
            funcs = parser_js.extract_js_functions(fp)
            for fn in funcs:
                all_functions.append((fn, module_name))

        if not all_functions:
            return _error_result(file_paths, start_time, "No functions found in uploaded JavaScript files", "javascript")

        # ── Step 3: Generate initial Jest tests (one prompt per module) ─────────
        # Read full source for each module so the LLM can see the real export style
        module_sources: dict = {}
        for fp in ordered_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    module_sources[Path(fp).stem] = f.read()
            except Exception:
                module_sources[Path(fp).stem] = ""

        # Group extracted functions by their module
        funcs_by_module: dict = defaultdict(list)
        for func_meta, module_name in all_functions:
            funcs_by_module[module_name].append(func_meta)

        test_parts = []
        for fp in ordered_files:
            module_name = Path(fp).stem
            funcs = funcs_by_module.get(module_name, [])
            if not funcs:
                continue
            prompt = generator.build_js_module_prompt(
                funcs, module_name, module_sources.get(module_name, "")
            )
            raw = generator.call_llm(prompt)
            code = generator.scrub_prose(generator.extract_code(raw), "javascript")
            test_parts.append(code)

        combined_code = "\n\n".join(test_parts)

        # ── Step 4: Build environment + run initial tests ─────────────────────
        if len(file_paths) > 1:
            temp_dir = environment_builder._make_temp_dir()
            test_file = environment_builder.build_js_environment(ordered_files, combined_code, temp_dir)
            run_result = runner_js.run_js_tests_in_dir(Path(test_file).name, temp_dir)
        else:
            run_result = runner_js.run_js_tests(combined_code, ordered_files[0])
            test_file = run_result.get("test_file", "")

        # ── Step 5: Loop A — Iterative Error Fix ─────────────────────────────
        iterations_fix = 0
        for iteration in range(1, MAX_ITERATIONS + 1):
            if run_result["all_passed"]:
                break

            iterations_fix = iteration
            errors = run_result.get("errors", {})
            error_detail = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])

            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    current_code = f.read()
            except Exception:
                current_code = combined_code

            all_source = "\n\n".join(fn["source_code"] for fn, _ in all_functions)
            combined_meta = {"name": "project", "args": [], "source_code": all_source,
                             "start_line": 1, "end_line": 9999, "export_type": "none"}
            error_str = run_result.get("output", "") + "\n" + str(errors)
            fix_prompt = generator.build_js_fix_prompt(combined_meta, current_code, error_str[:2000])
            fixed_code = generator.scrub_prose(
                generator.extract_code(generator.call_llm(fix_prompt)), "javascript"
            )

            iteration_log.append({
                "iteration": iteration, "event": "test_failed",
                "detail": error_detail[:200], "action": "regenerated",
            })

            if len(file_paths) > 1:
                test_file = environment_builder.build_js_environment(ordered_files, fixed_code, temp_dir)
                run_result = runner_js.run_js_tests_in_dir(Path(test_file).name, temp_dir)
            else:
                run_result = runner_js.run_js_tests(fixed_code, ordered_files[0])
                test_file = run_result.get("test_file", test_file)

        # ── Step 6: Coverage measurement ──────────────────────────────────────
        coverage_data = coverage_analyzer_js.measure_js_coverage(test_file, ordered_files[0])
        coverage_pct = coverage_data.get("percentage", 0.0)

        # ── Step 6b: Prune stubborn failing tests ────────────────────────────────
        _system_errors = {"collection_error", "timeout_error", "setup_error", "jest_error", "parse_error"}
        if (
            not run_result["all_passed"]
            and run_result.get("passed")
        ):
            stubborn = [f for f in run_result.get("failed", []) if f not in _system_errors]
            if stubborn:
                name_map = run_result.get("name_map", {})
                try:
                    with open(test_file, "r", encoding="utf-8") as _f:
                        _current = _f.read()
                except Exception:
                    _current = combined_code

                pruned = _remove_failing_tests_js(_current, set(stubborn), name_map)

                if len(file_paths) > 1:
                    test_file = environment_builder.build_js_environment(ordered_files, pruned, temp_dir)
                    run_result = runner_js.run_js_tests_in_dir(Path(test_file).name, temp_dir)
                else:
                    run_result = runner_js.run_js_tests(pruned, ordered_files[0])
                    test_file = run_result.get("test_file", test_file)

                coverage_data = coverage_analyzer_js.measure_js_coverage(test_file, ordered_files[0])
                coverage_pct = coverage_data.get("percentage", coverage_pct)
                iteration_log.append({
                    "iteration": iterations_fix + 1,
                    "event": "tests_pruned",
                    "detail": f"Removed {len(stubborn)} unfixable test(s): {', '.join(stubborn[:3])}",
                    "action": f"test file is now failure-free · coverage {coverage_pct:.1f}%",
                })

        # ── Step 7: Loop B — Coverage Gap Loop ───────────────────────────────
        iterations_cov = 0
        for cov_iter in range(1, MAX_COVERAGE_ITERATIONS + 1):
            if coverage_pct >= COVERAGE_THRESHOLD:
                break
            if not run_result.get("all_passed"):
                break  # still failures present — augmenting will make things worse
            uncovered = coverage_analyzer_js.get_js_uncovered_lines(coverage_data)
            if not uncovered:
                break

            iterations_cov = cov_iter
            additional = []
            for func_meta, module_name in all_functions:
                func_uncovered = [ln for ln in uncovered
                                  if func_meta["start_line"] <= ln <= func_meta["end_line"]]
                if not func_uncovered:
                    continue
                cov_prompt = generator.build_js_coverage_prompt(
                    func_meta, func_uncovered,
                    file_name=f"{module_name}.js"
                )
                extra = generator.scrub_prose(
                    generator.extract_code(generator.call_llm(cov_prompt)), "javascript"
                )
                additional.append(_strip_js_imports(extra))

            if not additional:
                break

            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    existing = f.read()
                augmented = existing + "\n\n" + "\n\n".join(additional)
            except Exception:
                augmented = combined_code + "\n\n" + "\n\n".join(additional)

            prev_pct = coverage_pct
            if len(file_paths) > 1:
                test_file = environment_builder.build_js_environment(ordered_files, augmented, temp_dir)
                run_result = runner_js.run_js_tests_in_dir(Path(test_file).name, temp_dir)
            else:
                run_result = runner_js.run_js_tests(augmented, ordered_files[0])
                test_file = run_result.get("test_file", test_file)

            coverage_data = coverage_analyzer_js.measure_js_coverage(test_file, ordered_files[0])
            new_pct = coverage_data.get("percentage", prev_pct)

            if new_pct < prev_pct:
                # Augmented tests hurt coverage — revert to the previous file
                try:
                    with open(test_file, "w", encoding="utf-8") as f:
                        f.write(existing)
                except Exception:
                    pass
                iteration_log.append({
                    "iteration": iterations_fix + cov_iter, "event": "coverage_gap",
                    "detail": f"Coverage was {prev_pct:.1f}% — augmented tests reduced it to {new_pct:.1f}%",
                    "action": f"reverted augmentation · holding at {prev_pct:.1f}%",
                })
                break

            coverage_pct = new_pct
            iteration_log.append({
                "iteration": iterations_fix + cov_iter, "event": "coverage_gap",
                "detail": f"Coverage was {prev_pct:.1f}%, uncovered: {uncovered[:5]}",
                "action": f"re-prompted — coverage now {coverage_pct:.1f}%",
            })

        # For multi-file: copy test out of temp_dir before cleanup so /download works
        if temp_dir and test_file and os.path.exists(test_file):
            out_dir = os.getenv("TEST_OUTPUT_DIR", "generated_tests")
            os.makedirs(out_dir, exist_ok=True)
            saved = os.path.join(out_dir, Path(test_file).name)
            shutil.copy2(test_file, saved)
            test_file = saved

        return _build_result(file_paths, run_result, coverage_pct, iterations_fix,
                             iterations_cov, iteration_log, test_file, start_time, "javascript",
                             classification=classification)

    finally:
        if temp_dir:
            environment_builder.cleanup_environment(temp_dir)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_python_header(file_paths: list) -> str:
    """Build pytest import header for all uploaded Python modules."""
    lines = ["import pytest"]
    for fp in file_paths:
        module_name = sanitize_module_name(Path(fp).stem)
        lines.append(f"from {module_name} import *")
    return "\n".join(lines) + "\n\n"


def _build_python_dep_context(file_paths: list) -> str:
    """Build dependency context string for multi-file Python prompts."""
    parts = []
    for fp in file_paths:
        funcs = py_parser.extract_functions(fp)
        names = [f["name"] for f in funcs if "error" not in f]
        if names:
            parts.append(f"- {Path(fp).name} contains: {', '.join(names)}")
    return "\n".join(parts)


def _classify_files(file_stems: list, dep_graph: dict) -> dict:
    """
    Classify uploaded files as 'dependent' (part of a dependency chain with other
    uploaded files) or 'standalone' (no import relationship with any other uploaded file).

    A file is standalone when it neither imports nor is imported by any other file
    in the current upload batch.

    Args:
        file_stems: list of raw stem names (e.g. ['models', 'service', 'utils'])
        dep_graph:  {stem: [list of stems it imports]}

    Returns:
        {"standalone": [...stems...], "dependent": [...stems...]}
    """
    standalone = []
    dependent  = []
    for stem in file_stems:
        imports_others     = bool(dep_graph.get(stem, []))
        imported_by_others = any(stem in deps for deps in dep_graph.values())
        if imports_others or imported_by_others:
            dependent.append(stem)
        else:
            standalone.append(stem)
    return {"standalone": standalone, "dependent": dependent}


def _is_valid_python(code: str) -> bool:
    """Return True if code parses without SyntaxError."""
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _strip_python_imports(code: str, source_modules: set = None) -> str:
    """
    Remove pytest and source-module imports from LLM-generated test code.
    Preserves stdlib/third-party imports (unittest.mock, types, os, etc.)
    so LLM-generated module-level fixtures don't cause NameErrors.

    source_modules: set of sanitized module names (e.g. {'user', 'order'}).
    If None, falls back to stripping ALL imports (safe for single-file use).
    """
    lines = code.splitlines()
    result = []
    skip_until_close_paren = False

    for line in lines:
        if skip_until_close_paren:
            if ")" in line:
                skip_until_close_paren = False
            continue

        stripped = line.strip()

        # Always remove pytest imports — the header adds them
        if stripped.startswith("import pytest"):
            if "(" in stripped and ")" not in stripped:
                skip_until_close_paren = True
            continue

        if source_modules is not None:
            # Only strip imports of source modules; keep everything else
            is_source_import = any(
                stripped.startswith(f"from {m} import") or stripped == f"import {m}"
                or stripped.startswith(f"import {m} ")
                for m in source_modules
            )
            if is_source_import:
                if "(" in stripped and ")" not in stripped:
                    skip_until_close_paren = True
                continue
        else:
            # Fallback: strip all imports (single-file path)
            if (stripped.startswith("from ") and "import" in stripped) or stripped.startswith("import "):
                if "(" in stripped and ")" not in stripped:
                    skip_until_close_paren = True
                continue

        result.append(line)

    return "\n".join(result).strip()


def _remove_failing_tests_python(code: str, failing_names: set) -> str:
    """
    Surgically remove named test functions from Python test code using AST.
    Handles decorators (e.g. @pytest.mark.parametrize) by including their lines.
    Returns the original code unchanged if it can't be parsed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    lines = code.splitlines()
    ranges = []  # (start_0idx, end_exclusive_0idx)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in failing_names:
                # Include decorator lines that appear above the def
                start = (
                    node.decorator_list[0].lineno - 1
                    if node.decorator_list
                    else node.lineno - 1
                )
                end = node.end_lineno  # 1-indexed inclusive → exclusive in 0-indexed slice
                ranges.append((start, end))

    # Remove in reverse order so earlier line numbers stay valid
    ranges.sort(reverse=True)
    result = lines[:]
    for start, end in ranges:
        del result[start:end]

    return "\n".join(result).strip()


def _remove_failing_tests_js(code: str, failing_names: set, name_map: dict = None) -> str:
    """
    Surgically remove test()/it() blocks from a Jest test file.
    Uses paren-depth counting (skipping string literals) to find each block's end.
    Returns the original code unchanged if no matching titles are found.
    """
    import re as _re

    # Build the set of titles to search for (original + space variants)
    _system_errors = {"setup_error", "timeout_error", "jest_error", "parse_error", "collection_error"}
    titles: set = set()
    for safe in failing_names:
        if safe in _system_errors:
            continue
        if name_map and safe in name_map:
            titles.add(name_map[safe])          # original title from Jest JSON
        titles.add(safe.replace("_", " "))      # spaces variant
        titles.add(safe)                        # normalised variant as fallback

    if not titles:
        return code

    lines = code.splitlines()
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check whether this line opens a failing test/it block
        is_failing = False
        for title in titles:
            escaped = _re.escape(title)
            if _re.match(rf"""(?:test|it)\s*\(\s*['"`]{escaped}['"`]\s*""", stripped):
                is_failing = True
                break

        if is_failing:
            # Consume the entire test(...) call using paren-depth counting.
            # Skip characters inside string literals so quoted parens don't confuse the count.
            paren_depth = 0
            in_string: str | None = None
            prev_ch = ""
            done = False
            while i < len(lines) and not done:
                for ch in lines[i]:
                    if in_string:
                        if ch == in_string and prev_ch != "\\":
                            in_string = None
                    elif ch in ('"', "'", "`"):
                        in_string = ch
                    elif ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth -= 1
                        if paren_depth == 0:
                            done = True
                            break
                    prev_ch = ch
                i += 1
            continue  # block consumed — do not append to result

        result.append(line)
        i += 1

    return "\n".join(result).strip()


def _strip_js_imports(code: str) -> str:
    return "\n".join(
        line for line in code.splitlines()
        if not (
            line.startswith("const ") and "require(" in line
            or line.startswith("import ")
        )
    ).strip()


def _run_pytest_in_dir(test_filename: str, working_dir: str, test_file_path: str) -> dict:
    """Run pytest inside an existing multi-file temp directory."""
    import subprocess
    timeout = int(os.getenv("TIMEOUT_SECONDS", "30"))
    try:
        result = subprocess.run(
            ["pytest", test_filename, "--tb=short", "-v", "--no-header"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=working_dir,
        )
        # Reuse the existing runner's output parser
        return py_runner._parse_output(result.stdout, result.stderr, test_file_path)
    except subprocess.TimeoutExpired:
        return {"passed": [], "failed": ["timeout_error"],
                "errors": {"timeout_error": f"Tests timed out after {timeout}s"},
                "all_passed": False, "test_file": test_file_path, "output": "TIMEOUT"}
    except FileNotFoundError:
        return {"passed": [], "failed": ["setup_error"],
                "errors": {"setup_error": "pytest not found"},
                "all_passed": False, "test_file": test_file_path, "output": ""}


def _measure_python_coverage_dir(test_file: str, working_dir: str, source_files: list) -> dict:
    """Measure combined coverage for all source files in the temp directory."""
    import subprocess, json
    test_filename = Path(test_file).name
    timeout = int(os.getenv("TIMEOUT_SECONDS", "30"))
    coverage_json = os.path.join(working_dir, "coverage.json")

    try:
        subprocess.run(
            ["pytest", test_filename, "--cov=.", "--cov-report=json", "-q", "--no-header"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=working_dir,
        )

        if os.path.exists(coverage_json):
            with open(coverage_json, "r", encoding="utf-8") as f:
                cov_data = json.load(f)

            totals = cov_data.get("totals", {})
            # Aggregate uncovered lines across all source files
            all_covered, all_uncovered = [], []
            files_data = cov_data.get("files", {})
            for fp in source_files:
                name = sanitize_module_name(Path(fp).stem) + ".py"
                for fpath, fdata in files_data.items():
                    if Path(fpath).name == name or Path(fpath).name == Path(fp).name:
                        all_covered.extend(fdata.get("executed_lines", []))
                        all_uncovered.extend(fdata.get("missing_lines", []))
                        break

            pct = round(totals.get("percent_covered", 0.0), 2)
            return {"percentage": pct, "covered_lines": all_covered,
                    "uncovered_lines": all_uncovered, "total_lines": totals.get("num_statements", 0)}
        else:
            return {"percentage": 0.0, "covered_lines": [], "uncovered_lines": [], "total_lines": 0}
    except Exception:
        return {"percentage": 0.0, "covered_lines": [], "uncovered_lines": [], "total_lines": 0}
    finally:
        try:
            if os.path.exists(coverage_json):
                os.remove(coverage_json)
            cov_db = os.path.join(working_dir, ".coverage")
            if os.path.exists(cov_db):
                os.remove(cov_db)
        except Exception:
            pass


def _build_result(file_paths, run_result, coverage_pct, iterations_fix,
                  iterations_cov, iteration_log, test_file, start_time, language,
                  classification=None) -> dict:
    test_results = []
    for name in run_result.get("passed", []):
        test_results.append({"name": name, "status": "PASSED", "error": None})
    for name in run_result.get("failed", []):
        err = run_result.get("errors", {}).get(name, "Unknown error")
        test_results.append({"name": name, "status": "FAILED", "error": str(err)[:300]})

    total_passed = len(run_result.get("passed", []))
    total_failed = len(run_result.get("failed", []))

    primary_file = Path(file_paths[0]).name if file_paths else "unknown"
    all_files = [Path(fp).name for fp in file_paths]

    return {
        "file_name": primary_file,
        "all_files": all_files,
        "language": language,
        "tests_generated": total_passed + total_failed,
        "tests_passed": total_passed,
        "tests_failed": total_failed,
        "final_coverage": coverage_pct,
        "iterations_taken": max(iterations_fix + iterations_cov, 1),
        "time_taken_seconds": round(time.time() - start_time, 2),
        "test_results": test_results,
        "iteration_log": iteration_log,
        "download_url": f"/download/{Path(test_file).name}" if test_file else None,
        "standalone_files": classification.get("standalone", []) if classification else [],
        "dependent_files":  classification.get("dependent",  []) if classification else [],
    }


def _error_result(file_paths, start_time, error, language) -> dict:
    primary = Path(file_paths[0]).name if file_paths else "unknown"
    return {
        "file_name": primary,
        "all_files": [Path(fp).name for fp in file_paths],
        "language": language,
        "tests_generated": 0, "tests_passed": 0, "tests_failed": 0,
        "final_coverage": 0.0, "iterations_taken": 0,
        "time_taken_seconds": round(time.time() - start_time, 2),
        "test_results": [],
        "iteration_log": [{"iteration": 0, "event": "error", "detail": error, "action": "stopped"}],
        "download_url": None, "error": error,
        "standalone_files": [], "dependent_files": [],
    }
