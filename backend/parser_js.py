"""
parser_js.py — Regex-Based JavaScript Function Extractor
Extracts named functions, arrow functions, and function expressions from JS files.
"""
import re
from pathlib import Path


# ── Regex patterns for function declarations ──────────────────────────────────
# Each pattern captures: (name, params_str) and expects the opening { to follow
_PATTERNS = [
    # [export] [async] function foo(a, b) {   — any indentation level
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    ),
    # [export] const foo = [async] (a, b) => {
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>\s*\{",
        re.MULTILINE,
    ),
    # const foo = x => {   (single-param arrow, no parens)
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(\w+)\s*=>\s*\{",
        re.MULTILINE,
    ),
    # [export] const foo = [async] function(a, b) {
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    ),
    # method shorthand inside object/class: foo(a, b) {  (excludes JS keywords)
    re.compile(
        r"^\s+(?!(?:if|for|while|switch|catch|else|try|do|return|typeof|instanceof|new|delete|void|throw|yield|await)\b)([a-zA-Z_$]\w*)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    ),
]


def extract_js_functions(file_path: str) -> list:
    """
    Extract function metadata from a JavaScript file using regex.

    Returns list of dicts with keys:
        name, args, source_code, start_line, end_line, export_type
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return []

    # Detect module.exports = { foo, bar } for CommonJS export tagging
    cjs_exports: set = set()
    cjs_match = re.search(r"module\.exports\s*=\s*\{([^}]*)\}", content)
    if cjs_match:
        for name in cjs_match.group(1).split(","):
            cjs_exports.add(name.strip().split(":")[0].strip())

    raw: list = []
    for pattern in _PATTERNS:
        for m in pattern.finditer(content):
            name = m.group(1)
            params_str = m.group(2).strip()
            args = (
                [p.strip().split("=")[0].strip() for p in params_str.split(",") if p.strip()]
                if params_str
                else []
            )

            # Find the matching closing brace
            try:
                brace_open = content.index("{", m.start())
                brace_close = _find_closing_brace(content, brace_open)
            except (ValueError, IndexError):
                continue

            start_char = m.start()
            end_char = brace_close

            start_line = content[:start_char].count("\n") + 1
            end_line = content[:end_char].count("\n") + 1
            source_code = content[start_char : end_char + 1]

            prefix = content[m.start() : m.start() + 30]
            if "export" in prefix:
                export_type = "esmodule"
            elif name in cjs_exports:
                export_type = "commonjs"
            else:
                export_type = "none"

            raw.append(
                {
                    "name": name,
                    "args": args,
                    "source_code": source_code,
                    "start_line": start_line,
                    "end_line": end_line,
                    "export_type": export_type,
                }
            )

    # Deduplicate (a function can match multiple patterns), keep first by start_line
    seen: set = set()
    result = []
    for fn in sorted(raw, key=lambda x: x["start_line"]):
        if fn["name"] not in seen:
            seen.add(fn["name"])
            result.append(fn)

    return result


def _find_closing_brace(content: str, open_pos: int) -> int:
    """
    Find the index of the closing } that matches the { at open_pos.
    Handles nested braces, strings (single/double/template), and line comments.
    """
    depth = 0
    in_string = False
    string_char = ""
    i = open_pos

    while i < len(content):
        ch = content[i]

        if in_string:
            if ch == "\\" and string_char != "`":
                i += 2  # skip escaped character
                continue
            if ch == string_char:
                in_string = False
        else:
            # Single-line comment
            if ch == "/" and i + 1 < len(content) and content[i + 1] == "/":
                eol = content.find("\n", i)
                i = eol if eol != -1 else len(content)
                continue
            # Block comment
            if ch == "/" and i + 1 < len(content) and content[i + 1] == "*":
                end = content.find("*/", i + 2)
                i = end + 2 if end != -1 else len(content)
                continue
            if ch in ('"', "'", "`"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1

    return len(content) - 1
