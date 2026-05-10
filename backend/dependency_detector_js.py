"""
dependency_detector_js.py — JavaScript Import Dependency Graph Builder
Uses regex to detect require() / import dependencies between JS files.
"""
import re
from collections import defaultdict, deque
from pathlib import Path

# Patterns for relative imports only (starts with ./ or ../)
_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"](\.[^'"]+)['"]\s*\)""")
_IMPORT_RE = re.compile(r"""import\s+.*?from\s+['"](\.[^'"]+)['"]""")


def extract_js_imports(file_path: str) -> list:
    """
    Read a .js file and return the local module names it imports via
    require() or ES import statements (relative paths only).

    Strips ./ prefix and .js/.mjs extensions.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return []

    raw_paths = []
    raw_paths.extend(_REQUIRE_RE.findall(content))
    raw_paths.extend(_IMPORT_RE.findall(content))

    names = []
    for p in raw_paths:
        # Strip leading ./ or ../
        stem = Path(p).stem
        names.append(stem)

    return list(set(names))


def build_js_dependency_graph(file_paths: list) -> dict:
    """
    Build adjacency dict: graph[module] = [modules it depends on].
    Only considers files present in file_paths.
    """
    module_map = {Path(fp).stem: fp for fp in file_paths}
    available = set(module_map.keys())
    graph = {}

    for name, fp in module_map.items():
        raw_deps = extract_js_imports(fp)
        deps = [d for d in raw_deps if d in available and d != name]
        graph[name] = deps

    return graph


def topological_sort_js(graph: dict) -> list:
    """
    Kahn's algorithm — same logic as the Python version.
    graph[A] = [B] means A depends on B → B is processed first.
    """
    comes_before: dict = defaultdict(list)
    in_degree: dict = {node: 0 for node in graph}

    for node, deps in graph.items():
        for dep in deps:
            comes_before[dep].append(node)
            in_degree[node] += 1

    queue = deque(n for n, d in in_degree.items() if d == 0)
    result = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for dependent in comes_before[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(graph):
        raise ValueError("Circular dependency detected among uploaded JavaScript files")

    return result
