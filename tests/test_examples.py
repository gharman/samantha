"""Smoke tests for examples/aliens and examples/training-run.

The fast tests skip when pygame or numpy and matplotlib are not installed.
The full offline stories (scripted model, about 30 s each) run only with
RUN_EXAMPLE_STORIES=1.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import samantha

SRC = Path(samantha.__file__).resolve().parent.parent
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ALIENS = EXAMPLES / "aliens"
TRAINING = EXAMPLES / "training-run"


def _needs(*modules: str) -> None:
    for name in modules:
        if importlib.util.find_spec(name) is None:
            pytest.skip(f"{name} is not installed")


def _run(cwd: Path, args: list[str], tmp_path: Path, timeout: float = 120,
         **env: str) -> subprocess.CompletedProcess[str]:
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("SAMANTHA") and not k.endswith("_API_KEY")
    }
    clean.update(PYTHONPATH=str(SRC), HOME=str(tmp_path), PYTHONIOENCODING="utf-8",
                 PYGAME_HIDE_SUPPORT_PROMPT="1", **env)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=clean,
                          capture_output=True, text=True, timeout=timeout)


def _game_lines(stdout: str) -> list[str]:
    return re.findall(r"^\[game\] (paused|resumed)$", stdout, re.M)


@pytest.mark.parametrize("example", [ALIENS, TRAINING], ids=["aliens", "training-run"])
def test_scripted_model_self_test(example: Path, tmp_path: Path) -> None:
    result = _run(example, ["rehearse.py", "--self-test"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rehearse self-test: PASS" in result.stdout


def test_aliens_pause_keys(tmp_path: Path) -> None:
    """Real KEYDOWN and TEXTINPUT events pause and resume, at start and in play."""
    _needs("pygame")
    presses = ["0:p+text",  # P with its text event at the start screen: one toggle
               "200:p", "200:unicode-p",  # by key code, then by typed letter only
               "400:focus-lost", "400:text-p",  # the window loses focus: pause
               "600:active-lost", "600:space", "600:P"]  # Space resumes only at start
    args = ["rehearse.py", "--headless", "--fps", "200", "--samantha", "off",
            "--frames", "800", "--quiet", "--timeout", "90"]
    for press in presses:
        args += ["--press", press]
    result = _run(ALIENS, args, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _game_lines(result.stdout) == ["paused", "resumed"] * 4, result.stdout
    assert "stopped after 800 frames" in result.stdout
    tail = result.stdout[result.stdout.index("press space"):]
    assert tail.index("press P") < tail.index("[game] resumed"), result.stdout


def test_aliens_space_starts_the_game(tmp_path: Path) -> None:
    _needs("pygame")
    result = _run(ALIENS, ["rehearse.py", "--headless", "--samantha", "off", "--frames",
                           "400", "--press", "0:space", "--quiet", "--timeout", "60"],
                  tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _game_lines(result.stdout) == ["paused", "resumed"]
    assert "hits per minute · goal met:" in result.stdout


def test_training_run_rounds(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(TRAINING, ["distill/train.py", "--rounds", "3", "--round-seconds", "0",
                             "--steps", "20", "--out", str(tmp_path / "out")],
                  tmp_path, SAMANTHA="off")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(target 0.95)" in result.stdout
    assert "done after round 002" in result.stdout
    assert (tmp_path / "out" / "curves.png").is_file()


stories = pytest.mark.skipif(os.getenv("RUN_EXAMPLE_STORIES") != "1",
                             reason="set RUN_EXAMPLE_STORIES=1 for the full offline stories")


@stories
def test_aliens_story(tmp_path: Path) -> None:
    _needs("pygame")
    result = _run(ALIENS, ["rehearse.py", "--headless", "--quiet", "--out",
                           str(tmp_path / "out")], tmp_path, timeout=600,
                  XDG_RUNTIME_DIR=str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout


@stories
def test_training_run_story(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(TRAINING, ["rehearse.py", "--out", str(tmp_path / "out")], tmp_path,
                  timeout=600, XDG_RUNTIME_DIR=str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
