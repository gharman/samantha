"""Line-oriented numeric series extraction."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[-+]?inf|nan"
# Names start only at a token boundary and never give characters back, so each
# token is scanned once. Without both, a long word (a hash, base64, minified JSON)
# was rescanned from every offset, and a 2,000-character token took ~240 ms.
NAME_START = r"(?<![\w./-])[A-Za-z]"
PAIR = re.compile(
    rf"(?P<name>{NAME_START}[\w./-]*+(?:\s+[A-Za-z][\w.-]*+)?)"
    rf"\s*[:=]\s*(?P<value>{NUMBER})\s*(?P<pct>%?)",
    re.I,
)
SPACE = re.compile(
    rf"(?P<name>{NAME_START}[\w./-]++)\s+(?P<value>{NUMBER})(?P<pct>%)?", re.I
)
CEILING = re.compile(
    rf"(?:ceiling|target|expected|budget|limit)\s*[:=]?\s*(?P<value>{NUMBER})", re.I
)


def _name(value: str) -> str:
    return re.sub(r"[\s/]+", ".", value.strip()).lower()


class MetricStore:
    def __init__(self, size: int = 4096) -> None:
        self.series: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=size))
        self.references: dict[str, float] = {}

    def ingest(self, line: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        matches = list(PAIR.finditer(line))
        if not matches:
            matches = list(SPACE.finditer(line))
        ceiling = CEILING.search(line)
        for match in matches:
            name, raw = _name(match.group("name")), match.group("value")
            if name in {"ceiling", "target", "expected", "budget", "limit"}:
                continue
            value = float(raw)
            if match.groupdict().get("pct"):
                value /= 100
            self.series[name].append(value)
            item: dict[str, Any] = {"series": name, "value": value}
            if ceiling:
                reference = float(ceiling.group("value"))
                self.references[name] = reference
                item["reference"] = reference
            found.append(item)
        return found
