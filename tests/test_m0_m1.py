import json
from pathlib import Path

from samantha.identity import fingerprint
from samantha.observe.census import census
from samantha.observe.hotread import CountingDict
from samantha.observe.metrics import MetricStore
from samantha.state import Journal, redact


def test_redaction_and_journal(tmp_path: Path) -> None:
    assert "secret" not in redact("token=secret")
    journal = Journal(tmp_path, "run")
    journal.write("chat", direction="in", text="Bearer abcdef")
    row = json.loads(journal.path.read_text())
    assert row["seq"] == 1 and "abcdef" not in row["text"]


def test_metrics_and_reference() -> None:
    metrics = MetricStore()
    found = metrics.ingest("fidelity head_2: 0.71 (ceiling 0.92)")
    assert found == [{"series": "fidelity.head_2", "value": 0.71, "reference": 0.92}]
    assert metrics.references["fidelity.head_2"] == 0.92
    assert metrics.ingest("loss=1.25 gpu/util: 72%")[-1]["value"] == 0.72


def test_census_and_counting_dict() -> None:
    assert "cfg" in census({"cfg": {"lr": 0.1}, "unrelated": {}})
    wrapped = CountingDict({"lr": 0.1}, "lr")
    assert wrapped["lr"] == 0.1 and wrapped.reads == 1


def test_function_fingerprint_stable(tmp_path: Path) -> None:
    path = tmp_path / "x.py"
    path.write_text("def f():\n    return 1\n")
    first = fingerprint(path, "f")
    path.write_text("# comment\ndef f():\n    return 1\n")
    assert fingerprint(path, "f") == first
