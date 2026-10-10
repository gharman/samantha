import json
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

from samantha._boot import Config
from samantha.act.controller import ActController
from samantha.act.ledger import Ledger
from samantha.cognition.harness import Cognition
from samantha.observe.metrics import MetricStore
from samantha.sentinels import Sentinels, WakeEvent
from samantha.state import Journal


def speed_module(tmp_path: Path, monkeypatch: object) -> types.ModuleType:
    source = tmp_path / "game.py"
    source.write_text("SPEED = 1\n")
    module = types.ModuleType("autonomy_demo")
    module.__file__ = str(source)
    module.SPEED = 1
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


class GoalLLM:
    """The wake raises a flag; the planner sets one module constant."""

    def __init__(self) -> None:
        self.plans = 0

    def complete(self, messages: object, signature: str = "default") -> dict:
        return {
            "assessment": "goal unmet",
            "actions": [{"type": "flag", "summary": "goal.met is below target"}],
        }

    def plan_action(self, messages: object) -> dict[str, object]:
        self.plans += 1
        return {
            "reply": "Raised SPEED to close the gap.",
            "lifetime": "process",
            "operations": [
                {
                    "op": "set",
                    "effect": "write",
                    "path": "module:autonomy_demo::SPEED",
                    "query": None,
                    "value_json": "3",
                    "args_json": None,
                    "kwargs_json": None,
                    "lifetime": "process",
                    "reason": "predicted to meet the target",
                }
            ],
        }


def resident(tmp_path: Path, mode: str = "act", autonomy: str = "goal"):
    config = SimpleNamespace(
        mode=mode,
        autonomy=autonomy,
        max_wakes_per_hour=0,
        token_budget_per_day=0,
        flag_budget=0,
        act_max_rounds=4,
        model="test-model",
        goal="meet the goal target",
    )
    llm = GoalLLM()
    current = SimpleNamespace(
        config=config,
        journal=Journal(tmp_path, "run"),
        ledger=Ledger(tmp_path / "ledger.json"),
        state=tmp_path,
        root=tmp_path,
    )
    current.actuator = ActController(current, llm)
    current.cognition = Cognition(current, llm)
    return current, llm


def gap_event() -> WakeEvent:
    metrics = MetricStore()
    for _ in range(20):
        metrics.ingest("goal met: 0 (target 1)")
    events = [e for e in Sentinels(metrics).inspect_metrics() if e.sentinel == "gap"]
    assert len(events) == 1
    assert events[0].evidence == {
        "series": "goal.met",
        "value": 0.0,
        "reference": 1.0,
        "line": "goal met: 0 (target 1)",
    }
    return events[0]


def settle() -> None:
    """Wait for autonomous act turns, which run on their own threads."""
    for thread in threading.enumerate():
        if thread.name == "samantha-autonomy":
            thread.join(5)


def journal_rows(current: SimpleNamespace) -> list[dict]:
    return [json.loads(line) for line in current.journal.path.read_text().splitlines()]


def test_gap_wake_acts_with_goal_autonomy_and_is_revertible(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    current.cognition.wake(gap_event())
    settle()
    assert module.SPEED == 3
    assert llm.plans == 1
    rows = journal_rows(current)
    auto = [r for r in rows if r["type"] == "chat" and r["direction"] == "auto"]
    assert len(auto) == 1 and "goal.met" in auto[0]["text"]
    assert any(r["type"] == "chat" and r["direction"] == "out" for r in rows)
    assert any(n.startswith("acted: ") for n in current.cognition.notices)
    runtime = current.actuator.runtime
    (group,) = runtime.groups.values()
    assert runtime.revert(group.id)
    assert module.SPEED == 1


def test_planner_sees_recent_program_output(tmp_path: Path, monkeypatch: object) -> None:
    speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    lines = [{"source": "stdout", "text": f"{i} hits per minute"} for i in range(30)]
    current.observer = SimpleNamespace(recent_snapshot=lambda limit=50: lines[-limit:])
    seen: list[dict] = []
    plan = llm.plan_action

    def spy(messages: list[dict]) -> dict:
        seen.append(json.loads(messages[0]["content"]))
        return plan(messages)

    llm.plan_action = spy
    current.cognition.wake(gap_event())
    settle()
    assert seen[0]["recent_process_activity"] == [
        f"{i} hits per minute" for i in range(10, 30)
    ]
    # The request itself carries no program output (it is not an instruction).
    assert "hits per minute" not in seen[0]["operator_request"]


def test_gap_wake_does_not_act_without_the_setting(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path, autonomy="off")
    current.cognition.wake(gap_event())
    settle()
    assert module.SPEED == 1
    assert llm.plans == 0
    assert any(r["type"] == "flag" for r in journal_rows(current))


def test_gap_wake_does_not_act_in_advise_mode(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path, mode="advise")
    current.cognition.wake(gap_event())
    settle()
    assert module.SPEED == 1
    assert llm.plans == 0


def test_other_sentinels_do_not_act(tmp_path: Path, monkeypatch: object) -> None:
    module = speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    current.cognition.wake(WakeEvent("stall", {"series": "goal.met"}))
    settle()
    assert module.SPEED == 1
    assert llm.plans == 0


def test_one_autonomous_turn_per_series_per_dwell(
    tmp_path: Path, monkeypatch: object
) -> None:
    speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    current.cognition.wake(gap_event())
    current.cognition.wake(gap_event())
    settle()
    assert llm.plans == 1
    other = WakeEvent("gap", {"series": "score", "value": 0.0, "reference": 1.0})
    current.cognition.wake(other)
    settle()
    assert llm.plans == 2


def test_failed_autonomous_turn_is_journaled_and_reported(
    tmp_path: Path, monkeypatch: object
) -> None:
    speed_module(tmp_path, monkeypatch)
    current, _ = resident(tmp_path)

    def broken(request: str) -> str:
        raise RuntimeError("graph unavailable")

    current.actuator.handle = broken
    current.cognition.wake(gap_event())
    settle()
    rows = [r for r in journal_rows(current) if r["type"] == "chat"]
    assert [r["direction"] for r in rows] == ["auto", "out"]
    assert "graph unavailable" in rows[1]["text"]
    assert "act failed: graph unavailable" in current.cognition.notices


def test_wake_does_not_wait_for_the_autonomous_turn(
    tmp_path: Path, monkeypatch: object
) -> None:
    speed_module(tmp_path, monkeypatch)
    current, _ = resident(tmp_path)
    release = threading.Event()

    def slow(request: str) -> str:
        release.wait(5)
        return "nothing to change"

    current.actuator.handle = slow
    started = time.monotonic()
    assert current.cognition.wake(gap_event()) is not None
    assert time.monotonic() - started < 1
    release.set()
    settle()
    assert any(
        r["type"] == "chat" and r["text"] == "nothing to change"
        for r in journal_rows(current)
    )


def test_chat_and_autonomous_turns_are_serialized(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    active: list[int] = []
    overlap: list[bool] = []

    class SlowLLM:
        def plan_action(self, messages: object) -> dict[str, object]:
            overlap.append(bool(active))
            active.append(1)
            entered.set()
            release.wait(5)
            active.pop()
            return {"reply": "nothing to change", "operations": []}

    current, _ = resident(tmp_path)
    controller = ActController(current, SlowLLM())
    first = threading.Thread(target=controller.handle, args=("autonomous",))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=controller.handle, args=("chat",))
    second.start()
    second.join(0.2)
    assert second.is_alive()  # waits for the first turn's lock
    release.set()
    first.join(5)
    second.join(5)
    assert overlap == [False, False]


def test_autonomy_setting_is_resolved_and_validated(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAMANTHA_AUTONOMY", raising=False)
    assert Config.resolve(_dotenv_start=tmp_path).autonomy == "off"
    monkeypatch.setenv("SAMANTHA_AUTONOMY", "Goal")
    assert Config.resolve(_dotenv_start=tmp_path).autonomy == "goal"
    monkeypatch.setenv("SAMANTHA_AUTONOMY", "always")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy == "off"
    assert any("SAMANTHA_AUTONOMY" in warning for warning in config.warnings)


def unmet_metrics() -> MetricStore:
    metrics = MetricStore()
    for _ in range(20):
        metrics.ingest("goal met: 0 (target 1)")
    return metrics


def test_gap_fires_once_without_rearm() -> None:
    sentinels = Sentinels(unmet_metrics())
    # A flat series below its target is one wake (gap), not also a stall.
    assert [e.sentinel for e in sentinels.inspect_metrics()] == ["gap"]
    for key in sentinels.fired:
        sentinels.fired[key] -= 3600
    assert sentinels.inspect_metrics() == []


def test_stall_still_fires_for_flat_series_without_a_gap() -> None:
    metrics = MetricStore()
    for _ in range(20):
        metrics.ingest("gpu/util: 72%")
        metrics.ingest("goal met: 1 (target 1)")
    events = Sentinels(metrics).inspect_metrics()
    # gpu.util has no target; goal.met is flat but at its target.
    assert sorted((e.sentinel, e.evidence["series"]) for e in events) == [
        ("stall", "goal.met"),
        ("stall", "gpu.util"),
    ]


def test_rearmed_gap_fires_again_after_the_dwell() -> None:
    sentinels = Sentinels(unmet_metrics(), rearm={"gap": 600.0})
    assert [e.sentinel for e in sentinels.inspect_metrics()] == ["gap"]
    assert sentinels.inspect_metrics() == []  # still inside the dwell
    for key in sentinels.fired:
        sentinels.fired[key] -= 601
    assert [e.sentinel for e in sentinels.inspect_metrics()] == ["gap"]


def test_unmet_target_gets_another_turn_after_the_dwell(
    tmp_path: Path, monkeypatch: object
) -> None:
    speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    first = gap_event()
    current.cognition.wake(first)
    settle()
    evidence = dict(first.evidence)
    current.cognition.wake(WakeEvent("gap", evidence, at=first.at + 300))
    settle()
    assert llm.plans == 1
    current.cognition.wake(WakeEvent("gap", evidence, at=first.at + 600))
    settle()
    assert llm.plans == 2


def test_autonomy_dwell_is_configurable_with_a_floor(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAMANTHA_AUTONOMY_DWELL", raising=False)
    assert Config.resolve(_dotenv_start=tmp_path).autonomy_dwell == 600
    monkeypatch.setenv("SAMANTHA_AUTONOMY_DWELL", "90")
    assert Config.resolve(_dotenv_start=tmp_path).autonomy_dwell == 90
    monkeypatch.setenv("SAMANTHA_AUTONOMY_DWELL", "5")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_dwell == 30
    assert any("SAMANTHA_AUTONOMY_DWELL" in warning for warning in config.warnings)


def test_a_shorter_dwell_gives_the_next_turn_sooner(
    tmp_path: Path, monkeypatch: object
) -> None:
    speed_module(tmp_path, monkeypatch)
    current, llm = resident(tmp_path)
    current.config.autonomy_dwell = 90
    first = gap_event()
    current.cognition.wake(first)
    settle()
    evidence = dict(first.evidence)
    current.cognition.wake(WakeEvent("gap", evidence, at=first.at + 60))
    settle()
    assert llm.plans == 1
    current.cognition.wake(WakeEvent("gap", evidence, at=first.at + 90))
    settle()
    assert llm.plans == 2


def test_runtime_rearms_gap_only_for_goal_autonomy_in_act_mode(
    tmp_path: Path, monkeypatch: object
) -> None:
    from samantha._boot import _start_runtime

    monkeypatch.setattr("samantha.chat.server.ChatServer.start", lambda self: None)
    monkeypatch.setattr("samantha.cli.runtime_dir", lambda: tmp_path / "run")
    for mode, autonomy, dwell, rearm in [
        ("act", "goal", 600, {"gap": 600.0}),
        ("act", "goal", 90, {"gap": 90.0}),
        ("act", "off", 600, {}),
        ("advise", "goal", 600, {}),
    ]:
        current = SimpleNamespace(
            config=Config(mode=mode, autonomy=autonomy, autonomy_dwell=dwell),
            observer=SimpleNamespace(metrics=MetricStore()),
            stop=lambda: None,
        )
        _start_runtime(current)
        assert current.sentinels.rearm == rearm
