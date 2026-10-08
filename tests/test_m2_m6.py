import json
from pathlib import Path
from types import SimpleNamespace

from samantha.act.ledger import Ledger
from samantha.cognition.harness import Cognition
from samantha.cognition.llm import (
    FakeLLM,
    _action_plan_format,
    _cold_start_format,
    _output_text,
)
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


def test_cold_start_uses_strict_structured_output() -> None:
    output = _cold_start_format()
    assert output["type"] == "json_schema"
    assert output["strict"] is True
    assert output["schema"]["additionalProperties"] is False
    assert "watching" in output["schema"]["required"]


def test_action_plan_uses_fixed_operation_vocabulary() -> None:
    output = _action_plan_format()
    operation = output["schema"]["properties"]["operations"]["items"]
    assert output["strict"] is True
    assert "intercept" in operation["properties"]["op"]["enum"]
    assert "rewrite_output" in operation["properties"]["op"]["enum"]
    assert "effect" in operation["required"]
    assert "exec" not in operation["properties"]["op"]["enum"]


def test_chat_can_be_ordinary_conversation(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    save_json(
        tmp_path / "source-inventory.json",
        {"entry": "app.py", "files": {"app.py": {"docstring": "A demo."}}},
    )
    cognition = Cognition(
        resident,
        FakeLLM({"chat": {"answer": "Why did the daemon cross the event loop?"}}),
    )
    cognition.provider_ready = True
    cognition.analysis_ready = False
    assert (
        cognition.chat("Tell me a joke") == "Why did the daemon cross the event loop?"
    )


def test_ledger_protection_does_not_match_substrings(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    for knob in (
        "retrieval",
        "retriever",
        "latest",
        "evaluate",
        "contest",
        "attestation",
    ):
        assert ledger.register(knob)["state"] == "declared", knob
    for knob in (
        "eval",
        "seed",
        "test",
        "tests",
        "ckpt",
        "checkpoint",
        "eval_seed",
        'config/"eval-split"',
        'config/"seed offset"',
    ):
        assert ledger.register(knob)["state"] == "protected", knob


def test_sentinel_wakes_never_block_the_observed_thread(tmp_path: Path) -> None:
    import threading
    import time

    from samantha._boot import _inspect
    from samantha.observe.metrics import MetricStore
    from samantha.sentinels import Sentinels

    release = threading.Event()

    class BlockingLLM:
        def complete(self, messages: list, signature: str = "default") -> dict:
            release.wait(5)
            return {"actions": []}

    resident = fake_resident(tmp_path)
    resident.sentinels = Sentinels(MetricStore())
    resident.cognition = Cognition(resident, BlockingLLM())
    started = time.monotonic()
    _inspect(resident, "WARNING disk nearly full")
    assert time.monotonic() - started < 0.5
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = [
            json.loads(line) for line in resident.journal.path.read_text().splitlines()
        ]
        if any(row["type"] == "wake" for row in rows):
            break
        time.sleep(0.02)
    assert any(
        row["type"] == "wake" and row["sentinel"] == "log-novelty" for row in rows
    )


def test_goals_reach_chat_and_act_contexts_and_questions_are_not_hijacked(
    tmp_path: Path,
) -> None:
    from samantha.act.controller import ActController
    from samantha.chat.server import ChatServer

    resident = fake_resident(tmp_path, mode="advise")
    resident.key = "project"
    seen: list[str] = []

    class RecordingLLM:
        def chat(self, messages: list[dict[str, str]]) -> str:
            seen.append(messages[0]["content"])
            return "answered"

        def plan_action(self, messages: list[dict[str, str]]) -> dict[str, object]:
            seen.append(messages[0]["content"])
            return {"reply": "noted", "operations": []}

    resident.cognition = Cognition(resident, RecordingLLM())
    server = ChatServer(resident, tmp_path / "run")
    reply = server._handle({"type": "command", "text": "goal of this app?"})
    assert reply["reply"] == "answered" and resident.config.goal is None
    reply = server._handle({"type": "command", "text": "/goal every head at ceiling"})
    assert resident.config.goal == "every head at ceiling"
    server._handle({"type": "command", "text": "how close are we?"})
    assert '"current_goal": "every head at ceiling"' in seen[-1]
    resident.ledger = Ledger(tmp_path / "ledger.json")
    ActController(resident, RecordingLLM()).handle("push toward it")
    assert '"current_goal": "every head at ceiling"' in seen[-1]


def test_wake_findings_use_a_schema_and_reach_the_operator(tmp_path: Path) -> None:
    from samantha.chat.server import ChatServer
    from samantha.cognition.llm import _wake_format

    schema = _wake_format()["schema"]
    kinds = schema["properties"]["actions"]["items"]["properties"]["type"]["enum"]
    assert kinds == ["flag", "question", "proposal", "resolve"]
    resident = fake_resident(tmp_path)
    resident.key = "project"
    llm = FakeLLM(
        {
            "stall": {
                "assessment": "gpu/util has not moved in 20 samples",
                "actions": [
                    {
                        "type": "flag",
                        "summary": "gpu/util is pinned at 72%",
                        "evidence": "20 identical samples",
                        "prediction": "",
                    }
                ],
            },
            "chat": {"answer": "Looking into it."},
        }
    )
    resident.cognition = Cognition(resident, llm)
    resident.cognition.provider_ready = True
    resident.cognition.wake(WakeEvent("stall", {"series": "gpu.util"}))
    server = ChatServer(resident, tmp_path / "run")
    reply = server._handle({"type": "command", "text": "anything wrong?"})
    assert reply["notices"] == ["flag: gpu/util is pinned at 72%"]
    assert server._handle({"type": "command", "text": "and now?"})["notices"] == []


def test_silence_pauses_sentinel_wakes(tmp_path: Path) -> None:
    import time

    resident = fake_resident(tmp_path)
    llm = FakeLLM({"stall": {"assessment": "x", "actions": []}})
    cognition = Cognition(resident, llm)
    resident.quiet_until = time.time() + 60
    assert cognition.wake(WakeEvent("stall", {"series": "loss"})) is None
    assert llm.calls == 0
    resident.quiet_until = 0
    assert cognition.wake(WakeEvent("stall", {"series": "loss"})) is not None


def test_analysis_inventory_reports_cut_files(tmp_path: Path) -> None:
    cognition = Cognition(fake_resident(tmp_path), None)
    inventory = {
        "entry": "main.py",
        "files": {
            "main.py": {"text": "a" * 15_000},
            "other.py": {"text": "b" * 3_000},
        },
        "truncated": ["earlier.py"],
    }
    out = cognition._analysis_inventory(inventory)
    assert len(out["files"]["main.py"]["source_excerpt"]) == 15_000
    assert len(out["files"]["other.py"]["source_excerpt"]) == 2_500
    assert out["truncated"] == ["earlier.py", "other.py"]

    inventory["files"]["main.py"]["text"] = "a" * 25_000
    out = cognition._analysis_inventory(inventory)
    assert len(out["files"]["main.py"]["source_excerpt"]) == 20_000
    assert out["truncated"] == ["earlier.py", "main.py", "other.py"]


def _flag(summary: str) -> dict[str, object]:
    return {"type": "flag", "summary": summary, "evidence": "e", "prediction": ""}


def test_wake_can_resolve_a_flag_and_frees_budget(tmp_path: Path) -> None:
    resident = fake_resident(tmp_path)
    resident.config.flag_budget = 1
    llm = FakeLLM({"stall": {"assessment": "x", "actions": [_flag("pinned")]}})
    cognition = Cognition(resident, llm)
    cognition.wake(WakeEvent("stall", {}))
    (flag_id,) = cognition.open_flags
    # At budget, a second flag is dropped but the wake still runs.
    cognition.wake(WakeEvent("stall", {}))
    assert list(cognition.open_flags) == [flag_id]
    llm.scripts["stall"] = {
        "assessment": "fixed",
        "actions": [
            {"type": "resolve", "flag_id": flag_id, "summary": "util recovered"}
        ],
    }
    cognition.wake(WakeEvent("stall", {}))
    assert cognition.open_flags == {}
    rows = resident.journal.recent(50, "flag_resolved")
    assert rows[-1]["id"] == flag_id and rows[-1]["reason"] == "util recovered"
    llm.scripts["stall"] = {"assessment": "x", "actions": [_flag("again")]}
    cognition.wake(WakeEvent("stall", {}))
    assert len(cognition.open_flags) == 1


def test_resolve_command_lists_and_closes_flags(tmp_path: Path) -> None:
    from samantha.chat.server import ChatServer

    resident = fake_resident(tmp_path)
    resident.key = "project"
    cognition = Cognition(
        resident, FakeLLM({"s": {"assessment": "x", "actions": [_flag("a")]}})
    )
    cognition.wake(WakeEvent("s", {}))
    resident.cognition = cognition
    (flag_id,) = cognition.open_flags
    server = ChatServer(resident, tmp_path / "run")
    assert flag_id in server._handle({"type": "command", "text": "/resolve"})["reply"]
    assert server._handle({"type": "command", "text": "/resolve nope"})["reply"] == (
        "No such open flag."
    )
    reply = server._handle({"type": "command", "text": f"/resolve {flag_id}"})
    assert flag_id in reply["reply"] and cognition.open_flags == {}
