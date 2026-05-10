"""
parser.py — AST-Based Function Extractor
Reads a Python source file and extracts all function metadata using the ast module.
"""
import ast
import textwrap
from pathlib import Path


def extract_functions(file_path: str) -> list:
    """
    Parse a Python file and extract all top-level and class-level functions.

    Returns a list of dicts, each containing:
        name, args, type_hints, return_type, docstring, source_code, start_line, end_line
    Returns empty list with error key on failure.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except FileNotFoundError:
        return [{"error": f"File not found: {file_path}"}]
    except Exception as e:
        return [{"error": str(e)}]

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{"error": f"SyntaxError: {e}"}]

    source_lines = source.splitlines()
    functions = []

    class FunctionVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            functions.append(_build_meta(node, source_lines))
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            functions.append(_build_meta(node, source_lines))
            self.generic_visit(node)

    FunctionVisitor().visit(tree)
    return functions


def _build_meta(node: ast.FunctionDef, source_lines: list) -> dict:
    """Build a metadata dict from an AST function node."""
    # Argument names
    args = [arg.arg for arg in node.args.args]

    # Type hints for arguments
    type_hints = {}
    for arg in node.args.args:
        if arg.annotation:
            try:
                type_hints[arg.arg] = ast.unparse(arg.annotation)
            except Exception:
                pass

    # Return type
    return_type = ""
    if node.returns:
        try:
            return_type = ast.unparse(node.returns)
        except Exception:
            pass

    # Docstring
    docstring = ""
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        docstring = node.body[0].value.value

    # Source code (dedented)
    start_line = node.lineno
    end_line = node.end_lineno or node.lineno
    raw_lines = source_lines[start_line - 1 : end_line]
    source_code = textwrap.dedent("\n".join(raw_lines))

    return {
        "name": node.name,
        "args": args,
        "type_hints": type_hints,
        "return_type": return_type,
        "docstring": docstring,
        "source_code": source_code,
        "start_line": start_line,
        "end_line": end_line,
    }
