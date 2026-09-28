import json
import os
from pathlib import Path

from samantha._boot import Config
from samantha import _application_entry
from samantha.observe.metrics import MetricStore
from samantha.observe.source import read_source
from samantha.state import Journal, redact, state_root


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


def test_dotenv_openai_key_arms_configuration(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=test-key\nSAMANTHA_MODEL=test-model\n"
    )
    config = Config.resolve()
    assert config.api_key == "test-key"
    assert config.model == "test-model"


def test_dotenv_can_be_loaded_from_discovered_root(
    tmp_path: Path, monkeypatch: object
) -> None:
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".env").write_text("OPENAI_API_KEY=root-key\n")
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = Config.resolve(_dotenv_start=project)
    assert config.api_key == "root-key"
    assert config.dotenv_file == str(project / ".env")


def test_anthropic_key_is_not_used_as_openai_bearer_token(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    config = Config.resolve()
    assert config.api_key is None


def test_dotenv_does_not_leak_unrelated_keys_into_host_environment(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=test-key\nDEBUG=true\nDATABASE_URL=postgres://evil\n"
    )
    Config.resolve()
    assert "DEBUG" not in os.environ
    assert "DATABASE_URL" not in os.environ


def test_dotenv_stops_at_given_root_and_does_not_walk_up_to_parents(
    tmp_path: Path, monkeypatch: object
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / ".env").write_text("OPENAI_API_KEY=ancestor-key\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = Config.resolve(_dotenv_start=project)
    assert config.api_key is None
    assert config.dotenv_file is None


def test_dotenv_strips_unquoted_inline_comment(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-abc123 # prod key\n")
    config = Config.resolve()
    assert config.api_key == "sk-abc123"


def test_dotenv_keeps_hash_inside_quoted_value(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAMANTHA_GOAL", raising=False)
    (tmp_path / ".env").write_text('SAMANTHA_GOAL="ship #23 and #24"\n')
    config = Config.resolve()
    assert config.goal == "ship #23 and #24"


def test_quiet_environment_disables_resident_console_logging(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("SAMANTHA_QUIET", "true")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.log_level == "OFF"


def test_cognition_and_actuation_are_unbudgeted_by_default(
    tmp_path: Path, monkeypatch: object
) -> None:
    for name in (
        "SAMANTHA_LLM_TIMEOUT",
        "SAMANTHA_ACT_MAX_ROUNDS",
        "SAMANTHA_MAX_WAKES_PER_HOUR",
        "SAMANTHA_TOKEN_BUDGET_PER_DAY",
        "SAMANTHA_FLAG_BUDGET",
    ):
        monkeypatch.delenv(name, raising=False)
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.llm_timeout == 0
    assert config.act_max_rounds == 0
    assert config.max_wakes_per_hour == 0
    assert config.token_budget_per_day == 0
    assert config.flag_budget == 0


def test_source_inventory_follows_local_imports_and_comments(tmp_path: Path) -> None:
    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    entry = tmp_path / "app.py"
    entry.write_text('import demo.worker\napp(help="Runs the demo queue.")\n')
    (package / "worker.py").write_text(
        '"""Processes jobs from the durable queue."""\n'
        "# daemon: keep queue lag below 20\n"
        "def work():\n    return 1\n"
    )
    inventory = read_source(entry, tmp_path)
    assert "src/demo/worker.py" in inventory["files"]
    assert inventory["directives"] == ["keep queue lag below 20"]
    assert inventory["files"]["src/demo/worker.py"]["outline"][0]["name"] == "work"
    assert inventory["files"]["app.py"]["help_strings"] == ["Runs the demo queue."]


def test_default_memory_is_dot_samantha(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SAMANTHA_STATE_DIR", raising=False)
    path = state_root("project-key")
    assert path == tmp_path / ".samantha" / "project-key"
    assert path.is_dir()
    assert path.parent.stat().st_mode & 0o077 == 0


def test_application_entry_ignores_virtual_environment_shims() -> None:
    entry = Path(_application_entry() or "")
    assert entry.name == "test_m0_m1.py"
    assert ".venv" not in entry.parts


def test_dotenv_numeric_settings_are_parsed_leniently(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("SAMANTHA_ACT_MAX_ROUNDS", raising=False)
    monkeypatch.delenv("SAMANTHA_LLM_TIMEOUT", raising=False)
    (tmp_path / ".env").write_text(
        "SAMANTHA_ACT_MAX_ROUNDS=five\nSAMANTHA_LLM_TIMEOUT=45 # seconds\n"
    )
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.act_max_rounds == 0
    assert config.llm_timeout == 45.0
    assert config.warnings == [
        "ignoring SAMANTHA_ACT_MAX_ROUNDS='five': expected a number; using 0"
    ]


def test_observer_retains_bounded_output(tmp_path: Path) -> None:
    import tracemalloc

    from samantha.observe import Observer

    observer = Observer(Journal(tmp_path, "run"))
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for index in range(5_000):
        observer.observe("stdout", f"{index:06d}" + "-" * 1_000)
    after, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(observer.recent) == 200
    assert after - before < 2_000_000  # an unbounded buffer retains ~5 MB here


def test_journal_holds_one_handle_and_serves_recent_rows(
    tmp_path: Path, monkeypatch: object
) -> None:
    opened = []
    original_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    journal = Journal(tmp_path, "run")
    journal.write("chat", direction="in", text="what is the loss doing?")
    for step in range(1_000):
        journal.write("observation", source="stdout", payload={"step": step})
    journal.close()
    assert opened.count(journal.path) == 1
    assert len(journal.path.read_text().splitlines()) == 1_001
    assert journal.recent(1)[0]["payload"] == {"step": 999}
    assert journal.recent(5, "chat")[0]["text"] == "what is the loss doing?"


def test_metric_extraction_is_linear_on_long_tokens() -> None:
    import time

    metrics = MetricStore()
    started = time.perf_counter()
    found = metrics.ingest("payload=" + "x" * 20_000 + ", loss=0.5")
    assert time.perf_counter() - started < 0.1  # quadratic backtracking took ~24 s
    assert found == [{"series": "loss", "value": 0.5}]


def test_host_basic_config_works_and_logged_metrics_are_observed_once(
    tmp_path: Path,
) -> None:
    import logging

    from samantha.observe import Observer

    observer = Observer(Journal(tmp_path, "run"))
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    for handler in saved[1]:
        root.removeHandler(handler)
    observer.start()
    try:
        # A host that configures logging after `import samantha`.
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        assert root.handlers, "basicConfig must still install the host's handler"
        logging.getLogger("host").info("loss=0.5")
        logging.getLogger("host").debug("loss=0.7")  # below the configured level
    finally:
        observer.stop()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        root.setLevel(saved[0])
        for handler in saved[1]:
            root.addHandler(handler)
    assert list(observer.metrics.series["loss"]) == [0.5]


def test_source_inventory_works_under_hidden_directories_and_skips_venvs(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".projects" / "app"
    (root / "pkg").mkdir(parents=True)
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "main.py").write_text("import pkg.helpers\n")
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "helpers.py").write_text('"""Helpers."""\n')
    (root / "tools.py").write_text('"""Not imported by the entry point."""\n')
    (root / ".venv" / "lib" / "vendored.py").write_text("x = 1\n")
    inventory = read_source(root / "main.py", root)
    assert set(inventory["files"]) == {
        "main.py",
        "pkg/__init__.py",
        "pkg/helpers.py",
        "tools.py",
    }
