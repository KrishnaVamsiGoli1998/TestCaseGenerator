"""
dependency_detector.py — Python Import Dependency Graph Builder
Uses Python's ast module to detect inter-file dependencies and topologically sort them.
"""
import ast
from collections import defaultdict, deque
from pathlib import Path


def extract_local_imports(file_path: str, available_modules: set) -> list:
    """
    Parse a Python file and return the names of locally-available modules it imports.
    Ignores stdlib and third-party packages — only returns names present in available_modules.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
    except Exception:
        return []

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in available_modules:
                    found.append(name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if name in available_modules:
                    found.append(name)

    return list(set(found))


def build_dependency_graph(file_paths: list) -> dict:
    """
    Build an adjacency dict where graph[module] = [list of modules it depends on].

    Example: {'services': ['models'], 'controllers': ['services'], 'models': []}
    """
    # Map sanitized stem → original path
    module_map = {}
    for fp in file_paths:
        stem = Path(fp).stem
        module_map[stem] = fp

    available = set(module_map.keys())
    graph = {}
    for name, fp in module_map.items():
        deps = extract_local_imports(fp, available - {name})
        graph[name] = deps

    return graph


def topological_sort(graph: dict) -> list:
    """
    Kahn's algorithm topological sort.
    graph[A] = [B] means A depends on B — B must be processed before A.

    Returns ordered list with dependencies first.
    Raises ValueError if a circular dependency is detected.
    """
    # Build: comes_before[dep] = [nodes that depend on dep]
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
        raise ValueError("Circular dependency detected among uploaded Python files")

    return result
