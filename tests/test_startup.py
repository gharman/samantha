import os
import subprocess
import sys
from pathlib import Path

import pytest

import samantha
from samantha._boot import Config

SRC = Path(samantha.__file__).resolve().parent.parent

HOST_MAIN = """\
import samantha

print("armed" if samantha.resident() else "unarmed", flush=True)
"""

SPAWN_HOST = """\
import multiprocessing as mp

import samantha


def child(queue):
    queue.put("armed" if samantha.resident() else "unarmed")


if __name__ == "__main__":
    mp.set_start_method("spawn")
    queue = mp.Queue()
    worker = mp.Process(target=child, args=(queue,))
    worker.start()
    print("parent", "armed" if samantha.resident() else "unarmed", flush=True)
    print("child", queue.get(timeout=60), flush=True)
    worker.join()
"""


def _run(
    tmp_path: Path, args: list[str], **env: str
) -> subprocess.CompletedProcess[str]:
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("SAMANTHA") and not k.endswith("_API_KEY")
    }
    clean.update(
        PYTHONPATH=str(SRC),
        HOME=str(tmp_path),
        SAMANTHA="observe",
        SAMANTHA_STATE_DIR=str(tmp_path / "state"),
    )
    clean.update(env)
    return subprocess.run(
        [sys.executable, *args],
        cwd=tmp_path / "host",
        env=clean,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def host(tmp_path: Path) -> Path:
    package = tmp_path / "host" / "myapp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(HOST_MAIN)
    (tmp_path / "host" / "spawner.py").write_text(SPAWN_HOST)
    return tmp_path


def test_host_run_with_dash_m_arms(host: Path) -> None:
    result = _run(host, ["-m", "myapp"])
    assert result.stdout.strip() == "armed", result.stderr


def test_samantha_cli_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    package = Path(samantha.__file__).resolve().parent
    cases = [
        (["python", "-m", "samantha", "--pid", "1"], ["-m"], True),
        (["python", "-X", "dev", "-m", "samantha"], ["-m"], True),
        (["python", "-m", "myapp"], ["-m"], False),
        (["python", "app.py", "-m", "samantha"], ["app.py"], False),
        (["/venv/bin/samantha"], ["/venv/bin/samantha"], True),
        (
            ["python", str(package / "__main__.py")],
            [str(package / "__main__.py")],
            True,
        ),
        (["python", "/srv/myapp/__main__.py"], ["/srv/myapp/__main__.py"], False),
    ]
    for orig, argv, expected in cases:
        monkeypatch.setattr(sys, "orig_argv", orig)
        monkeypatch.setattr(sys, "argv", argv)
        assert samantha._invoked_as_cli() is expected, orig


def test_malformed_numeric_settings_fall_back_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAMANTHA_ACT_MAX_ROUNDS", "five")
    monkeypatch.setenv("SAMANTHA_LLM_TIMEOUT", "30s")
    monkeypatch.setenv("SAMANTHA_FLAG_BUDGET", " 7 ")
    config = Config.resolve(_dotenv_start=tmp_path)
    assert config.act_max_rounds == 0
    assert config.llm_timeout == 0
    assert config.flag_budget == 7
    assert len(config.warnings) == 2
    assert any("SAMANTHA_ACT_MAX_ROUNDS='five'" in w for w in config.warnings)


def test_malformed_setting_still_arms_and_warns(host: Path) -> None:
    result = _run(host, ["-m", "myapp"], SAMANTHA_ACT_MAX_ROUNDS="five")
    assert result.stdout.strip() == "armed", result.stderr
    assert "ignoring SAMANTHA_ACT_MAX_ROUNDS='five'" in result.stderr


def test_startup_failure_is_reported_once(host: Path) -> None:
    blocker = host / "not-a-dir"
    blocker.write_text("")
    result = _run(host, ["-m", "myapp"], SAMANTHA_STATE_DIR=str(blocker))
    assert result.returncode == 0
    assert result.stdout.strip() == "unarmed"
    assert result.stderr.count("[samantha] disabled: ") == 1


def test_startup_failure_respects_quiet(host: Path) -> None:
    blocker = host / "not-a-dir"
    blocker.write_text("")
    result = _run(
        host, ["-m", "myapp"], SAMANTHA_STATE_DIR=str(blocker), SAMANTHA_QUIET="1"
    )
    assert result.stdout.strip() == "unarmed"
    assert "[samantha]" not in result.stderr


def test_spawn_children_do_not_arm(host: Path) -> None:
    result = _run(host, ["spawner.py"])
    lines = result.stdout.splitlines()
    assert lines == ["parent armed", "child unarmed"], result.stderr


def test_spawn_children_arm_when_opted_in(host: Path) -> None:
    result = _run(host, ["spawner.py"], SAMANTHA_ARM_CHILDREN="1")
    lines = result.stdout.splitlines()
    assert lines == ["parent armed", "child armed"], result.stderr
