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


def test_daemon_goal_directive_is_the_goal_when_none_is_set(
    tmp_path: Path, monkeypatch: object
) -> None:
    from samantha.cognition.harness import directive_goal, operator_intent

    current, llm = resident(tmp_path)
    (tmp_path / "source-inventory.json").write_text(json.dumps({"directives": [
        "goal: the player should be hit less than once per minute",
        "fair game, live: BOMB_ODDS 60-600",
    ]}))
    current.config.goal = None
    assert operator_intent(current)["current_goal"] == (
        "the player should be hit less than once per minute"
    )
    current.config.goal = "operator text wins"
    assert operator_intent(current)["current_goal"] == "operator text wins"
    assert directive_goal(["goal — every region reaches 0.95", "goals are nice"]) == (
        "every region reaches 0.95"
    )
    assert directive_goal(["never touch SCORE"]) is None
    # Wakes see the directives too (what is fair game, and the limits).
    prompts: list[str] = []

    def complete(messages: list[dict], signature: str = "default") -> dict:
        prompts.append(messages[0]["content"])
        return {"assessment": "ok", "actions": []}

    llm.complete = complete
    import samantha

    settings = SimpleNamespace(rare=1.0)
    monkeypatch.setattr(samantha, "_exposures", {})
    samantha.expose("rare_weight", obj=settings, attr="rare", bounds=(0.1, 5.0))
    current.cognition.wake(WakeEvent("stall", {"series": "x"}))
    assert "fair game, live: BOMB_ODDS 60-600" in prompts[0]
    # ... and the declared knobs, with their current and starting values and bounds.
    settings.rare = 2.0
    current.cognition.wake(WakeEvent("stall", {"series": "y"}))
    sent = json.loads(prompts[1][prompts[1].rindex('{"event"'):])
    assert sent["declared_knobs"] == [
        {"name": "rare_weight", "value": 2.0, "bounds": [0.1, 5.0], "start": 1.0}
    ]


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
            config=Config(mode=mode, autonomy=autonomy, autonomy_dwell=dwell,
                          autonomy_trigger="^player hit", autonomy_slack="^too easy"),
            observer=SimpleNamespace(metrics=MetricStore()),
            stop=lambda: None,
        )
        _start_runtime(current)
        assert current.sentinels.rearm == rearm
        # Miss lines and slack lines count only under goal autonomy in act mode.
        assert (current.trigger is not None) == bool(rearm)
        assert (current.slack is not None) == bool(rearm)


# --- miss lines (SAMANTHA_AUTONOMY_TRIGGER) --------------------------------------


class MissLLM:
    """The first-move plan sets speed 3; later turns set speed 5. No chat."""

    def __init__(self, first: list[dict] | None = None) -> None:
        self.requests: list[str] = []
        self.chats = 0
        self.first = first
        self.gate: threading.Event | None = None

    def complete(self, messages: object, signature: str = "default") -> dict:
        return {"assessment": "ok", "actions": []}

    def chat(self, messages: object) -> str:
        self.chats += 1
        return "synthesized"

    def plan_action(self, messages: list[dict]) -> dict[str, object]:
        request = json.loads(messages[0]["content"])["operator_request"]
        self.requests.append(request)
        if self.gate is not None:
            self.gate.wait(5)
        first = "plan your first move now" in request
        value = 3 if first else 5
        operations = self.first if first and self.first is not None else [
            {"op": "set", "effect": "write", "path": "exposed:speed", "query": None,
             "value_json": str(value), "args_json": None, "kwargs_json": None,
             "lifetime": "process", "duration_s": None, "reason": "fewer misses"}
        ]
        return {"intent": "act", "reply": f"speed to {value}", "done": True,
                "lifetime": "process", "operations": operations}


def miss_resident(tmp_path: Path, monkeypatch: object, autonomy: str = "goal",
                  llm: MissLLM | None = None):
    import samantha

    knob = SimpleNamespace(speed=1)
    monkeypatch.setattr(samantha, "_exposures", {})
    samantha.expose("speed", obj=knob, attr="speed", bounds=(1, 9))
    speed_module(tmp_path, monkeypatch)
    config = SimpleNamespace(
        mode="act", autonomy=autonomy, max_wakes_per_hour=0, token_budget_per_day=0,
        flag_budget=0, act_max_rounds=4, model="test-model", goal="fewer misses",
        autonomy_trigger="^player hit", trigger_dwell=10.0,
    )
    llm = llm or MissLLM()
    current = SimpleNamespace(
        config=config, journal=Journal(tmp_path, "run"),
        ledger=Ledger(tmp_path / "ledger.json"), state=tmp_path, root=tmp_path,
    )
    current.actuator = ActController(current, llm)
    current.cognition = Cognition(current, llm)
    current.cognition.provider_ready = True  # the model check passed
    return current, llm, knob


def test_first_miss_applies_the_planned_move_with_no_model_call(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch)
    cog = current.cognition
    cog._prepare()  # at startup, while the program is healthy
    assert knob.speed == 1 and len(llm.requests) == 1
    assert cog.prepared["planned_against"] == {"exposed:speed": 1}
    assert cog.on_trigger("player hit · deaths: 1") is True
    settle()
    assert knob.speed == 3
    assert len(llm.requests) == 1 and llm.chats == 0  # no model call at the miss
    rows = [r for r in journal_rows(current) if r["type"] == "chat"]
    assert "applying the first move" in rows[-2]["text"]
    assert rows[-1]["text"].startswith("Applied runtime intervention")
    assert any(r["type"] == "intervention" for r in journal_rows(current))
    assert any(n.startswith("acted: ") for n in cog.notices)
    # The move is audited and revertible like any other change.
    (group,) = current.actuator.runtime.groups.values()
    assert current.actuator.runtime.revert(group.id) and knob.speed == 1


def test_later_misses_plan_live_once_per_dwell_with_no_synthesis(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch)
    cog = current.cognition
    cog._prepare()
    assert cog.on_trigger("player hit") is True
    settle()
    assert cog.on_trigger("player hit") is False  # inside the dwell: counted only
    assert cog.on_trigger("player hit") is False
    cog._trigger_at -= 10  # the dwell has passed
    assert cog.on_trigger("player hit") is True
    settle()
    assert knob.speed == 5
    assert len(llm.requests) == 2 and llm.chats == 0
    assert "3 line(s)" in llm.requests[1] and "10 s between" in llm.requests[1]
    assert "player hit" not in llm.requests[1].replace("'^player hit'", "")


def test_planned_move_is_dropped_when_its_knob_moved(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch)
    current.cognition._prepare()
    knob.speed = 2  # for example, a chat turn changed it
    current.cognition.on_trigger("player hit")
    settle()
    assert knob.speed == 5  # planned again, live
    assert len(llm.requests) == 2
    texts = [r["text"] for r in journal_rows(current) if r["type"] == "chat"]
    assert any("was not applied" in t for t in texts)


def test_planned_move_keeps_only_in_bounds_sets_on_declared_knobs(
    tmp_path: Path, monkeypatch: object
) -> None:
    def op(path: str, value: str, kind: str = "set") -> dict:
        return {"op": kind, "effect": "write", "path": path, "query": None,
                "value_json": value, "args_json": None, "kwargs_json": None,
                "lifetime": "process", "duration_s": None, "reason": "x"}

    llm = MissLLM(first=[op("exposed:speed", "99"),
                         op("module:autonomy_demo::SPEED", "3"),
                         op("exposed:speed", '"fast"')])
    current, _, knob = miss_resident(tmp_path, monkeypatch, llm=llm)
    cog = current.cognition
    cog._prepare()
    assert cog.prepared is None
    texts = [r["text"] for r in journal_rows(current) if r["type"] == "chat"]
    assert "outside (1, 9)" in texts[-1] and "not a set on a declared knob" in texts[-1]
    assert "is not a number" in texts[-1]
    cog.on_trigger("player hit")
    settle()
    assert knob.speed == 5 and sys.modules["autonomy_demo"].SPEED == 1


def test_a_miss_during_planning_waits_for_the_first_move(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch)
    cog = current.cognition
    llm.gate = threading.Event()
    cog.preparing = True
    planner = threading.Thread(target=cog._prepare)
    planner.start()
    assert cog.on_trigger("player hit") is True
    llm.gate.set()
    planner.join(5)
    settle()
    assert knob.speed == 3 and len(llm.requests) == 1


def test_misses_do_nothing_without_goal_autonomy(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch, autonomy="off")
    assert current.cognition.on_trigger("player hit") is False
    settle()
    assert knob.speed == 1 and llm.requests == []


def test_misses_before_the_model_check_or_while_silenced_are_ignored(
    tmp_path: Path, monkeypatch: object
) -> None:
    current, llm, knob = miss_resident(tmp_path, monkeypatch)
    cog = current.cognition
    cog.provider_ready = None  # the model check has not finished
    assert cog.on_trigger("player hit") is False
    cog.provider_ready = True
    current.quiet_until = time.time() + 60  # samantha.silence()
    assert cog.on_trigger("player hit") is False
    settle()
    assert knob.speed == 1 and llm.requests == []


def test_inspect_sends_miss_lines_past_the_wake_queue() -> None:
    import re

    from samantha._boot import _inspect

    seen: list[str] = []
    current = SimpleNamespace(
        trigger=re.compile("^player hit"),
        cognition=SimpleNamespace(on_trigger=seen.append, submit=lambda event: None),
        sentinels=Sentinels(MetricStore()),
        journal=SimpleNamespace(write=lambda *a, **k: None),
    )
    _inspect(current, "player hit · deaths: 1")
    _inspect(current, "1.0 hits per minute · goal met: 0 (target 1)")
    assert seen == ["player hit · deaths: 1"]


def test_trigger_settings_are_resolved_and_validated(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("SAMANTHA_AUTONOMY_TRIGGER", "SAMANTHA_AUTONOMY_TRIGGER_DWELL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SAMANTHA", "act")
    monkeypatch.setenv("SAMANTHA_AUTONOMY", "goal")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_trigger is None and config.trigger_dwell == 15
    monkeypatch.setenv("SAMANTHA_AUTONOMY_TRIGGER", r"^player hit\b")
    monkeypatch.setenv("SAMANTHA_AUTONOMY_TRIGGER_DWELL", "3")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_trigger == r"^player hit\b"
    assert config.trigger_dwell == 10  # the floor
    assert any("TRIGGER_DWELL" in warning for warning in config.warnings)
    monkeypatch.setenv("SAMANTHA_AUTONOMY_TRIGGER", "player (hit")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_trigger is None
    assert any("not a valid regular expression" in w for w in config.warnings)
    monkeypatch.setenv("SAMANTHA_AUTONOMY_TRIGGER", "^player hit")
    monkeypatch.setenv("SAMANTHA_AUTONOMY", "off")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_trigger is None
    assert any("needs SAMANTHA=act" in w for w in config.warnings)


# --- slack lines (SAMANTHA_AUTONOMY_SLACK) -----------------------------------------


class SlackLLM:
    """Each plan sets the (path, value_json) pairs of the next entry in plans."""

    def __init__(self, *plans: list[tuple[str, str]]) -> None:
        self.plans = list(plans)
        self.contexts: list[dict] = []
        self.chats = 0

    def complete(self, messages: object, signature: str = "default") -> dict:
        return {"assessment": "ok", "actions": []}

    def chat(self, messages: object) -> str:
        self.chats += 1
        return "synthesized"

    def plan_action(self, messages: list[dict]) -> dict[str, object]:
        self.contexts.append(json.loads(messages[0]["content"]))
        pairs = self.plans.pop(0) if self.plans else []
        operations = [
            {"op": "set", "effect": "write", "path": path, "query": None,
             "value_json": value, "args_json": None, "kwargs_json": None,
             "lifetime": "temporary", "duration_s": 1, "reason": "room to spare"}
            for path, value in pairs
        ]
        # "temporary" would undo a give-back by itself; slack turns use "process".
        return {"intent": "act", "reply": "one notch back", "done": True,
                "lifetime": "temporary", "operations": operations}


def slack_resident(tmp_path: Path, monkeypatch: object, llm: SlackLLM,
                   autonomy: str = "goal"):
    """Two knobs that start at speed 1 and size 10, with room on both sides."""
    import samantha

    knobs = SimpleNamespace(speed=1, size=10)
    monkeypatch.setattr(samantha, "_exposures", {})
    samantha.expose("speed", obj=knobs, attr="speed", bounds=(0, 9))
    samantha.expose("size", obj=knobs, attr="size", bounds=(0, 20))
    speed_module(tmp_path, monkeypatch)
    config = SimpleNamespace(
        mode="act", autonomy=autonomy, max_wakes_per_hour=0, token_budget_per_day=0,
        flag_budget=0, act_max_rounds=4, model="test-model", goal="fun",
        autonomy_trigger="^too hard", autonomy_slack="^too easy", trigger_dwell=10.0,
    )
    current = SimpleNamespace(
        config=config, journal=Journal(tmp_path, "run"),
        ledger=Ledger(tmp_path / "ledger.json"), state=tmp_path, root=tmp_path,
    )
    current.actuator = ActController(current, llm)
    current.cognition = Cognition(current, llm)
    current.cognition.provider_ready = True
    return current, knobs


def test_expose_records_the_starting_value(tmp_path: Path, monkeypatch: object) -> None:
    import samantha
    from samantha.act.runtime import RuntimeOperator

    current, knobs = slack_resident(tmp_path, monkeypatch, SlackLLM())
    knobs.speed = 7
    assert samantha._exposures["speed"].start == 1  # the value at expose()
    operator = RuntimeOperator(current)
    operator.inventory()
    starts = {k["path"]: (k["preview"], k["start"]) for k in operator.declared_knobs()}
    assert starts == {"exposed:speed": ("7", 1), "exposed:size": ("10", 10)}
    samantha.expose("hook", setter=lambda value: None)
    assert samantha._exposures["hook"].start is None
    assert current.actuator.off_start() == ["speed"]


def test_slack_line_does_nothing_while_every_knob_is_at_its_start(
    tmp_path: Path, monkeypatch: object
) -> None:
    llm = SlackLLM([("exposed:speed", "0")])
    current, knobs = slack_resident(tmp_path, monkeypatch, llm)
    assert current.cognition.on_slack("too easy: 30 s of play with no hit") is False
    settle()
    assert llm.contexts == [] and knobs.speed == 1  # no model call
    assert current.cognition._trigger_at == float("-inf")  # the dwell clock did not start


def test_slack_line_gives_back_one_knob_toward_its_start(
    tmp_path: Path, monkeypatch: object
) -> None:
    llm = SlackLLM([("exposed:speed", "4"), ("exposed:size", "12")])
    current, knobs = slack_resident(tmp_path, monkeypatch, llm)
    knobs.speed, knobs.size = 7, 16  # earlier changes
    assert current.cognition.on_slack("too easy: 30 s of play with no hit") is True
    settle()
    assert (knobs.speed, knobs.size) == (4, 16)  # one knob only
    assert len(llm.contexts) == 1 and llm.chats == 0  # one planning call, no synthesis
    request = llm.contexts[0]["operator_request"]
    assert "slack pattern '^too easy'" in request and "speed, size" in request
    assert {k["path"]: k["start"] for k in llm.contexts[0]["declared_knobs"]} == {
        "exposed:speed": 1, "exposed:size": 10}
    rows = [r for r in journal_rows(current) if r["type"] == "chat"]
    assert rows[-2]["direction"] == "auto" and rows[-1]["direction"] == "out"
    assert rows[-1]["text"].startswith("Applied runtime intervention")
    assert "exposed:size: one knob per slack turn" in rows[-1]["text"]
    assert any(n.startswith("acted: Applied runtime intervention") for n in
               current.cognition.notices)
    # Audited and revertible like any other change, and it does not expire.
    (group,) = current.actuator.runtime.groups.values()
    assert group.lifetime == "process"
    assert current.actuator.runtime.revert(group.id) and knobs.speed == 7


def test_slack_line_never_moves_a_knob_past_its_start(
    tmp_path: Path, monkeypatch: object
) -> None:
    # In bounds but past the start (1), away from the start, then exactly the start.
    llm = SlackLLM([("exposed:speed", "0")], [("exposed:speed", "8")],
                   [("exposed:speed", "1")])
    current, knobs = slack_resident(tmp_path, monkeypatch, llm)
    cog = current.cognition
    knobs.speed = 7
    for expected in (7, 7, 1):
        cog._trigger_at = float("-inf")  # past the dwell
        assert cog.on_slack("too easy") is True
        settle()
        assert knobs.speed == expected
    texts = [r["text"] for r in journal_rows(current)
             if r["type"] == "chat" and r["direction"] == "out"]
    assert "without going past it" in texts[0] and "without going past it" in texts[1]
    assert texts[2].startswith("Applied runtime intervention")
    cog._trigger_at = float("-inf")
    assert cog.on_slack("too easy") is False  # back at the start: nothing to give back
    assert len(llm.contexts) == 3


def test_slack_and_miss_lines_share_the_dwell(tmp_path: Path, monkeypatch: object) -> None:
    llm = SlackLLM([], [("exposed:speed", "4")])  # the miss turn changes nothing
    current, knobs = slack_resident(tmp_path, monkeypatch, llm)
    cog = current.cognition
    knobs.speed = 7
    assert cog.on_trigger("too hard: 3 hits") is True
    settle()
    assert cog.on_slack("too easy") is False  # inside the dwell of the miss turn
    cog._trigger_at -= 10
    assert cog.on_slack("too easy") is True
    settle()
    assert knobs.speed == 4
    assert cog.on_trigger("too hard: 3 hits") is False  # inside the slack turn's dwell


def test_slack_lines_do_nothing_without_goal_autonomy(
    tmp_path: Path, monkeypatch: object
) -> None:
    llm = SlackLLM([("exposed:speed", "4")])
    current, knobs = slack_resident(tmp_path, monkeypatch, llm, autonomy="off")
    knobs.speed = 7
    assert current.cognition.on_slack("too easy") is False
    current.cognition.resident.config.autonomy = "goal"
    current.quiet_until = time.time() + 60  # samantha.silence()
    assert current.cognition.on_slack("too easy") is False
    settle()
    assert knobs.speed == 7 and llm.contexts == []


def test_inspect_sends_slack_lines_past_the_wake_queue() -> None:
    import re

    from samantha._boot import _inspect

    seen: list[tuple[str, str]] = []
    current = SimpleNamespace(
        trigger=re.compile("^too hard"),
        slack=re.compile("^too easy"),
        cognition=SimpleNamespace(on_trigger=lambda t: seen.append(("miss", t)),
                                  on_slack=lambda t: seen.append(("slack", t)),
                                  submit=lambda event: None),
        sentinels=Sentinels(MetricStore()),
        journal=SimpleNamespace(write=lambda *a, **k: None),
    )
    _inspect(current, "too hard: the first hit")
    _inspect(current, "too easy: 30 s of play with no hit")
    _inspect(current, "1.0 hits per minute (last minute of play) · in the fun band (0.5-2)")
    assert seen == [("miss", "too hard: the first hit"),
                    ("slack", "too easy: 30 s of play with no hit")]


def test_slack_setting_is_resolved_and_validated(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAMANTHA_AUTONOMY_SLACK", raising=False)
    monkeypatch.setenv("SAMANTHA", "act")
    monkeypatch.setenv("SAMANTHA_AUTONOMY", "goal")
    assert Config.resolve(_dotenv_start=tmp_path).autonomy_slack is None  # the default
    monkeypatch.setenv("SAMANTHA_AUTONOMY_SLACK", r"^too easy\b")
    assert Config.resolve(_dotenv_start=tmp_path).autonomy_slack == r"^too easy\b"
    monkeypatch.setenv("SAMANTHA_AUTONOMY_SLACK", "too (easy")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_slack is None
    assert any("SAMANTHA_AUTONOMY_SLACK" in w and "not a valid regular expression" in w
               for w in config.warnings)
    monkeypatch.setenv("SAMANTHA_AUTONOMY_SLACK", "^too easy")
    monkeypatch.setenv("SAMANTHA", "advise")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.autonomy_slack is None
    assert any("SAMANTHA_AUTONOMY_SLACK needs SAMANTHA=act" in w for w in config.warnings)
