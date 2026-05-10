"""
generator.py — LLM-Based Test Case Generator
Builds prompts from function metadata and calls the Claude API (Anthropic) to generate pytest tests.
"""
import ast
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None

MAX_TESTS_PER_FUNCTION = int(os.getenv("MAX_TESTS_PER_FUNCTION", "3"))


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set in .env")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _model() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_prompt(function_meta: dict, module_name: str = "") -> str:
    """Build the initial test generation prompt for a single function."""
    name = function_meta["name"]
    args = ", ".join(function_meta.get("args", []))
    type_hints = function_meta.get("type_hints", {})
    return_type = function_meta.get("return_type", "")
    docstring = function_meta.get("docstring", "")
    source_code = function_meta["source_code"]

    hints_str = (
        ", ".join(f"{k}: {v}" for k, v in type_hints.items())
        if type_hints
        else "none"
    )

    return f"""Generate exactly {MAX_TESTS_PER_FUNCTION} pytest unit tests for the following Python function.

Function name: {name}
Arguments: {args}
Type hints: {hints_str}
Return type: {return_type if return_type else "not specified"}
Docstring: {docstring if docstring else "none"}

Source code (shown for reference ONLY — do NOT copy it into your output):
```python
{source_code}
```

CRITICAL RULES — violating any of these will break coverage measurement:
1. Return ONLY test function definitions — no prose, no markdown fences.
2. Write EXACTLY {MAX_TESTS_PER_FUNCTION} test functions — no more, no fewer.
   Prioritise: 1 happy path, 1 edge case, 1 exception (if the function raises one).
3. Do NOT copy, redefine, or re-implement any function from the source code.
   The function already exists in the module; your tests must CALL it, not redefine it.
4. Import statements: you MAY include stdlib imports (e.g. from unittest.mock import MagicMock,
   import types, import os) if your tests need them. Do NOT import pytest or any source module —
   the harness adds those automatically.
5. Every test function name must start with test_.
6. Use monkeypatch to mock builtins (input, random, etc.) when the function uses them.
7. EXCEPTION HANDLING — this is mandatory:
   - If the function raises an exception for invalid inputs, use pytest.raises():
       def test_empty_input():
           with pytest.raises(ValueError):
               some_function("")
   - NEVER call a function that raises an exception without wrapping it in pytest.raises().
8. Do NOT define pytest fixtures or helper classes that re-implement source classes.
   The source classes are already imported via `from {module_name} import *`.
   Instantiate them directly: obj = ClassName(args) — no fixtures needed.
   Never use types.ModuleType, exec(), or compile() to recreate a source class.
9. Do NOT use MagicMock() for objects that can be instantiated from an imported class.
   MagicMock is ONLY for external I/O you cannot import (databases, HTTP, file handles).
10. Do NOT write tests that scan sys.modules or inspect module internals at runtime.
"""


def build_fix_prompt(function_meta: dict, test_code: str, error: str) -> str:
    """Build a prompt to fix failing tests given the pytest error output."""
    source_code = function_meta["source_code"]

    return f"""The pytest test code below has errors. Fix it so all tests pass.

Original function source (for reference — do NOT copy or redefine it):
```python
{source_code}
```

Current (broken) test code:
```python
{test_code}
```

pytest error output:
```
{error[:2000]}
```

CRITICAL RULES:
- Return the COMPLETE corrected test file — no prose, no markdown fences.
- PRESERVE every import line exactly as-is from the broken test code (import pytest, from X import *, etc.).
  Do NOT remove, change, or add import lines — the import header must stay identical.
- Do NOT redefine or copy any function from the source.
- If a test fails with ValueError/TypeError/etc., wrap that call in pytest.raises():
    with pytest.raises(ValueError):
        function_under_test(bad_input)
- Fix every error shown in the pytest output.
- Maintain full test coverage of the original function.
"""


def build_coverage_prompt(function_meta: dict, uncovered_lines: list) -> str:
    """Build a prompt targeting specific uncovered lines for additional test generation."""
    name = function_meta["name"]
    source_code = function_meta["source_code"]
    start_line = function_meta.get("start_line", 1)

    source_lines = source_code.splitlines()
    uncovered_content = []
    for line_num in uncovered_lines:
        relative = line_num - start_line
        if 0 <= relative < len(source_lines):
            uncovered_content.append(
                f"  Line {line_num}: {source_lines[relative].strip()}"
            )

    uncovered_str = "\n".join(uncovered_content) if uncovered_content else str(uncovered_lines)

    return f"""The existing tests do not cover all lines of the function '{name}'.
Generate at most 2 ADDITIONAL pytest test functions targeting the uncovered lines below.

Function source:
```python
{source_code}
```

Uncovered lines that need coverage:
{uncovered_str}

Requirements:
- Return ONLY new test functions as valid Python code — no prose, no markdown fences.
- Write at most 2 new test functions — only what is needed to hit the uncovered lines.
- Do NOT include any import statements.
- Each test function must start with test_.
- Focus on the code paths that reach the uncovered lines.
"""


# ---------------------------------------------------------------------------
# JavaScript prompt builders
# ---------------------------------------------------------------------------

def build_js_prompt(function_meta: dict, module_name: str = "") -> str:
    """Build the initial Jest test generation prompt for a JS function."""
    name = function_meta["name"]
    args = ", ".join(function_meta.get("args", []))
    source_code = function_meta.get("source_code", "")
    export_type = function_meta.get("export_type", "none")

    if export_type == "esmodule":
        import_line = f"import {{ {name} }} from './{module_name or name}';"
    else:
        import_line = f"const {{ {name} }} = require('./{module_name or name}');"

    return f"""You are an expert JavaScript test engineer using Jest.
Generate exactly {MAX_TESTS_PER_FUNCTION} Jest test cases for the following function.

Function name: {name}
Parameters: {args}
The function is already imported via: {import_line}

Source code (for reference — do NOT copy or redefine it):
```javascript
{source_code}
```

CRITICAL RULES — return ONLY the describe()/test() blocks, nothing else:
1. Write EXACTLY {MAX_TESTS_PER_FUNCTION} test() blocks — no more, no fewer.
   Prioritise: 1 happy path, 1 edge case, 1 error scenario (if applicable).
2. Do NOT include any require() or import statements — they are added by the test harness.
3. Do NOT copy, redefine, or re-implement the function — it is already imported.
4. Use describe('{name}', () => {{ ... }}) containing test()/it() blocks with expect() assertions.
5. Use jest.fn() or manual mocks only when strictly necessary.
6. Return ONLY raw JavaScript test code — no prose, no markdown fences, no backticks.
"""


def build_js_fix_prompt(function_meta: dict, test_code: str, error: str) -> str:
    """Build a prompt to fix failing Jest tests."""
    source_code = function_meta.get("source_code", "")

    return f"""The following Jest test has errors. Fix it so all tests pass.

Original source (do NOT copy or redefine):
```javascript
{source_code}
```

Current (broken) test:
```javascript
{test_code}
```

Jest error output:
```
{error[:2000]}
```

CRITICAL RULES:
- Return ONLY the corrected JavaScript code — no prose, no markdown fences, no backticks.
- ALWAYS use require() — NEVER use import/export syntax (Jest runs in CommonJS mode).
- Preserve the require() lines at the top of the test file.
- Do NOT redefine the function — call it via require().
- Fix every error shown in the Jest output.
"""


def build_js_module_prompt(functions: list, module_name: str, full_source: str) -> str:
    """
    Build a Jest test prompt for an entire module at once.
    Passes the full source so the LLM can see the real export pattern
    (plain functions, class, module.exports object, etc.) and generate
    correct imports without guessing.
    """
    func_names = ", ".join(f["name"] for f in functions)

    total_tests = len(functions) * MAX_TESTS_PER_FUNCTION

    return f"""You are an expert JavaScript test engineer using Jest (CommonJS environment).
Generate a complete, runnable Jest test file for the module '{module_name}'.
Write exactly {MAX_TESTS_PER_FUNCTION} test() blocks per function — {total_tests} tests total.

Full module source:
```javascript
{full_source}
```

Functions/methods to test: {func_names}

CRITICAL RULES — read the source carefully before writing:
1. Write EXACTLY {MAX_TESTS_PER_FUNCTION} test() blocks per function ({total_tests} total) — no more.
   Per function: 1 happy path, 1 edge case, 1 error scenario (if the function can throw/return error).
2. ALWAYS use require() — NEVER use import/export syntax (Jest runs in CommonJS mode, import will crash).
   - Exported class:     const ClassName = require('./{module_name}');  then  new ClassName()
   - Exported object:    const {{ fn1, fn2 }} = require('./{module_name}');
   - Default function:   const fn = require('./{module_name}');
3. Do NOT copy, redefine, or re-implement anything from the source.
4. Use describe() blocks per function/method and test()/it() with expect() assertions.
5. Return ONLY raw JavaScript code — no prose, no markdown fences, no backticks.
"""


def build_js_coverage_prompt(function_meta: dict, uncovered_lines: list, file_name: str = "") -> str:
    """Build a prompt to generate additional Jest tests targeting uncovered lines."""
    name = function_meta["name"]
    source_code = function_meta.get("source_code", "")
    start_line = function_meta.get("start_line", 1)

    source_lines = source_code.splitlines()
    uncovered_content = []
    for ln in uncovered_lines:
        rel = ln - start_line
        if 0 <= rel < len(source_lines):
            uncovered_content.append(f"  Line {ln}: {source_lines[rel].strip()}")

    uncovered_str = "\n".join(uncovered_content) if uncovered_content else str(uncovered_lines)

    return f"""The existing Jest tests do not cover all lines of '{name}' in {file_name or 'the source file'}.
Generate at most 2 ADDITIONAL Jest test cases targeting the uncovered lines below.
Return ONLY valid JavaScript test code that can be appended to the existing test file.

Function source:
```javascript
{source_code}
```

Uncovered lines:
{uncovered_str}

CRITICAL RULES:
- Write at most 2 new test()/it() blocks — only what is needed to hit the uncovered lines.
- Return ONLY new test()/it() or describe() blocks — no require/import lines.
- NEVER use import/export syntax — Jest runs in CommonJS mode.
- Do NOT redefine the function.
- Focus on the code paths leading to the uncovered lines.
- Return ONLY JavaScript code — no prose, no markdown fences, no backticks.
"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm(prompt: str) -> str:
    """Send a prompt to Claude and return the raw text response."""
    client = _get_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=(
            "You are an expert software test engineer. "
            "Return ONLY raw executable code — no markdown fences, no backticks, "
            "no explanations, no prose. The output is written directly to a file and executed."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def extract_code(llm_response: str) -> str:
    """Strip markdown code fences (any language) and return pure code."""
    # (?:\w+)? consumes any language tag (python, javascript, js, etc.)
    # so the tag never leaks into the captured group
    matches = re.findall(r"```(?:\w+)?\n?(.*?)```", llm_response, re.DOTALL)
    if matches:
        return "\n\n".join(m.strip() for m in matches)
    return llm_response.strip()


# ---------------------------------------------------------------------------
# Prose scrubber — removes explanatory text mixed into generated code
# ---------------------------------------------------------------------------

_PY_CODE_STARTS = (
    'def ', 'class ', 'import ', 'from ', '@', '#',
    'if ', 'elif ', 'else:', 'for ', 'while ', 'with ',
    'try:', 'except', 'finally:', 'async ', 'return ',
    'raise ', 'assert ', 'pass', 'yield ',
)

_JS_CODE_STARTS = (
    'const ', 'let ', 'var ', 'function ', 'class ',
    'describe(', 'test(', 'it(', 'expect(', 'require(',
    'module.', 'exports.', '//', '/*', '* ',
    'beforeEach', 'afterEach', 'beforeAll', 'afterAll',
)


def scrub_prose(code: str, lang: str = "python") -> str:
    """
    Remove leading/trailing prose lines that the LLM injected around code.
    For Python, also verifies the result parses; falls back to the raw input if not.
    Applied after extract_code so both fence-wrapped and bare LLM output is cleaned.
    """
    if lang == "python":
        return _scrub_prose_python(code)
    return _scrub_prose_js(code)


def _scrub_prose_python(code: str) -> str:
    try:
        ast.parse(code)
        return code  # already clean — fast path
    except SyntaxError:
        pass

    lines = code.splitlines()

    # Find first line that looks like Python code
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s and (any(s.startswith(p) for p in _PY_CODE_STARTS)
                  or line[:1] in (' ', '\t')):
            start = i
            break

    # Walk backward; strip trailing prose sentences (unindented, no syntax chars)
    end = len(lines)
    for i in range(len(lines) - 1, start - 1, -1):
        s = lines[i].strip()
        if not s:
            continue
        is_prose = (
            lines[i][:1] not in (' ', '\t')
            and not any(s.startswith(p) for p in _PY_CODE_STARTS)
            and ' ' in s
            and not any(c in s for c in ':=()[]{}#@\'"')
        )
        if is_prose:
            end = i
        else:
            break

    trimmed = '\n'.join(lines[start:end]).strip()
    try:
        ast.parse(trimmed)
        return trimmed
    except SyntaxError:
        return code  # best-effort failed; return original for downstream handling


def _scrub_prose_js(code: str) -> str:
    lines = code.splitlines()

    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s and (any(s.startswith(p) for p in _JS_CODE_STARTS)
                  or line[:1] in (' ', '\t')):
            start = i
            break

    end = len(lines)
    for i in range(len(lines) - 1, start - 1, -1):
        s = lines[i].strip()
        if not s:
            continue
        is_prose = (
            lines[i][:1] not in (' ', '\t')
            and not any(s.startswith(p) for p in _JS_CODE_STARTS)
            and ' ' in s
            and not any(c in s for c in ':=()[]{}/#\'"')
        )
        if is_prose:
            end = i
        else:
            break

    return '\n'.join(lines[start:end]).strip()
