import json
from pathlib import Path
from types import SimpleNamespace

from samantha.act.ledger import Ledger
from samantha.claims import ClaimStore
from samantha.cognition.harness import Cognition
from samantha.cognition.llm import FakeLLM, _output_text
from samantha.report import generate
from samantha.sentinels import WakeEvent
from samantha.state import Journal, save_json


def fake_resident(tmp_path: Path, mode: str = "advise") -> SimpleNamespace:
    config = SimpleNamespace(
        mode=mode,
        max_wakes_per_hour=6,
        token_budget_per_day=1000,
        flag_budget=3,
        model="test-model",
        goal=None,
    )
    return SimpleNamespace(
        config=config,
        journal=Journal(tmp_path, "run"),
        state=tmp_path,
        root=tmp_path,
    )


def test_fake_cognition_downgrades_intervention(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    llm = FakeLLM(
        {"gap": {"actions": [{"type": "intervene", "knob": "lr", "value": 1}]}}
    )
    Cognition(resident, llm).wake(WakeEvent("gap", {"series": "loss"}))
    rows = [json.loads(line) for line in resident.journal.path.read_text().splitlines()]
    assert [row["type"] for row in rows] == ["wake", "proposal"]


def test_fake_cognition_answers_operator(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    (tmp_path / "model.md").write_text("A queue worker")
    cognition = Cognition(
        resident,
        FakeLLM({"chat": {"answer": "The queue is currently healthy."}}),
    )
    cognition.provider_ready = True
    cognition.analysis_ready = True
    assert cognition.chat("How is the queue?") == "The queue is currently healthy."


def test_provider_failure_is_available_to_chat(tmp_path: Path) -> None:
    class BrokenLLM:
        def check(self) -> None:
            raise RuntimeError("invalid model")

    resident = fake_resident(tmp_path)
    cognition = Cognition(resident, BrokenLLM())
    cognition._check_provider()
    assert cognition.chat("hello") == "Cognition is unavailable: invalid model"


def test_cold_start_builds_specific_program_theory(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    save_json(
        tmp_path / "source-inventory.json",
        {"files": {"worker.py": {"text": "def drain_queue(): pass"}}},
    )
    cognition = Cognition(
        resident,
        FakeLLM(
            {
                "cold_start": {
                    "summary": "A durable queue worker.",
                    "architecture": ["worker.drain_queue consumes jobs"],
                    "goals": ["keep queue lag low"],
                    "watching": ["queue lag", "failed jobs"],
                }
            }
        ),
    )
    cognition._check_provider()
    model = (tmp_path / "model.md").read_text()
    assert cognition.provider_ready is True
    assert "durable queue worker" in model
    assert "queue lag" in model


def test_analysis_timeout_does_not_disable_provider_or_chat(tmp_path: Path) -> None:
    class SlowLLM:
        def check(self) -> None:
            pass

        def complete(self, messages: object, signature: str) -> object:
            raise TimeoutError("read timed out")

    resident = fake_resident(tmp_path)
    save_json(
        tmp_path / "source-inventory.json",
        {
            "entry": "app.py",
            "files": {
                "app.py": {"docstring": "Runs a durable queue."},
                "worker.py": {"docstring": "Consumes queued work."},
            },
        },
    )
    (tmp_path / "model.md").write_text("Initial deterministic inventory")
    cognition = Cognition(resident, SlowLLM())
    cognition._check_provider()
    assert cognition.provider_ready is True
    assert cognition.analysis_ready is False
    reply = cognition.chat("What do you know?")
    assert "Runs a durable queue" in reply
    assert "worker.py" in reply
    assert "No runtime metric activity" in reply
    assert cognition.provider_ready is True


def test_incomplete_analysis_still_has_live_conversation(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    save_json(
        tmp_path / "source-inventory.json",
        {
            "entry": "app.py",
            "files": {"app.py": {"docstring": "Runs an idea funnel."}},
        },
    )
    cognition = Cognition(
        resident,
        FakeLLM(
            {
                "chat": {
                    "answer": "I’m still analyzing, but this is an idea-funnel application."
                }
            }
        ),
    )
    cognition.provider_ready = True
    cognition.analysis_ready = False
    cognition.analysis_error = "initial request timed out"
    reply = cognition.chat("What does it do?")
    assert reply == "I’m still analyzing, but this is an idea-funnel application."


def test_responses_parser_skips_reasoning_items() -> None:
    payload = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"answer":"ready"}'}],
            },
        ],
    }
    assert _output_text(payload) == '{"answer":"ready"}'


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
