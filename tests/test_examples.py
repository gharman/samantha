"""Tests for demo.py and the two demos (examples/aliens, examples/training-run).

The fast tests skip when pygame (pygame-ce) or numpy and matplotlib are not
installed (pip install -e ".[demos]"). The full offline stories in
tests/stories/ (scripted model, about 30 s each) run only with
RUN_EXAMPLE_STORIES=1.
"""

import importlib.util
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import samantha

SRC = Path(samantha.__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parent.parent
STORIES = REPO / "tests" / "stories"
TRAINING = REPO / "examples" / "training-run"


def _needs(*modules: str) -> None:
    for name in modules:
        if importlib.util.find_spec(name) is None:
            pytest.skip(f"{name} is not installed")


def _env(tmp_path: Path, **env: str) -> dict[str, str]:
    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("SAMANTHA") and not k.endswith("_API_KEY")
    }
    clean.update(PYTHONPATH=str(SRC), HOME=str(tmp_path), PYTHONIOENCODING="utf-8",
                 PYGAME_HIDE_SUPPORT_PROMPT="1", SDL_VIDEODRIVER="dummy",
                 SDL_AUDIODRIVER="dummy", MPLBACKEND="Agg", XDG_RUNTIME_DIR=str(tmp_path),
                 **env)
    return clean


def _run(cwd: Path, args: list[str], tmp_path: Path, timeout: float = 120,
         **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=_env(tmp_path, **env),
                          capture_output=True, text=True, timeout=timeout)


def _game_lines(stdout: str) -> list[str]:
    return re.findall(r"^\[game\] (paused|resumed)$", stdout, re.M)


@pytest.mark.parametrize("story", ["aliens_story.py", "training_story.py"])
def test_scripted_model_self_test(story: str, tmp_path: Path) -> None:
    result = _run(STORIES, [story, "--self-test"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "story self-test: PASS" in result.stdout


def test_demo_without_a_key_says_where_to_put_it(tmp_path: Path) -> None:
    # demo.py reads .env from the repository root; a developer may have one
    # there, so copy demo.py to a directory with no .env.
    _needs("pygame")
    (tmp_path / "demo.py").write_text((REPO / "demo.py").read_text())
    result = _run(tmp_path, ["demo.py", "aliens"], tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "No OpenAI API key" in result.stdout
    assert "cp .env.example .env" in result.stdout


def test_demo_help_lists_both_demos(tmp_path: Path) -> None:
    result = _run(REPO, ["demo.py", "--help"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "aliens" in result.stdout and "training" in result.stdout
    assert "--wait" in result.stdout


def test_env_example_has_the_key_name_and_no_key(tmp_path: Path) -> None:
    text = (REPO / ".env.example").read_text()
    assert re.search(r"^OPENAI_API_KEY=$", text, re.M)
    assert ".env" in (REPO / ".gitignore").read_text().split()


def test_aliens_pause_keys(tmp_path: Path) -> None:
    """Real KEYDOWN and TEXTINPUT events pause and resume, at start and in play."""
    _needs("pygame")
    presses = ["0:p+text",  # P with its text event at the start screen: one toggle
               "200:p", "200:unicode-p",  # by key code, then by typed letter only
               "400:focus-lost", "400:text-p",  # the window loses focus: pause
               "600:active-lost", "600:space", "600:P"]  # Space resumes only at start
    args = ["aliens_story.py", "--headless", "--fps", "200", "--samantha", "off",
            "--frames", "800", "--quiet", "--timeout", "90"]
    for press in presses:
        args += ["--press", press]
    result = _run(STORIES, args, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _game_lines(result.stdout) == ["paused", "resumed"] * 4, result.stdout
    assert "stopped after 800 frames" in result.stdout
    tail = result.stdout[result.stdout.index("press space"):]
    assert tail.index("press P") < tail.index("[game] resumed"), result.stdout


def test_aliens_space_starts_the_game_and_the_panel_runs(tmp_path: Path) -> None:
    _needs("pygame")
    shots = tmp_path / "shots"
    result = _run(STORIES, ["aliens_story.py", "--headless", "--samantha", "off",
                            "--frames", "400", "--press", "0:space", "--quiet",
                            "--timeout", "60", "--shots", str(shots)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _game_lines(result.stdout) == ["paused", "resumed"]
    assert "hits per minute · goal met:" in result.stdout
    assert "[demo] aliens: press P to play" in result.stdout
    import pygame

    image = pygame.image.load(str(shots / "last.png"))
    assert image.get_size() == (640, 480 + 136)  # the game, and the panel under it


def test_panel_reads_samantha_lines() -> None:
    _needs("pygame")
    sys.path.insert(0, str(REPO / "examples" / "aliens"))
    try:
        import play
    finally:
        sys.path.pop(0)
    feed = play.Feed()
    handler = play.SamanthaLog(feed)

    def log(text: str, level: int = logging.INFO) -> None:
        handler.emit(logging.LogRecord("samantha", level, "", 0, text, None, None))

    log("flag: The player is hit 6 times per minute.")
    log("acted: Applied runtime intervention ab12 (process):\n"
        "✓ set exposed:BOMB_ODDS: assignment verified → 600\n"
        "✓ set frame:1:main:6::lastalien/facing: assignment verified → -8")
    log("resident armed · mode=act")
    log("autonomous act turn failed: boom", logging.WARNING)
    assert feed.snapshot() == [
        ("say", "Samantha: The player is hit 6 times per minute."),
        ("change", "Samantha set lastalien.facing -> -8"),
        ("error", "Samantha: autonomous act turn failed: boom"),
    ]


def test_training_run_rounds(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(TRAINING, ["distill/train.py", "--rounds", "3", "--round-seconds", "0",
                             "--steps", "20", "--out", str(tmp_path / "out")],
                  tmp_path, SAMANTHA="off")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(target 0.95)" in result.stdout
    assert "done after round 002" in result.stdout
    assert (tmp_path / "out" / "curves.png").is_file()


def test_training_window_falls_back_to_the_png_without_a_display(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(TRAINING, ["distill/train.py", "--window", "--rounds", "2",
                             "--round-seconds", "0", "--steps", "10", "--out",
                             str(tmp_path / "out")], tmp_path, SAMANTHA="off")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No display for a plot window" in result.stdout
    assert (tmp_path / "out" / "curves.png").is_file()


stories = pytest.mark.skipif(os.getenv("RUN_EXAMPLE_STORIES") != "1",
                             reason="set RUN_EXAMPLE_STORIES=1 for the full offline stories")


@stories
def test_aliens_story_samantha_acts_by_herself(tmp_path: Path) -> None:
    _needs("pygame")
    result = _run(STORIES, ["aliens_story.py", "--headless", "--quiet", "--out",
                            str(tmp_path / "out")], tmp_path, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert "[demo] Samantha changed BOMB_ODDS: 60 → 600" in result.stdout


@stories
def test_training_story_samantha_acts_by_herself(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(STORIES, ["training_story.py", "--out", str(tmp_path / "out")], tmp_path,
                  timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout


@stories
def test_training_story_chat_path(tmp_path: Path) -> None:
    _needs("numpy", "matplotlib")
    result = _run(STORIES, ["training_story.py", "--wait", "--out", str(tmp_path / "out")],
                  tmp_path, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
