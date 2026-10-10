"""Tests for demo.py and the two demos (examples/aliens, examples/training-run).

The fast tests skip when pygame (pygame-ce) or numpy and matplotlib are not
installed (pip install -e ".[demos]"). The full offline stories in
tests/stories/ (scripted model, about 30 s each) run only with
RUN_EXAMPLE_STORIES=1.
"""

import importlib.util
import json
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


STATUS = "1.0 hits per minute (last minute of play) · in the fun band (0.5-2)"


def test_demo_settings_too_hard_and_too_easy_lines_only_in_the_aliens_demo() -> None:
    sys.path.insert(0, str(REPO))
    try:
        import demo
    finally:
        sys.path.remove(str(REPO))
    aliens = demo.settings("aliens")
    assert aliens["SAMANTHA_AUTONOMY"] == "goal"
    miss, slack = aliens["SAMANTHA_AUTONOMY_TRIGGER"], aliens["SAMANTHA_AUTONOMY_SLACK"]
    for line in ("too hard: the first hit", "too hard: 3 hits in the last minute of play"):
        assert re.search(miss, line) and not re.search(slack, line)
    assert re.search(slack, "too easy: 30 s of play with no hit")
    assert not re.search(miss, "too easy: 30 s of play with no hit")
    for line in ("player hit · deaths: 2 · score: 7", STATUS):
        assert not re.search(miss, line) and not re.search(slack, line)
    assert aliens["SAMANTHA_AUTONOMY_TRIGGER_DWELL"] == "10"
    # --wait (she acts only when asked) and the training demo: no such lines.
    waiting = demo.settings("aliens", wait=True)
    assert "SAMANTHA_AUTONOMY_TRIGGER" not in waiting
    assert "SAMANTHA_AUTONOMY_SLACK" not in waiting
    assert demo.settings("training") == {"SAMANTHA": "act", "SAMANTHA_AUTONOMY": "goal",
                                         "SAMANTHA_AUTONOMY_DWELL": "90"}


def test_aliens_status_line_is_not_a_metric() -> None:
    """The hits-per-minute line gives no metric series, so it starts no gap or
    stall wake (a model call); the too hard and too easy lines do the work."""
    from samantha.observe.metrics import MetricStore

    metrics = MetricStore()
    for _ in range(30):
        assert metrics.ingest(STATUS) == []
        assert metrics.ingest("0.0 hits per minute (last minute of play) · below the fun "
                              "band (0.5-2)") == []
    assert metrics.ingest("too hard: the first hit") == []


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
    assert "hits per minute (last minute of play) · " in result.stdout
    assert "[demo] aliens: press P to play" in result.stdout
    import pygame

    image = pygame.image.load(str(shots / "last.png"))
    assert image.get_size() == (640, 480 + 136)  # the game, and the panel under it


def test_aliens_prints_too_hard_and_too_easy_lines(tmp_path: Path) -> None:
    """The first hit prints "too hard", and CRUISE_SECONDS of play with no hit
    prints "too easy" (here 5 s, from ALIENS_CRUISE_SECONDS)."""
    _needs("pygame")
    result = _run(STORIES, ["aliens_story.py", "--headless", "--samantha", "off",
                            "--frames", "2400", "--quiet", "--timeout", "60"], tmp_path,
                  ALIENS_CRUISE_SECONDS="5")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    first_hit = lines.index("player hit · deaths: 1 · score: 5")
    assert lines[first_hit + 1] == "too hard: the first hit"
    assert "too hard: the first hit" not in lines[first_hit + 2:]
    easy = [line for line in lines if line.startswith("too easy: ")]
    assert easy and all(re.fullmatch(r"too easy: \d+ s of play with no hit", e) for e in easy)
    assert all(int(e.split()[2]) >= 5 for e in easy)


def test_panel_says_easier_and_harder(monkeypatch: object) -> None:
    """A change back toward the starting values is "harder", with a playful line."""
    sys.path.insert(0, str(REPO / "examples" / "aliens"))
    try:
        import play
    finally:
        sys.path.pop(0)
    from types import SimpleNamespace

    game = SimpleNamespace(BOMB_ODDS=60, MAX_SHOTS=2, ALIEN_ODDS=22,
                           Bomb=SimpleNamespace(speed=9), Alien=SimpleNamespace(speed=13))
    monkeypatch.setitem(sys.modules, "__main__", game)
    feed = play.Feed()
    panel = play.Panel(None, feed)
    panel.watch_knobs()  # the starting values
    game.BOMB_ODDS, game.Bomb.speed = 195, 7
    panel.watch_knobs()
    game.BOMB_ODDS = 60
    panel.watch_knobs()
    assert feed.snapshot() == [
        ("change", "Samantha changed BOMB_ODDS 60 -> 195, Bomb.speed 9 -> 7 (easier)"),
        ("say", f"Samantha: {play.CRUISING[0]}"),
        ("change", "Samantha changed BOMB_ODDS 195 -> 60 (harder)"),
    ]
    assert play.CRUISING[0] == "You're cruising. Bringing the aliens back."


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
    log("acted: No safe change applied: every knob is at its most protective bound.")
    assert feed.snapshot() == [
        ("say", "Samantha: The player is hit 6 times per minute."),
        ("change", "Samantha set lastalien.facing -> -8"),
        ("error", "Samantha: autonomous act turn failed: boom"),
        ("say", "Samantha: No safe change applied: every knob is at its most protective "
                "bound."),
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
def test_aliens_story_samantha_acts_at_the_first_hit(tmp_path: Path) -> None:
    """(a) The first move is planned before P; the first hit applies it at once.
    It is one notch on two knobs, not every knob to its easy end."""
    _needs("pygame")
    summary = tmp_path / "summary.json"
    result = _run(STORIES, ["aliens_story.py", "--headless", "--quiet", "--out",
                            str(tmp_path / "out"), "--summary", str(summary),
                            "--max-latency", "2"], tmp_path, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    # [samantha] lines
    assert "first move ready: BOMB_ODDS 60 → 195, Bomb.speed 9 → 7" in result.stderr
    assert "slack lines: /^too easy\\b/" in result.stderr
    assert "[demo] Samantha changed BOMB_ODDS: 60 → 195 (live, no restart, easier)" in (
        result.stdout)
    data = json.loads(summary.read_text())
    first = data["changes"][0]
    assert first["moved"] == {"BOMB_ODDS": [60, 195], "Bomb.speed": [9, 7]}
    assert first["way"] == "easier"
    # Quiet until the first hit, then the change within 2 s (no model call).
    assert data["checks"]["no change before the first hit"]
    assert 0 <= data["first_change_after_first_hit_s"] <= 2
    assert first["frame"] >= data["first_hit_frame"]
    assert data["checks"]["no knob was ever harder than at the start"]


@stories
def test_aliens_story_harder_when_the_player_cruises(tmp_path: Path) -> None:
    """(b) 30 s of play with no hit: one notch harder, one knob, back toward the
    start. (c) Never harder than the start: the give-back stops at the starting
    values, and a request past them is refused."""
    _needs("pygame")
    summary = tmp_path / "summary.json"
    result = _run(STORIES, ["aliens_story.py", "--headless", "--quiet", "--dodge", "--out",
                            str(tmp_path / "out"), "--summary", str(summary),
                            "--max-latency", "2"], tmp_path, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    data = json.loads(summary.read_text())
    assert data["fps"] == 100 and data["cruise_s"] == 30
    ways = [(c["way"], c["moved"]) for c in data["changes"]]
    assert ways == [("easier", {"BOMB_ODDS": [60, 195], "Bomb.speed": [9, 7]}),
                    ("harder", {"BOMB_ODDS": [195, 60]}),
                    ("harder", {"Bomb.speed": [7, 9]})]
    first, second, third = data["changes"]
    assert 0 <= data["first_change_after_first_hit_s"] <= 2
    # (b) Each harder change came at the next "too easy" line (every 30 s of play
    # with no hit), at once (the scripted planning call is fast).
    assert 30 <= second["play_s_since_last_hit"] < 60
    assert 60 <= third["play_s_since_last_hit"] < 90
    assert 0 <= second["after_too_easy_s"] <= 2 and 0 <= third["after_too_easy_s"] <= 2
    assert data["harder_steps"] == 2 and data["easier_steps"] == 1
    # (c) Back at the start, later "too easy" lines changed nothing; no knob was
    # ever harder than at the start; Alien.speed 15 was refused.
    assert data["knobs_at_start_at_end"]
    assert data["too_easy_frames_with_no_turn_after_the_last_change"]
    assert data["beyond_start"] == []
    assert any("Alien.speed: 15 is outside (4, 13)" in t for t in data["replies_out"])
    assert "[demo] Samantha: You're cruising. Bringing the aliens back." in result.stdout
    assert "[demo] Samantha changed BOMB_ODDS: 195 → 60 (live, no restart, harder)" in (
        result.stdout)


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
