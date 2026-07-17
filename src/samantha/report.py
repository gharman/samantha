"""Cross-run recurrence mining and permanent-fix reports."""

from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def events(state: Path) -> list[dict[str, Any]]:
    rows = []
    for journal in state.glob("journal-*.jsonl"):
        for line in journal.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def generate(state: Path, llm: Any = None) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events(state):
        if row.get("type") in {"intervention", "flag", "proposal"}:
            signature = str(row.get("knob") or row.get("text") or row.get("body"))
            grouped[signature].append(row)
    sections = ["# samantha recurrence report", ""]
    for signature, rows in grouped.items():
        runs = {row.get("run_id") for row in rows}
        if len(runs) < 3:
            continue
        kept = sum(
            1
            for row in events(state)
            if row.get("type") == "outcome"
            and row.get("verdict") == "kept"
            and any(r.get("id") == row.get("intervention_id") for r in rows)
        )
        proposal = (
            "Promote the repeatedly successful runtime setting into the source default."
        )
        if llm:
            response = llm.complete(
                [
                    {
                        "role": "user",
                        "content": f"Propose a source diff for recurrence: {signature}",
                    }
                ],
                "report",
            )
            proposal = str(response.get("diff", proposal))
        sections.extend(
            [
                f"## {signature[:120]}",
                "",
                f"Observed in {len(runs)} runs; {kept} kept outcomes.",
                "",
                "### Proposed permanent source change",
                "",
                "```diff",
                proposal,
                "```",
                "",
            ]
        )
    text = "\n".join(sections)
    (state / "report.md").write_text(text)
    return text
