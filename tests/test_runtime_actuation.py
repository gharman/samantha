import queue
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from samantha.act.controller import ActController
from samantha.act.ledger import Ledger
from samantha.act.runtime import RuntimeOperator
from samantha.state import Journal


def runtime_module(tmp_path: Path, monkeypatch: object) -> types.ModuleType:
    source = tmp_path / "app.py"
    source.write_text("# live test module\n")
    module = types.ModuleType("runtime_demo")
    module.__file__ = str(source)
    module.config = {"topic": "general", "count": 1}
    module.requests = queue.Queue()

    def generate(topic: str = "default") -> str:
        return topic

    generate.__module__ = module.__name__
    module.generate = generate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def resident(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path,
        state=tmp_path,
        journal=Journal(tmp_path, "runtime"),
        ledger=Ledger(tmp_path / "ledger.json"),
        config=SimpleNamespace(mode="act"),
    )


def test_unregistered_live_state_can_be_mutated_in_a_group(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "steer the next item",
        [
            {
                "op": "set",
                "path": 'module:runtime_demo::config/"topic"',
                "value": "neuroevolutionary AI",
            },
            {
                "op": "enqueue",
                "path": "module:runtime_demo::requests",
                "value": "HI GREG",
            },
        ],
        "process",
    )
    assert module.config["topic"] == "neuroevolutionary AI"
    assert module.requests.get_nowait() == "HI GREG"
    assert all(result.ok for result in group.results)
    assert group.status == "active"


def test_one_shot_interceptor_restores_after_use(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    original = module.generate
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "override the next generation topic",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::generate",
                "kwargs": {"topic": "neuroevolutionary AI"},
                "lifetime": "one_shot",
            }
        ],
        "one_shot",
    )
    assert group.results[0].verified
    assert module.generate() == "neuroevolutionary AI"
    assert module.generate is original
    assert module.generate() == "default"


def test_process_mutation_can_be_reverted(tmp_path: Path, monkeypatch: object) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "change topic for this process",
        [
            {
                "op": "set",
                "path": 'module:runtime_demo::config/"topic"',
                "value": "evolution",
            }
        ],
        "process",
    )
    assert module.config["topic"] == "evolution"
    assert operator.revert(group.id)
    assert module.config["topic"] == "general"


class PlannedLLM:
    def plan_action(self, messages: object) -> dict[str, object]:
        return {
            "intent": "act",
            "reply": "I will change the live topic.",
            "done": True,
            "lifetime": "process",
            "operations": [
                {
                    "op": "set",
                    "path": 'module:runtime_demo::config/"topic"',
                    "query": None,
                    "value_json": '"neuroevolutionary AI"',
                    "args_json": None,
                    "kwargs_json": None,
                    "lifetime": "process",
                    "reason": "operator requested this topic",
                }
            ],
        }


def test_controller_returns_verified_receipt(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    controller = ActController(resident(tmp_path), PlannedLLM())
    reply = controller.handle("make the next topic neuroevolutionary AI")
    assert module.config["topic"] == "neuroevolutionary AI"
    assert "Applied runtime intervention" in reply
    assert "assignment verified" in reply
