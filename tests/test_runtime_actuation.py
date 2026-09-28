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
                    "kwargs_json": None,
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
