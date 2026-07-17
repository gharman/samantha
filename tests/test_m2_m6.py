import json
from pathlib import Path
from types import SimpleNamespace

from samantha.act.ledger import Ledger
from samantha.claims import ClaimStore
from samantha.cognition.harness import Cognition
from samantha.cognition.llm import FakeLLM
from samantha.report import generate
from samantha.sentinels import WakeEvent
from samantha.state import Journal


def fake_resident(tmp_path: Path, mode: str = "advise") -> SimpleNamespace:
    config = SimpleNamespace(
        mode=mode,
        max_wakes_per_hour=6,
        token_budget_per_day=1000,
        flag_budget=3,
    )
    return SimpleNamespace(
        config=config, journal=Journal(tmp_path, "run"), state=tmp_path
    )


def test_fake_cognition_downgrades_intervention(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    llm = FakeLLM(
        {"gap": {"actions": [{"type": "intervene", "knob": "lr", "value": 1}]}}
    )
    Cognition(resident, llm).wake(WakeEvent("gap", {"series": "loss"}))
    rows = [json.loads(line) for line in resident.journal.path.read_text().splitlines()]
    assert [row["type"] for row in rows] == ["wake", "proposal"]


def test_claim_demotion(tmp_path: Path) -> None:
    source = tmp_path / "code.py"
    source.write_text("def train():\n    return 1\n")
    claims = ClaimStore(tmp_path / "claims.json")
    claims.record("hot", "live", source, "train")
    source.write_text("def train():\n    return 2\n")
    assert claims.validate() == ["hot"]


def test_ledger_protection_and_promotion(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    assert ledger.register("eval_seed")["state"] == "protected"
    ledger.register("lr", (0.0, 1.0))
    ledger.transition("lr", "probed")
    ledger.outcome("lr", True)
    ledger.outcome("lr", True)
    assert ledger.authorized("lr", 0.5)
    assert not ledger.authorized("lr", 2.0)


def test_three_run_report(tmp_path: Path) -> None:
    for index in range(3):
        journal = Journal(tmp_path, f"run-{index}")
        journal.write("intervention", id=f"i{index}", knob="cfg.weight", old=1, new=2)
    report = generate(tmp_path)
    assert "cfg.weight" in report and "Proposed permanent source change" in report
