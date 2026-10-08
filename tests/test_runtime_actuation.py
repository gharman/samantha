import io
import json
import queue
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from samantha.act.controller import ActController
from samantha.act.introspect import RuntimeGraph
from samantha.act.ledger import Ledger
from samantha.act.runtime import RuntimeOperator
from samantha.observe.logtap import Tee
from samantha.state import Journal


def runtime_module(tmp_path: Path, monkeypatch: object) -> types.ModuleType:
    source = tmp_path / "app.py"
    source.write_text("# live test module\n")
    module = types.ModuleType("runtime_demo")
    module.__file__ = str(source)
    module.config = {"topic": "general", "count": 1}
    module.requests = queue.Queue()
    module.encode = json.dumps
    module.danger = eval

    class Worker:
        def __init__(self) -> None:
            self.topic = "general"
            self.pending: list[str] = []

    Worker.__module__ = module.__name__
    module.Worker = Worker

    def generate(topic: str = "default") -> str:
        return topic

    generate.__module__ = module.__name__
    module.generate = generate

    def render(stage: str, value: str) -> str:
        return f"# {stage.title()}\n{value}"

    render.__module__ = module.__name__
    module.render = render

    def route(stage: str, payload: dict[str, object]) -> dict[str, object]:
        return payload

    route.__module__ = module.__name__
    module.route = route
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def test_retrieval_path_is_not_protected_by_eval_substring(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    module.retrieval_count = 1
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "bump the retrieval counter",
        [
            {
                "op": "set",
                "path": "module:runtime_demo::retrieval_count",
                "value": 2,
            }
        ],
    )
    assert group.results[0].ok
    assert module.retrieval_count == 2


def test_eval_path_is_protected_and_names_the_matched_keyword(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    module.eval_count = 1
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "tamper with eval state",
        [
            {
                "op": "set",
                "path": "module:runtime_demo::eval_count",
                "value": 2,
            }
        ],
    )
    assert not group.results[0].ok
    assert "'eval'" in group.results[0].detail
    assert module.eval_count == 1


def test_suspended_project_instances_are_discoverable(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    worker = module.Worker()
    graph = RuntimeGraph(tmp_path).refresh()
    paths = [
        path
        for path in graph.nodes
        if path.startswith("object:runtime_demo.") and path.endswith(f":{id(worker)}")
    ]
    assert paths
    assert graph.get(f"{paths[0]}/topic").read() == "general"
    assert worker.topic == "general"


class IntIndexTuple(tuple):
    """Like pygame.key.ScancodeWrapper: a tuple that accepts only int indexes."""

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise TypeError("'slice' object cannot be interpreted as an integer")
        return super().__getitem__(index)


def test_tuple_that_rejects_slices_does_not_break_refresh(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    module.keystate = IntIndexTuple((0, 1, 0))
    graph = RuntimeGraph(tmp_path).refresh()
    assert graph.get("module:runtime_demo::keystate/[1]").read() == 1


def test_json_safe_accepts_tuple_that_rejects_slices() -> None:
    from samantha.act.runtime import _json_safe

    assert _json_safe(IntIndexTuple((0, 1))) == [0, 1]


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


def test_conditional_result_transform_changes_only_matching_calls(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "rename only the steelman heading",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::render",
                "kwargs": {},
                "value": {
                    "when": {"stage": "steelman"},
                    "result_replace": {"Steelman": "Steelman for Greg"},
                },
                "lifetime": "process",
            }
        ],
        "process",
    )
    assert group.results[0].verified
    assert module.render("steelman", "case") == "# Steelman for Greg\ncase"
    assert module.render("critique", "case") == "# Critique\ncase"


def test_conditional_argument_transform_changes_live_progress_message(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    seen: list[str] = []

    def progress(message: str) -> None:
        seen.append(message)

    progress.__module__ = module.__name__
    module.progress = progress
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "rename the steelman progress phase",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::progress",
                "value": {
                    "when_contains": {"message": "steelman"},
                    "argument_replace": {
                        "message": {"steelman": "Greg is THE STEEL MAN"}
                    },
                },
                "lifetime": "process",
            }
        ],
        "process",
    )
    assert group.results[0].verified
    module.progress("#45 research")
    module.progress("#45 steelman")
    assert seen == ["#45 research", "#45 Greg is THE STEEL MAN"]


def test_one_shot_interceptor_patches_future_matching_payload(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    original = module.route
    current = resident(tmp_path)
    activated: list[dict[str, object]] = []
    current.observer = SimpleNamespace(
        add_output_rewrite=lambda match, replace, source, lifetime: activated.append(
            {"match": match, "replace": replace, "source": source, "lifetime": lifetime}
        )
    )
    operator = RuntimeOperator(current)
    group = operator.execute(
        "make the next generated item about puppies",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::route",
                "value": {
                    "when": {"stage": "generate"},
                    "argument_append": {
                        "payload": {"constraints": "The idea must be about puppies."}
                    },
                    "activate_output_rewrite": {
                        "match": "steelman",
                        "replace": "greg is the STEEL MAN",
                        "source": None,
                        "lifetime": "one_shot",
                    },
                },
                "lifetime": "one_shot",
            }
        ],
        "one_shot",
    )
    assert group.results[0].verified
    assert module.route("steelman", {"constraints": ["buildable"]}) == {
        "constraints": ["buildable"]
    }
    assert activated == []
    assert module.route is not original
    assert module.route("generate", {"constraints": ["buildable"]}) == {
        "constraints": ["buildable", "The idea must be about puppies."]
    }
    assert module.route is original
    assert activated == [
        {
            "match": "steelman",
            "replace": "greg is the STEEL MAN",
            "source": None,
            "lifetime": "one_shot",
        }
    ]


def test_one_shot_output_rewrite_is_specific_and_expires() -> None:
    output = io.StringIO()
    rules = [
        {
            "id": "test",
            "match": "steelman",
            "replace": "greg is the STEEL MAN",
            "source": "stdout",
            "lifetime": "one_shot",
            "applications": 0,
        }
    ]
    stream = Tee(output, "stdout", lambda source, line: None, rules)
    stream.write("#43 critique\n")
    stream.write("#43 steelman\n")
    stream.write("#44 steelman\n")
    assert output.getvalue() == (
        "#43 critique\n#43 greg is the STEEL MAN\n#44 steelman\n"
    )
    assert rules == []


class _Terminal(io.StringIO):
    encoding = "utf-8"
    errors = "strict"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1


def test_tee_presents_wrapped_terminal_interface() -> None:
    terminal = _Terminal()
    lines: list[str] = []
    stream = Tee(terminal, "stdout", lambda source, line: lines.append(line))
    assert stream.isatty() is True
    assert stream.fileno() == 1
    assert stream.encoding == "utf-8"
    assert stream.errors == "strict"
    assert stream.writable() is True
    stream.writelines(["a\n", "b\n"])
    print("c", file=stream)
    assert terminal.getvalue() == "a\nb\nc\n"
    assert lines == ["a", "b", "c"]


def test_reachable_external_api_accepts_live_object_references(
    tmp_path: Path, monkeypatch: object
) -> None:
    runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "serialize the live configuration through the application's API",
        [
            {
                "op": "call",
                "path": "module:runtime_demo::encode",
                "args": [{"$ref": "module:runtime_demo::config"}],
            }
        ],
    )
    assert group.results[0].verified
    assert json.loads(group.results[0].value) == {"topic": "general", "count": 1}


def test_explicit_execution_primitives_remain_barred(
    tmp_path: Path, monkeypatch: object
) -> None:
    runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "evaluate code",
        [{"op": "call", "path": "module:runtime_demo::danger", "args": ["1+1"]}],
    )
    assert not group.results[0].ok
    assert "barred execution primitive" in group.results[0].detail


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
                    "effect": "write",
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


class ResultLLM:
    def __init__(self) -> None:
        self.final_context = ""
        self.planning_calls = 0

    def plan_action(self, messages: object) -> dict[str, object]:
        self.planning_calls += 1
        if self.planning_calls > 1:
            return {
                "intent": "converse",
                "reply": "The retrieved topic is neuroevolution.",
                "done": True,
                "lifetime": "contextual",
                "operations": [],
            }
        return {
            "intent": "inspect",
            "reply": "I’ll retrieve it.",
            "done": True,
            "lifetime": "one_shot",
            "operations": [
                {
                    "op": "call",
                    "effect": "read",
                    "path": "module:runtime_demo::generate",
                    "query": None,
                    "value_json": None,
                    "args_json": None,
                    "kwargs_json": '{"topic":"neuroevolution"}',
                    "lifetime": "one_shot",
                    "reason": "retrieve generated content",
                }
            ],
        }

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.final_context = messages[0]["content"]
        return "The retrieved topic is neuroevolution."


def test_callable_result_is_synthesized_into_chat_answer(
    tmp_path: Path, monkeypatch: object
) -> None:
    runtime_module(tmp_path, monkeypatch)
    llm = ResultLLM()
    controller = ActController(resident(tmp_path), llm)
    reply = controller.handle("what topic was generated?")
    assert "The retrieved topic is neuroevolution." in reply
    assert llm.planning_calls == 2


def test_act_planner_receives_prior_chat_history(
    tmp_path: Path, monkeypatch: object
) -> None:
    runtime_module(tmp_path, monkeypatch)
    current = resident(tmp_path)
    current.journal.write("chat", direction="in", text="assess idea 41")
    current.journal.write("chat", direction="out", text="retrieving idea 41")

    class HistoryLLM:
        context = ""

        def plan_action(self, messages: list[dict[str, str]]) -> dict[str, object]:
            self.context = messages[0]["content"]
            return {
                "intent": "converse",
                "reply": "Idea 41 is still the active subject.",
                "done": True,
                "lifetime": "contextual",
                "operations": [],
            }

    llm = HistoryLLM()
    reply = ActController(current, llm).handle("ok, and?")
    assert reply == "Idea 41 is still the active subject."
    assert "assess idea 41" in llm.context
    assert "retrieving idea 41" in llm.context


def test_read_call_does_not_complete_an_action_request(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)

    class InspectThenActLLM:
        calls = 0

        def plan_action(self, messages: object) -> dict[str, object]:
            self.calls += 1
            operation = (
                {
                    "op": "call",
                    "effect": "read",
                    "path": "module:runtime_demo::generate",
                    "query": None,
                    "value_json": None,
                    "args_json": None,
                    # Each read asks for something new, so every round makes progress.
                    "kwargs_json": json.dumps({"topic": f"idea {self.calls}"}),
                    "lifetime": "contextual",
                    "reason": "inspect behavior",
                }
                if self.calls < 10
                else {
                    "op": "set",
                    "effect": "write",
                    "path": 'module:runtime_demo::config/"topic"',
                    "query": None,
                    "value_json": '"puppies"',
                    "args_json": None,
                    "kwargs_json": None,
                    "lifetime": "one_shot",
                    "reason": "apply requested behavior",
                }
            )
            return {
                "intent": "inspect" if self.calls < 10 else "act",
                "reply": "",
                "done": True,
                "lifetime": "contextual",
                "operations": [operation],
            }

    llm = InspectThenActLLM()
    reply = ActController(resident(tmp_path), llm).handle("make it puppies")
    assert llm.calls == 10
    assert module.config["topic"] == "puppies"
    assert "assignment verified" in reply


def running_loop(
    tmp_path: Path, monkeypatch: object
) -> tuple[types.ModuleType, object]:
    import importlib.util
    import threading

    source = tmp_path / "stable_paths_app.py"
    source.write_text(
        "import time\n"
        "cfg = {'lr': 0.03}\n"
        "state = {'stop': False, 'tick': 0}\n"
        "def leaf():\n"
        "    time.sleep(0.0005)\n"
        "def loop(cfg):\n"
        "    while not state['stop']:\n"
        "        state['tick'] += 1\n"
        "        leaf() if state['tick'] % 2 else time.sleep(0.0005)\n"
    )
    spec = importlib.util.spec_from_file_location("stable_paths_app", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "stable_paths_app", module)
    thread = threading.Thread(target=module.loop, args=(module.cfg,), daemon=True)
    thread.start()
    return module, thread


def test_frame_paths_are_stable_while_the_thread_moves(
    tmp_path: Path, monkeypatch: object
) -> None:
    module, thread = running_loop(tmp_path, monkeypatch)
    try:
        seen = set()
        for _ in range(30):
            graph = RuntimeGraph(tmp_path).refresh()
            seen.update(
                path
                for path in graph.nodes
                if path.startswith("frame:") and path.endswith("::cfg")
            )
        assert len(seen) == 1
    finally:
        module.state["stop"] = True
        thread.join(1)


def test_shared_objects_resolve_through_every_path(
    tmp_path: Path, monkeypatch: object
) -> None:
    module, thread = running_loop(tmp_path, monkeypatch)
    try:
        operator = RuntimeOperator(resident(tmp_path))
        group = operator.execute(
            "raise the learning rate",
            [{"op": "set", "path": 'module:stable_paths_app::cfg/"lr"', "value": 0.1}],
        )
        assert group.results[0].ok, group.results[0].detail
        assert module.cfg["lr"] == 0.1
        described = operator.graph.describe("module:stable_paths_app::cfg")
        assert described["same_object_as"].startswith("frame:")
    finally:
        module.state["stop"] = True
        thread.join(1)


def test_methods_of_live_objects_are_callable_and_interceptable(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)

    class Client:
        def __init__(self) -> None:
            self.jobs = 3

        def status(self, verbose: bool = False) -> str:
            return f"healthy: {self.jobs} jobs" + (" (verbose)" if verbose else "")

    Client.__module__ = module.__name__
    module.Client = Client
    module.client = Client()
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "check status", [{"op": "call", "path": "module:runtime_demo::client/status"}]
    )
    assert group.results[0].ok, group.results[0].detail
    assert group.results[0].value == "healthy: 3 jobs"
    described = operator.graph.describe("module:runtime_demo::client/status")
    assert described["callable"] and not described["mutable"]
    group = operator.execute(
        "always be verbose",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::Client/status",
                "kwargs": {"verbose": True},
                "lifetime": "process",
            }
        ],
    )
    assert group.results[0].ok, group.results[0].detail
    assert module.client.status() == "healthy: 3 jobs (verbose)"


def test_instance_scan_is_limited_to_project_classes(
    tmp_path: Path, monkeypatch: object
) -> None:
    from collections import OrderedDict

    module = runtime_module(tmp_path, monkeypatch)
    module.OrderedDict = OrderedDict  # an imported library class
    noise = [OrderedDict(value=index) for index in range(3_000)]
    workers = [module.Worker() for _ in range(5)]
    graph = RuntimeGraph(tmp_path).refresh()
    roots = [path for path in graph.nodes if path.startswith("object:")]
    assert not any("OrderedDict" in path for path in roots)
    for worker in workers:
        assert any(path.endswith(f":{id(worker)}") for path in roots)
    assert noise


class MalformedValueLLM:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def plan_action(self, messages: list[dict[str, str]]) -> dict[str, object]:
        self.contexts.append(json.loads(messages[0]["content"]))
        operations = []
        if len(self.contexts) == 1:
            operations = [
                {
                    "op": "set",
                    "effect": "write",
                    "path": 'module:runtime_demo::config/"topic"',
                    "query": None,
                    "value_json": "fast",  # not JSON: a bare word
                    "args_json": None,
                    "kwargs_json": None,
                    "lifetime": "process",
                    "reason": "operator asked",
                }
            ]
        return {
            "intent": "act" if operations else "converse",
            "reply": "done" if operations else "I could not apply that value.",
            "done": True,
            "lifetime": "process",
            "operations": operations,
        }


def test_malformed_value_json_fails_instead_of_assigning_none(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    llm = MalformedValueLLM()
    reply = ActController(resident(tmp_path), llm).handle("make the topic fast")
    assert module.config["topic"] == "general"
    assert "Applied runtime intervention" not in reply
    failure = llm.contexts[1]["prior_tool_results"][0]["results"][0]
    assert failure["ok"] is False
    assert "invalid value_json 'fast'" in failure["detail"]


def test_natural_language_queries_rank_relevant_live_paths_first(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    module.filler = {f"setting_{index}": index for index in range(300)}
    module.sampling_weights = {"head_0": 1.0, "head_1": 0.25}
    graph = RuntimeGraph(tmp_path).refresh()
    ranked = graph.snapshot("please increase the sampling weight for head_1", limit=5)
    assert ranked[0]["path"] == 'module:runtime_demo::sampling_weights/"head_1"'
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "find it", [{"op": "search", "path": "", "query": "head_1 sampling weight"}]
    )
    found = [item["path"] for item in group.results[0].value]
    assert found and all("sampling_weights" in path for path in found)


def cafe_module(tmp_path: Path, monkeypatch: object) -> types.ModuleType:
    from dataclasses import dataclass, field

    module = runtime_module(tmp_path, monkeypatch)

    @dataclass
    class EspressoMachine:
        pressure_bar: float = 4.0

    @dataclass
    class Cafe:
        machine: EspressoMachine = field(default_factory=EspressoMachine)

        def announce(self, message: str) -> str:
            return f"{message} at {self.machine.pressure_bar} bar"

    for cls in (EspressoMachine, Cafe):
        cls.__module__ = module.__name__
        setattr(module, cls.__name__, cls)
    module.cafe = Cafe()
    return module


def test_class_attribute_shadowed_by_instances_is_not_reported_verified(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = cafe_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "raise the pressure",
        [
            {
                "op": "set",
                "path": "module:runtime_demo::EspressoMachine/pressure_bar",
                "value": 9.0,
            }
        ],
    )
    result = group.results[0]
    assert result.ok and not result.verified
    assert "shadowed by 1 live instance" in result.detail
    assert "module:runtime_demo::cafe/machine/pressure_bar" in result.detail
    assert module.cafe.machine.pressure_bar == 4.0
    # The class change is still recorded so it can be undone.
    assert operator.revert(group.id)
    assert module.EspressoMachine.pressure_bar == 4.0


def test_unshadowed_class_attribute_set_is_still_verified(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    module.Worker.retries = 2
    worker = module.Worker()
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "retry more",
        [{"op": "set", "path": "module:runtime_demo::Worker/retries", "value": 5}],
    )
    assert group.results[0].verified, group.results[0].detail
    assert worker.retries == 5


def test_instance_paths_rank_above_the_class_attributes_they_shadow(
    tmp_path: Path, monkeypatch: object
) -> None:
    cafe_module(tmp_path, monkeypatch)
    graph = RuntimeGraph(tmp_path).refresh()
    paths = [item["path"] for item in graph.snapshot("espresso machine pressure")]
    instance = "module:runtime_demo::cafe/machine/pressure_bar"
    default = "module:runtime_demo::EspressoMachine/pressure_bar"
    assert paths.index(instance) < paths.index(default)
    described = graph.describe(default)
    assert described["scope"] == "class"
    assert described["shadowed_by"] == [instance]
    assert "scope" not in graph.describe(instance)

    paths = [item["path"] for item in graph.snapshot("cafe announce")]
    bound = "module:runtime_demo::cafe/announce"
    assert paths.index(bound) < paths.index("module:runtime_demo::Cafe/announce")
    assert graph.describe("module:runtime_demo::Cafe/announce")["shadowed_by"] == [
        bound
    ]


def plan(*operations: dict[str, object]) -> dict[str, object]:
    return {
        "intent": "act",
        "reply": "",
        "done": True,
        "lifetime": "process",
        "operations": [
            {
                "effect": "write",
                "path": None,
                "query": None,
                "value_json": None,
                "args_json": None,
                "kwargs_json": None,
                "lifetime": "process",
                "reason": "operator asked",
                **operation,
            }
            for operation in operations
        ],
    }


def test_operator_can_undo_the_last_intervention_through_chat(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)

    class UndoLLM:
        def __init__(self) -> None:
            self.contexts: list[dict[str, object]] = []

        def plan_action(self, messages: list[dict[str, str]]) -> dict[str, object]:
            context = json.loads(messages[0]["content"])
            self.contexts.append(context)
            if context["operator_request"] == "make the topic evolution":
                return plan(
                    {
                        "op": "set",
                        "path": 'module:runtime_demo::config/"topic"',
                        "value_json": '"evolution"',
                    }
                )
            return plan({"op": "revert", "path": "last"})

    llm = UndoLLM()
    controller = ActController(resident(tmp_path), llm)
    controller.handle("make the topic evolution")
    assert module.config["topic"] == "evolution"
    reply = controller.handle("undo that")
    assert module.config["topic"] == "general"
    active = llm.contexts[-1]["active_interventions"]
    assert [change for group in active for change in group["changes"]] == [
        'set module:runtime_demo::config/"topic"'
    ]
    assert "reverted" in reply
    assert controller.runtime.revertible() == []


def test_revert_removes_unfired_one_shot_interceptors_and_output_rules(
    tmp_path: Path, monkeypatch: object
) -> None:
    from samantha.observe import Observer

    module = runtime_module(tmp_path, monkeypatch)
    current = resident(tmp_path)
    current.observer = Observer(current.journal)
    operator = RuntimeOperator(current)
    original = module.generate
    group = operator.execute(
        "change the next topic and label it",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::generate",
                "kwargs": {"topic": "puppies"},
                "lifetime": "one_shot",
            },
            {"op": "rewrite_output", "value": {"match": "Topic", "replace": "Theme"}},
        ],
        "one_shot",
    )
    assert module.generate is not original
    assert current.observer.streamtap.rewrites
    assert operator.revert(group.id)
    assert module.generate is original
    assert current.observer.streamtap.rewrites == []


def test_async_calls_run_on_the_hosts_event_loop(
    tmp_path: Path, monkeypatch: object
) -> None:
    import asyncio
    import threading

    module = runtime_module(tmp_path, monkeypatch)
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    class Service:
        def __init__(self) -> None:
            # Bound to the host loop, like a client session or connection pool.
            self.answer = loop.create_future()

        async def fetch(self) -> str:
            return await self.answer

    def host() -> None:
        asyncio.set_event_loop(loop)
        module.service = Service()
        # Still pending when Samantha awaits it, so the await must suspend on this loop.
        loop.call_later(0.2, module.service.answer.set_result, "from the host loop")
        module.fetch = module.service.fetch
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=host, daemon=True)
    thread.start()
    ready.wait(5)
    try:
        operator = RuntimeOperator(resident(tmp_path))
        group = operator.execute(
            "fetch", [{"op": "call", "path": "module:runtime_demo::fetch"}]
        )
        result = group.results[0]
        assert result.ok, result.detail
        assert result.value == "from the host loop"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def test_interceptors_reach_callers_that_imported_the_function(
    tmp_path: Path, monkeypatch: object
) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    caller_source = tmp_path / "caller.py"
    caller_source.write_text("from runtime_demo import generate\n")
    caller = types.ModuleType("runtime_caller")
    caller.__file__ = str(caller_source)
    caller.generate = module.generate  # what `from runtime_demo import generate` does
    monkeypatch.setitem(sys.modules, caller.__name__, caller)
    original = module.generate
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "make the next topic puppies",
        [
            {
                "op": "intercept",
                "path": "module:runtime_demo::generate",
                "kwargs": {"topic": "puppies"},
                "lifetime": "one_shot",
            }
        ],
        "one_shot",
    )
    assert "+1 imported references" in group.results[0].detail
    stale = caller.generate  # a reference taken while the interceptor was live
    assert caller.generate("anything") == "puppies"
    assert caller.generate is original and module.generate is original
    assert stale("anything") == "anything"


def test_declared_knobs_are_offered_and_bounds_checked(
    tmp_path: Path, monkeypatch: object
) -> None:
    import samantha

    module = runtime_module(tmp_path, monkeypatch)
    monkeypatch.setattr(samantha, "_exposures", {})
    samantha.expose("topic_count", obj=module, attr="retries", bounds=(0, 5))
    module.retries = 1
    operator = RuntimeOperator(resident(tmp_path))
    operator.inventory()
    knobs = operator.declared_knobs()
    assert [(k["path"], k["bounds"]) for k in knobs] == [
        ("exposed:topic_count", (0, 5))
    ]
    ok = operator.execute(
        "more retries", [{"op": "set", "path": "exposed:topic_count", "value": 3}]
    )
    assert ok.results[0].ok and module.retries == 3
    bad = operator.execute(
        "way more", [{"op": "set", "path": "exposed:topic_count", "value": 50}]
    )
    assert not bad.results[0].ok and "outside declared bounds" in bad.results[0].detail
    assert module.retries == 3


class RepeatingReadLLM:
    def __init__(self) -> None:
        self.calls = 0

    def plan_action(self, messages: object) -> dict[str, object]:
        self.calls += 1
        return {
            "intent": "inspect",
            "reply": "The topic is still general.",
            "done": False,
            "lifetime": "contextual",
            "operations": [
                {
                    "op": "inspect",
                    "effect": "read",
                    "path": 'module:runtime_demo::config/"topic"',
                    "query": None,
                    "value_json": None,
                    "args_json": None,
                    "kwargs_json": None,
                    "lifetime": "contextual",
                    "reason": "check again",
                }
            ],
        }


def test_repeating_unchanged_reads_stops_planning(
    tmp_path: Path, monkeypatch: object
) -> None:
    runtime_module(tmp_path, monkeypatch)
    llm = RepeatingReadLLM()
    reply = ActController(resident(tmp_path), llm).handle("what is the topic?")
    assert llm.calls == 2
    assert reply == "The topic is still general."


class ChangingReadLLM(RepeatingReadLLM):
    """Reads a value the host keeps changing, so every round is new evidence."""

    def __init__(self, module: types.ModuleType) -> None:
        super().__init__()
        self.module = module

    def plan_action(self, messages: object) -> dict[str, object]:
        self.module.config["count"] += 1
        plan = super().plan_action(messages)
        plan["operations"][0]["path"] = 'module:runtime_demo::config/"count"'
        return plan


def test_act_planning_rounds_are_capped(tmp_path: Path, monkeypatch: object) -> None:
    module = runtime_module(tmp_path, monkeypatch)
    current = resident(tmp_path)
    current.config.act_max_rounds = 3
    llm = ChangingReadLLM(module)
    reply = ActController(current, llm).handle("watch the count")
    assert llm.calls == 3
    assert "limit (3)" in reply


def test_temporary_interventions_expire(tmp_path: Path, monkeypatch: object) -> None:
    import time

    module = runtime_module(tmp_path, monkeypatch)
    operator = RuntimeOperator(resident(tmp_path))
    group = operator.execute(
        "try evolution briefly",
        [
            {
                "op": "set",
                "path": 'module:runtime_demo::config/"topic"',
                "value": "evolution",
                "duration_s": 0.2,
            }
        ],
        "temporary",
    )
    assert module.config["topic"] == "evolution"
    assert group.summary()["expires_in_s"] >= 0
    deadline = time.monotonic() + 3
    while group.status == "active" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert group.status == "reverted"
    assert module.config["topic"] == "general"


def test_until_failure_reverts_on_failure_signals(
    tmp_path: Path, monkeypatch: object
) -> None:
    from samantha._boot import _inspect
    from samantha.observe.metrics import MetricStore
    from samantha.sentinels import Sentinels

    module = runtime_module(tmp_path, monkeypatch)
    current = resident(tmp_path)
    current.sentinels = Sentinels(MetricStore())
    current.actuator = SimpleNamespace(runtime=RuntimeOperator(current))
    operator = current.actuator.runtime
    change = [
        {"op": "set", "path": 'module:runtime_demo::config/"topic"', "value": "x"}
    ]
    operator.execute("risky change", change, "until_failure")
    _inspect(current, "WARNING queue is slow")  # novel, but not a failure
    assert module.config["topic"] == "x"
    _inspect(current, "ERROR queue worker crashed")
    assert module.config["topic"] == "general"

    def explode(topic: str = "default") -> str:
        raise RuntimeError("boom")

    explode.__module__ = module.__name__
    module.explode = explode
    group = operator.execute(
        "retry harder until it breaks",
        [
            *change,
            {
                "op": "intercept",
                "path": "module:runtime_demo::explode",
                "kwargs": {"topic": "x"},
                "lifetime": "until_failure",
            },
        ],
        "until_failure",
    )
    try:
        module.explode()
    except RuntimeError:
        pass
    assert group.status == "reverted"
    assert module.explode is explode and module.config["topic"] == "general"


def test_thread_bound_objects_are_called_on_their_owning_thread(
    tmp_path: Path, monkeypatch: object
) -> None:
    import sqlite3
    import threading
    import time

    module = runtime_module(tmp_path, monkeypatch)
    ready, stop = threading.Event(), threading.Event()

    def host() -> None:
        module.db = sqlite3.connect(":memory:")
        module.db.execute("create table steps (loss real)")
        module.db.executemany("insert into steps values (?)", [(0.9,), (0.7,)])
        ready.set()
        while not stop.is_set():  # a host loop doing ordinary Python work
            time.sleep(0.005)

    def losses() -> list[float]:
        return [row[0] for row in module.db.execute("select loss from steps")]

    losses.__module__ = module.__name__
    module.losses = losses
    thread = threading.Thread(target=host, daemon=True)
    thread.start()
    ready.wait(5)
    try:
        operator = RuntimeOperator(resident(tmp_path))
        group = operator.execute(
            "show losses", [{"op": "call", "path": "module:runtime_demo::losses"}]
        )
        result = group.results[0]
        assert result.ok, result.detail
        assert result.value == [0.9, 0.7]
        assert "owning thread" in result.detail
    finally:
        stop.set()
        thread.join(5)


def test_injection_times_out_without_running_later() -> None:
    import threading

    import pytest

    from samantha.act.onthread import InjectionTimeout, run_on_thread

    release, started = threading.Event(), threading.Event()
    ran: list[bool] = []

    def blocked() -> None:
        started.set()
        release.wait(5)  # stuck in a native wait: no Python lines execute
        for _ in range(3):
            pass

    thread = threading.Thread(target=blocked, daemon=True)
    thread.start()
    started.wait(5)
    with pytest.raises(InjectionTimeout):
        run_on_thread(thread.ident, lambda: ran.append(True), timeout=0.2)
    release.set()
    thread.join(5)
    assert ran == []


def test_directive_names_extracts_constants_and_class_attrs() -> None:
    from samantha.act.controller import _directive_names

    directives = [
        "MAX_SHOTS in 1..8",
        "Bomb.speed in 3..9; main() reads them each frame",
        "MAX_SHOTS again",
    ]
    assert _directive_names(directives) == "MAX_SHOTS Bomb.speed"
    assert _directive_names([]) == ""


class RankingRuntime:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.paths = [f"frame:{i}:main:x" for i in range(400)] + ["module:g::BOMB_ODDS"]

    def inventory(self, query: str = "", limit: int = 300) -> list[str]:
        self.queries.append(query)
        words = query.split()
        hits = [p for p in self.paths if any(w in p for w in words)]
        rest = [p for p in self.paths if p not in hits]
        return (hits + rest)[:limit]

    def declared_knobs(self) -> list[object]:
        return []

    def revertible(self) -> list[object]:
        return []


def ranking_controller(directives: list[str]) -> ActController:
    controller = ActController.__new__(ActController)
    controller.runtime = RankingRuntime()
    controller.resident = object()
    controller._project_theory = lambda: ""
    controller._recent_chat = lambda limit=30: []
    controller._source_structure = lambda: {"directives": directives}
    return controller


def test_context_ranks_paths_named_by_directives(monkeypatch: object) -> None:
    monkeypatch.setattr("samantha.act.controller.operator_intent", lambda r: "")
    controller = ranking_controller(["BOMB_ODDS in 1..9"])
    paths = controller._context("help", [], 1)["live_paths"]
    assert "module:g::BOMB_ODDS" in paths

    plain = ranking_controller([])
    paths = plain._context("help", [], 1)["live_paths"]
    assert "module:g::BOMB_ODDS" not in paths
    assert plain.runtime.queries == ["help"]
