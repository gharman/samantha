import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from samantha import cli


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # AF_UNIX paths are limited to ~104 bytes; pytest's tmp_path is often longer.
    base = Path(tempfile.mkdtemp(prefix="sam-", dir="/tmp"))
    monkeypatch.setattr(cli, "runtime_dir", lambda: base)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_residents_ignore_and_remove_dead_processes(runtime: Path) -> None:
    live = runtime / f"{os.getpid()}.sock"
    stale = runtime / f"{dead_pid()}.sock"
    for path in (live, stale, stale.with_suffix(".pid")):
        path.touch()
    assert cli.residents() == [(os.getpid(), live)]
    assert not stale.exists()
    assert not stale.with_suffix(".pid").exists()


def test_connect_to_socket_without_listener_is_friendly(
    runtime: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = runtime / f"{os.getpid()}.sock"
    orphan = socket.socket(socket.AF_UNIX)
    orphan.bind(str(path))
    orphan.close()  # the file remains, but nothing accepts connections
    assert cli.connect(path) == 1
    assert "no longer running" in capsys.readouterr().out
    assert not path.exists()


def test_overlong_runtime_dir_falls_back_to_a_socket_safe_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/" + "nested/" * 20)
    assert cli.runtime_dir() == Path(f"/tmp/samantha-{os.getuid()}") / "samantha"
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/501")
    assert cli.runtime_dir() == Path("/run/user/501/samantha")
