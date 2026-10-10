"""The training demo as an unattended story, with a scripted model on
127.0.0.1 (by default). Only the tests and the maintainers use it; the demo
itself is `python demo.py training`.

All else is the real thing: distill/train.py runs with Samantha in act mode.
She reads it, watches its output, flags the starved rare region by herself, and
(SAMANTHA_AUTONOMY=goal) raises sampling_weights.rare in the running process.
With --wait she changes nothing by herself, and the story types two chat lines
("What do you suggest?", "Yes, do it.") as the samantha CLI does.

  python training_story.py             # about 30 s; the plot is out/story/curves.png
  python training_story.py --wait      # the chat path
  python training_story.py --self-test # check the scripted model only

Lines that start with "host |" are the training program's output (every 10th
round, and all Samantha lines). Lines that start with "you |" and "samantha |"
are the chat.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripted import LABEL, format_name, message, scripted_env, serve  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "training-run"
RARE = "exposed:sampling_weights.rare"
CONVERSATION = ["What do you suggest?", "Yes, do it."]
AUTONOMOUS_VALUE = 4.0

# --- the scripted model ------------------------------------------------------


def cold_start() -> dict[str, Any]:
    return {
        "summary": f"{LABEL} distill trains a small tanh MLP student to imitate a teacher "
                   "function from synthetic points made per region (common, edge, rare).",
        "architecture": ["Generator.batch draws per-region quotas from sampling_weights",
                         "Student is a 2-32-32-1 tanh MLP trained with AdamW",
                         "evaluate() reports loss and fidelity per region on fixed probes"],
        "goals": ["every region reaches fidelity 0.95"],
        "watching": ["fidelity.rare against its 0.95 target", "loss.rare vs total loss"],
        "knob_candidates": [f"{RARE} (0.1-5.0)"],
        "protected": ["seed", "teacher", "probe sets"],
        "ambiguities": [],
    }


def wake(prompt: str) -> dict[str, Any]:
    start = prompt.rfind('{"event"')
    event = json.loads(prompt[start:]) if start >= 0 else {}
    evidence = event.get("evidence", {})
    if event.get("event") != "gap" or evidence.get("series") != "fidelity.rare":
        return {"assessment": f"{LABEL} nothing to report", "actions": []}
    value, reference = float(evidence["value"]), float(evidence["reference"])
    return {"assessment": f"{LABEL} fidelity.rare is below target", "actions": [{
        "type": "flag", "flag_id": "",
        "summary": f"{LABEL} fidelity.rare is {value:.2f}, {reference - value:.2f} below "
                   f"its {reference} target: the rare region does not meet the goal.",
        "evidence": f"gap sentinel: fidelity.rare={value}, target={reference}",
        "prediction": "disproved by a later evaluation with fidelity.rare >= 0.95",
    }]}


ADVICE = (f"{LABEL} fidelity/rare is flat near 0.53, far below 0.95, while common and edge "
          "are at target. The rare region gets almost no training points at weight 1.0. I "
          "suggest raising sampling_weights.rare from 1.0 to 4.0 (allowed 0.1-5.0). "
          "Prediction: fidelity/rare passes 0.9 within about 20 rounds, and common and edge "
          "stay above 0.9. Say yes and I will apply it between rounds.")


def _requested_value(text: str) -> float | None:
    match = re.search(r"\bto\s+(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def _advised_value(context: dict[str, Any]) -> float | None:
    """The value from the last advice in the recent chat."""
    for row in reversed(context.get("recent_chat") or []):
        if row.get("direction") == "out":
            match = re.search(r"sampling_weights\.rare from [\d.]+ to (\d+(?:\.\d+)?)",
                              str(row.get("text", "")))
            if match:
                return float(match.group(1))
    return None


def plan(context: dict[str, Any]) -> dict[str, Any]:
    request = str(context.get("operator_request", ""))
    prior = context.get("prior_tool_results") or []
    base = {"done": True, "lifetime": "process"}
    if re.search(r"\b(suggest|recommend|advise|what should)\b", request, re.I):
        return {**base, "intent": "converse", "operations": [], "reply": ADVICE}
    if prior:
        failure = next((r for step in prior for r in step["results"] if not r["ok"]), None)
        detail = failure["detail"] if failure else "no change was needed"
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing changed: {detail}."}
    if request.startswith("Standing approval"):
        value = AUTONOMOUS_VALUE if "fidelity.rare" in request else None
    else:
        value = _requested_value(request)
        if value is None and re.search(r"\b(yes|do it|go ahead|ok|apply)\b", request, re.I):
            value = _advised_value(context)
    if value is None:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Which value should sampling_weights.rare take (0.1-5.0)?"}
    op = {"op": "set", "effect": "write", "path": RARE, "query": None,
          "value_json": json.dumps(value), "args_json": None, "kwargs_json": None,
          "lifetime": "process", "duration_s": None,
          "reason": "the rare region gets no training points at weight 1.0"}
    return {**base, "intent": "act", "operations": [op],
            "reply": f"{LABEL} Setting sampling_weights.rare to {value:g}."}


def chat(content: str) -> str:
    if content.startswith("Answer the operator's request now"):
        start = content.find("{")
        context = json.loads(content[start:]) if start >= 0 else {}
        done = [r for r in context.get("verified_tool_results", []) if r.get("ok")]
        if done:
            r = done[0]
            return (f"{LABEL} Done. {r['path']} is now {r['value']} ({r['detail']}). The "
                    "generator reads it at the start of the next round, with no restart. "
                    "Watch fidelity/rare: it should pass 0.9 within about 20 rounds.")
        return f"{LABEL} Nothing was changed."
    return f"{LABEL} I watch distill's per-region fidelity and loss."


def respond(body: dict[str, Any]) -> dict[str, Any]:
    fmt = format_name(body)
    content = str(body["input"][-1]["content"])
    if fmt == "samantha_cold_start_analysis":
        return message(json.dumps(cold_start()))
    if fmt == "samantha_wake":
        return message(json.dumps(wake(content)))
    if fmt == "samantha_runtime_plan":
        return message(json.dumps(plan(json.loads(content))))
    return message(chat(content))


def self_test() -> int:
    def text(name: str, content: str) -> dict[str, Any]:
        reply = respond({"text": {"format": {"name": name}}, "input": [{"content": content}]})
        return json.loads(reply["output"][0]["content"][0]["text"])

    assert text("samantha_cold_start_analysis", "x")["summary"].startswith(LABEL)
    gap = ('x{"event": "gap", "evidence": {"series": "fidelity.rare", "value": 0.53, '
           '"reference": 0.95}}')
    assert [a["type"] for a in text("samantha_wake", gap)["actions"]] == ["flag"]
    assert text("samantha_wake", 'x{"event": "stall", "evidence": {}}')["actions"] == []
    ask = {"operator_request": "What do you suggest?", "prior_tool_results": []}
    assert text("samantha_runtime_plan", json.dumps(ask))["reply"] == ADVICE
    yes = {"operator_request": "Yes, do it.", "prior_tool_results": [],
           "recent_chat": [{"direction": "out", "text": json.dumps({"reply": ADVICE})}]}
    assert plan(yes)["operations"][0]["value_json"] == "4.0"
    auto = {"operator_request": "Standing approval (SAMANTHA_AUTONOMY=goal): fidelity.rare "
                                "is 0.5375, below its target 0.95. ... apply it now.",
            "prior_tool_results": []}
    assert plan(auto)["operations"][0]["value_json"] == "4.0"
    nine = {"operator_request": "Now set sampling_weights.rare to 9.", "prior_tool_results": []}
    assert plan(nine)["operations"][0]["value_json"] == "9.0"
    nine["prior_tool_results"] = [{"results": [{"ok": False, "detail": "outside bounds"}]}]
    assert "outside bounds" in plan(nine)["reply"]
    print("story self-test: PASS")
    return 0


# --- the run -------------------------------------------------------------------


class Stream:
    """Collect a child's output in the background, and wait for patterns."""

    def __init__(self, pipe, keep) -> None:
        self.text, self.closed, self.keep, self._line = "", False, keep, ""
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, args=(pipe,), daemon=True).start()

    def _pump(self, pipe) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := os.read(pipe.fileno(), 4096):
            text = decoder.decode(chunk)
            with self.lock:
                self.text += text
                self._line += text
                *lines, self._line = self._line.split("\n")
            for line in lines:
                if self.keep(line):
                    print(f"host | {line}", flush=True)
        self.closed = True

    def wait_for(self, pattern: str, timeout: float):
        regex, deadline = re.compile(pattern, re.M), time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                match = regex.search(self.text)
            if match or self.closed:
                return match
            time.sleep(0.1)
        return None


def worth_showing(line: str) -> bool:
    if line.startswith("[samantha]") or "changed live" in line or line.startswith("done "):
        return True
    match = re.match(r"round (\d+)  fidelity", line)
    return bool(match and int(match.group(1)) % 10 == 0)


def talk(pid: int, lines: list[str]) -> list[str]:
    """Send chat lines to the resident in process pid, as the samantha CLI does."""
    os.environ["SAMANTHA"] = "off"  # importing samantha here must not arm a resident
    from samantha.cli import runtime_dir

    replies = []
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(300)
        conn.connect(str(runtime_dir() / f"{pid}.sock"))
        incoming = conn.makefile("rb")
        incoming.readline()  # hello
        for text in lines:
            print(f"you      | {text}", flush=True)
            conn.sendall((json.dumps({"type": "command", "text": text}) + "\n").encode())
            reply = json.loads(incoming.readline())
            for notice in reply.get("notices") or []:
                print(f"samantha | ⚑ {notice}", flush=True)
            answer = str(reply.get("reply") or reply.get("error"))
            replies.append(answer)
            for line in answer.splitlines():
                print(f"samantha | {line}", flush=True)
    return replies


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-test", action="store_true", help="check the scripted model and exit")
    p.add_argument("--live", action="store_true",
                   help="the real model: OPENAI_API_KEY and SAMANTHA_BASE_URL from the "
                        "environment (default: the scripted model)")
    p.add_argument("--wait", action="store_true",
                   help="SAMANTHA_AUTONOMY=off, and type the chat lines (--say)")
    p.add_argument("--round-seconds", type=float, default=0.3,
                   help="minimum time per round (default 0.3; train.py's own default is 1)")
    p.add_argument("--after", type=int, default=25, help="rounds to run after the change")
    p.add_argument("--say", action="append", metavar="TEXT",
                   help="with --wait, a chat line, in order (default: "
                        + " / ".join(CONVERSATION) + ")")
    p.add_argument("--out", default=str(EXAMPLE / "out" / "story"),
                   help="directory for curves.png, history.json, and Samantha's state")
    p.add_argument("--timeout", type=float, default=300, help="seconds to wait for each step")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SAMANTHA="act", SAMANTHA_AUTONOMY="off" if args.wait else "goal",
               SAMANTHA_STATE_DIR=str(out / "state"), MPLBACKEND="Agg",
               PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    server = None
    if not args.live:
        server = serve(respond)
        env.update(scripted_env(server))
    print(f"model: {'real (live)' if args.live else 'scripted'}; output in {out}", flush=True)
    host = subprocess.Popen(
        [sys.executable, "distill/train.py", "--rounds", "400", "--out", str(out),
         "--round-seconds", str(args.round_seconds)],
        cwd=EXAMPLE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stream = Stream(host.stdout, worth_showing)
    checks: dict[str, bool] = {}
    replies: list[str] = []
    try:
        attach = stream.wait_for(r"attach: samantha --pid (\d+)", 60)
        checks["Samantha armed in the training program"] = bool(attach)
        if not attach:
            return 1
        finding = stream.wait_for(r"^\[samantha\] (flag|proposal): ", args.timeout)
        checks["Samantha raised a flag without being asked"] = bool(finding)
        if args.wait:
            replies = talk(int(attach.group(1)), args.say or CONVERSATION)
            checks["the reply has an intervention receipt"] = any(
                "Applied runtime intervention" in r for r in replies)
        change = stream.wait_for(r"^round (\d+)  sampling_weights\.rare changed live",
                                 args.timeout)
        checks["the program saw the live change (no restart)"] = bool(change)
        if change:
            last = int(change.group(1)) + args.after
            stream.wait_for(rf"^round {last:03d}  fidelity", args.timeout)
    finally:
        if host.poll() is None:
            host.send_signal(signal.SIGINT)
            try:
                host.wait(timeout=30)
            except subprocess.TimeoutExpired:
                host.kill()
        if server:
            server.shutdown()

    rows = []
    for path in sorted((out / "state").glob("*/journal-*.jsonl")):
        rows += [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not args.wait:
        auto = [r for r in rows if r.get("type") == "chat" and r.get("direction") == "auto"]
        checks["Samantha changed the run by herself (no chat)"] = bool(auto)
    history = json.loads((out / "history.json").read_text())
    rare = history["fidelity"]["rare"]
    if history["changes"]:
        at = history["changes"][0]["round"]
        before, best = rare[at - 1], max(rare[at:])
        print(f"fidelity/rare: {before:.3f} before round {at}, best {best:.3f} after", flush=True)
        if not args.live:
            checks["fidelity/rare passed 0.9 after the change"] = best >= 0.9
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}: {name}", flush=True)
    print(f"plot: {out / 'curves.png'}", flush=True)
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
