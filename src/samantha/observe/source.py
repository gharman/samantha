"""Budgeted project source inventory and daemon directives."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any

DIRECTIVE = re.compile(r"^\s*#\s*daemon:\s*(.+)$", re.M)
NOTES = re.compile(r"^\s*#.*\b(?:TODO|FIXME)\b.*$", re.M | re.I)
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}


def read_source(path: Path, root: Path, budget: int = 180_000) -> dict[str, Any]:
    """Read entry/imported code first, then the remaining project Python surface."""
    entry = path.resolve()
    root = root.resolve()
    result: dict[str, Any] = {
        "entry": str(entry.relative_to(root)),
        "files": {},
        "directives": [],
        "notes": [],
        "config": {},
        "truncated": [],
    }
    ordered = _ordered_candidates(entry, root)
    remaining = budget
    for candidate in ordered:
        if remaining <= 0:
            result["truncated"].append(str(candidate.relative_to(root)))
            continue
        try:
            text = candidate.read_text(errors="replace")
        except OSError:
            continue
        included = text[:remaining]
        remaining -= len(included)
        relative = str(candidate.relative_to(root))
        result["files"][relative] = {
            "text": included,
            "docstring": _docstring(included),
            "imports": _imports(included),
            "outline": _outline(included),
            "help_strings": _help_strings(included),
        }
        result["directives"].extend(DIRECTIVE.findall(included))
        result["notes"].extend(NOTES.findall(included))
        if len(included) < len(text):
            result["truncated"].append(relative)
        if candidate.name == "pyproject.toml":
            try:
                result["config"] = (
                    tomllib.loads(included).get("tool", {}).get("samantha", {})
                )
            except tomllib.TOMLDecodeError:
                pass
    return result


def _ordered_candidates(entry: Path, root: Path) -> list[Path]:
    python_files = [
        path
        for path in root.rglob("*.py")
        if not any(part in IGNORED_DIRS or part.startswith(".") for part in path.parts)
    ]
    closure = _import_closure(entry, root, python_files)
    remainder = sorted(
        (path for path in python_files if path not in closure),
        key=lambda item: (-item.stat().st_size, str(item)),
    )
    docs = [root / name for name in ("README.md", "README.rst", "pyproject.toml")]
    return [*closure, *remainder, *(path for path in docs if path.is_file())]


def _import_closure(entry: Path, root: Path, files: list[Path]) -> list[Path]:
    module_map = _module_map(root, files)
    ordered: list[Path] = []
    pending = [entry]
    seen: set[Path] = set()
    while pending:
        current = pending.pop(0).resolve()
        if current in seen or not current.is_file() or root not in current.parents:
            continue
        seen.add(current)
        ordered.append(current)
        try:
            imports = _imports(current.read_text(errors="replace"))
        except OSError:
            continue
        for name in imports:
            target = module_map.get(name) or module_map.get(name.split(".")[0])
            if target and target not in seen:
                pending.append(target)
    return ordered


def _module_map(root: Path, files: list[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in files:
        relative = path.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts[:1] == ["src"]:
            parts = parts[1:]
        if parts[-1:] == ["__init__"]:
            parts = parts[:-1]
        if parts:
            result[".".join(parts)] = path
    return result


def _imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _docstring(text: str) -> str | None:
    try:
        return ast.get_docstring(ast.parse(text))
    except SyntaxError:
        return None


def _outline(text: str) -> list[dict[str, Any]]:
    """Return top-level source structure without function bodies."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    result = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            item: dict[str, Any] = {
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "name": node.name,
                "line": node.lineno,
            }
            docstring = ast.get_docstring(node)
            if docstring:
                item["docstring"] = docstring[:500]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                item["parameters"] = [argument.arg for argument in node.args.args]
            result.append(item)
    return result


def _help_strings(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in {"help", "description"} and isinstance(
                keyword.value, ast.Constant
            ):
                if isinstance(keyword.value.value, str):
                    result.append(keyword.value.value[:500])
    return result
