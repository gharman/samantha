"""Budgeted project source inventory and daemon directives."""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any

DIRECTIVE = re.compile(r"^\s*#\s*daemon:\s*(.+)$", re.M)


def read_source(path: Path, root: Path, budget: int = 200_000) -> dict[str, Any]:
    result: dict[str, Any] = {"files": {}, "directives": [], "config": {}}
    candidates = [path, root / "README.md", root / "pyproject.toml"]
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file() or root not in candidate.parents and candidate != root:
            continue
        seen.add(candidate)
        text = candidate.read_text(errors="replace")[:budget]
        budget -= len(text)
        result["files"][str(candidate.relative_to(root))] = {"text": text, "docstring": _docstring(text)}
        result["directives"].extend(DIRECTIVE.findall(text))
        if candidate.name == "pyproject.toml":
            try:
                result["config"] = tomllib.loads(text).get("tool", {}).get("samantha", {})
            except tomllib.TOMLDecodeError:
                pass
        if budget <= 0:
            break
    return result


def _docstring(text: str) -> str | None:
    try:
        return ast.get_docstring(ast.parse(text))
    except SyntaxError:
        return None

