"""Run a callable on another live thread without instrumenting the host.

Some objects only work on the thread that created them (sqlite3 connections, GUI
toolkits). Samantha's chat thread cannot call them directly, and asking the host
to add a hook defeats import-and-go. Instead, PEP 669 monitoring enables LINE
events on just the code objects currently on the target thread's stack; the next
line that thread executes runs the job there, and the monitoring is removed.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Callable
from typing import Any

# One injection at a time: each claims a monitoring tool id for its duration.
_injection = threading.Lock()


class InjectionTimeout(TimeoutError):
    pass


def owner_thread(error: BaseException) -> int | None:
    """The thread an object is bound to, when an error says it was the wrong one."""
    text = str(error)
    if match := re.search(r"created in (?:a )?thread id (\d+)", text):
        return int(match.group(1))  # sqlite3
    if re.search(r"main thread|main loop", text, re.I):
        return threading.main_thread().ident  # tkinter and similar
    return None


def run_on_thread(thread_id: int, job: Callable[[], Any], timeout: float = 30.0) -> Any:
    if thread_id == threading.get_ident():
        return job()
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        raise RuntimeError(f"thread {thread_id} is not running")
    codes = set()
    while frame is not None:
        codes.add(frame.f_code)
        frame = frame.f_back
    monitoring = sys.monitoring
    state: dict[str, Any] = {"status": "pending"}
    state_lock = threading.Lock()
    finished = threading.Event()

    def on_line(code: Any, line: int) -> None:
        if threading.get_ident() != thread_id:
            return
        with state_lock:
            if state["status"] != "pending":
                return
            state["status"] = "running"
        try:
            state["result"] = job()
        except BaseException as exc:  # handed back to the caller's thread
            state["error"] = exc
        finally:
            finished.set()

    with _injection:
        tool = next(
            (i for i in (3, 4, 1, 2, 0, 5) if monitoring.get_tool(i) is None), None
        )
        if tool is None:
            raise RuntimeError("no free sys.monitoring tool id")
        monitoring.use_tool_id(tool, "samantha")
        try:
            monitoring.register_callback(tool, monitoring.events.LINE, on_line)
            for code in codes:
                monitoring.set_local_events(tool, code, monitoring.events.LINE)
            if not finished.wait(timeout):
                with state_lock:
                    if state["status"] == "pending":
                        state["status"] = "cancelled"
                if state["status"] == "cancelled":
                    raise InjectionTimeout(
                        f"thread {thread_id} did not execute Python code within "
                        f"{timeout:g}s (blocked in a native call?)"
                    )
                finished.wait()  # already running on the target; let it finish
        finally:
            for code in codes:
                monitoring.set_local_events(tool, code, 0)
            monitoring.register_callback(tool, monitoring.events.LINE, None)
            monitoring.free_tool_id(tool)
    if "error" in state:
        raise state["error"]
    return state["result"]
