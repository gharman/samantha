"""Stable project discovery and source fingerprints."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


def discover_project(entry: str | Path | None = None) -> tuple[Path, str]:
    path = Path(entry or Path.cwd()).resolve()
    if path.is_file():
        start = path.parent
    else:
        start = path
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate, project_key(candidate)
    target = path if path.is_file() else start
    return start, project_key(target)


def project_key(path: str | Path) -> str:
    return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:16]


def fingerprint(path: str | Path, function: str | None = None) -> str:
    data = Path(path).read_bytes()
    if function:
        try:
            tree = ast.parse(data)
            node = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == function
            )
            data = ast.dump(node, include_attributes=False).encode()
        except (SyntaxError, StopIteration):
            pass
    return hashlib.sha256(data).hexdigest()
