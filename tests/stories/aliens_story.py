"""The aliens demo as an unattended story: a scripted player that is not good
at the game, and (by default) a scripted model on 127.0.0.1. Only the tests and
the maintainers use it; the demo itself is `python demo.py aliens`.

All else is the real thing: examples/aliens/play.py (the panel) runs
game/aliens.py with demo.py's settings, and the real Samantha reads the game,
plans her first move, applies it at the first hit (SAMANTHA_AUTONOMY_TRIGGER),
wakes on the unmet goal, and (SAMANTHA_AUTONOMY=goal) changes the game's knobs
by herself in the running process. The summary has the time from the first hit
to the first change.

  python aliens_story.py --headless            # about 30 s, scripted model
  python aliens_story.py --headless --live     # the real model: OPENAI_API_KEY and
                                               # SAMANTHA_BASE_URL from the environment
  python aliens_story.py --self-test           # check the scripted model only

Before the game starts, this script replaces three pygame functions from the
outside: pg.key.get_pressed (the scripted player), pg.time.Clock (one hook per
frame), and pg.event.get (it posts real KEYDOWN events, for example P to
start). Lines that start with "[story]" go past Samantha's output tap.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import runpy
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripted import LABEL, format_name, message, scripted_env, serve  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PLAY = REPO / "examples" / "aliens" / "play.py"
GOAL = "the player should be hit less than once per minute"

# --- the scripted model ------------------------------------------------------

# The first move (planned at startup, applied at the first hit), then the next
# stage at each later autonomous turn or "help", until every stage is in place.
STAGES = [{"BOMB_ODDS": 300}, {"BOMB_ODDS": 600, "Bomb.speed": 3, "MAX_SHOTS": 8}]
CHANGE = STAGES[-1]
PREPARE_WORDS = r"plan your first move now"
ADVICE = (f"{LABEL} The player is far from the goal: goal.met stays 0, and the game "
          "prints about 6 hits per minute against a goal of under 1. Most hits are "
          "bombs. I suggest three changes, each to the easy end of its range: "
          "BOMB_ODDS 60 to 600, Bomb.speed 9 to 3, MAX_SHOTS 2 to 8. Say help and I "
          "will change them live.")
ADVICE_WORDS = r"\b(suggest|recommend|advise|what should|what would)\b"
ACT_WORDS = r"\b(help|yes|do it|go ahead|ok|apply|fix it|make it easier)\b"


def cold_start() -> dict[str, Any]:
    return {
        "summary": f"{LABEL} aliens.py is pygame's alien shooter. A hit respawns the "
                   "player and prints a 'player hit' line. Every 2 s it prints the hits "
                   "per minute and goal met (target 1).",
        "architecture": ["main() runs a 40 fps loop and reads the module constants each frame",
                         "Bomb.speed moves bombs every frame; BOMB_ODDS sets how often they drop"],
        "goals": [GOAL],
        "watching": ["goal.met against its target 1", "player hit lines"],
        "knob_candidates": ["BOMB_ODDS (60-600)", "MAX_SHOTS (2-8)", "ALIEN_ODDS (22-100)",
                            "Bomb.speed (3-9)", "Alien.speed (4-13)"],
        "protected": ["SCORE", "SCREENRECT"],
        "ambiguities": [],
    }


def wake(event: dict[str, Any]) -> dict[str, Any]:
    """A goal that stays unmet is a finding; nothing else is."""
    evidence = event.get("evidence", {})
    if event.get("event") != "gap" or evidence.get("series") != "goal.met":
        return {"assessment": f"{LABEL} nothing to report", "actions": []}
    value, reference = float(evidence["value"]), float(evidence["reference"])
    return {"assessment": f"{LABEL} the player misses the goal", "actions": [{
        "type": "flag", "flag_id": "",
        "summary": f"{LABEL} The player is hit too often: goal.met is {value:g} "
                   f"(target {reference:g}) and does not move.",
        "evidence": f"gap sentinel: {evidence.get('line', '')}",
        "prediction": "disproved if goal.met reaches 1 with no change to the game",
    }]}


def knob_paths(context: dict[str, Any], change: dict[str, Any]) -> dict[str, str]:
    """The keys of change to the declared knob paths (samantha.expose in the game)."""
    declared = {k.get("path", "") for k in context.get("declared_knobs") or []}
    return {key: f"exposed:{key}" for key in change if f"exposed:{key}" in declared}


def next_stage(context: dict[str, Any]) -> dict[str, Any] | None:
    """The stage after the last one in place (declared_knobs show the values);
    None when the last stage is in place."""
    now = {k.get("path", ""): k.get("preview") for k in context.get("declared_knobs") or []}
    for index in range(len(STAGES) - 1, -1, -1):
        if all(now.get(f"exposed:{key}") == repr(value) for key, value in STAGES[index].items()):
            return STAGES[index + 1] if index + 1 < len(STAGES) else None
    return STAGES[0]


def _op(op: str, path: str, effect: str, **extra: Any) -> dict[str, Any]:
    base = {"op": op, "effect": effect, "path": path, "query": None, "value_json": None,
            "args_json": None, "kwargs_json": None, "lifetime": "process",
            "duration_s": None, "reason": "the player misses the goal"}
    return {**base, **extra}


def plan(context: dict[str, Any]) -> dict[str, Any]:
    request = str(context.get("operator_request", ""))
    prior = context.get("prior_tool_results") or []
    base = {"done": True, "lifetime": "process"}
    preparing = bool(re.search(PREPARE_WORDS, request, re.I))
    if re.search(ADVICE_WORDS, request, re.I):
        return {**base, "intent": "converse", "operations": [], "reply": ADVICE}
    if not preparing and not re.search(ACT_WORDS + r"|miss pattern", request, re.I):
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} I can make the game easier. Say help."}
    failures = [r for step in prior for r in step["results"] if not r["ok"]]
    if failures:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing changed: {failures[0]['detail']}."}
    change = STAGES[0] if preparing else next_stage(context)
    if change is None:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing left to change: every knob I use is at its easy end."}
    paths = knob_paths(context, change)
    if len(paths) < len(change):
        missing = sorted(set(change) - set(paths))
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} I could not find {missing}."}
    ops = [_op("set", paths[key], "write", value_json=json.dumps(value))
           for key, value in change.items()]
    said = ", ".join(f"{key} {value}" for key, value in change.items())
    return {**base, "intent": "act", "operations": ops,
            "reply": f"{LABEL} Making the game easier: {said}."}


def chat(content: str) -> str:
    if content.startswith("Answer the operator's request now"):
        start = content.find("{")
        context = json.loads(content[start:]) if start >= 0 else {}
        done = [r for r in context.get("verified_tool_results", [])
                if r.get("ok") and r.get("op") == "set"]
        if done:
            changed = ", ".join(f"{r['path'].removeprefix('exposed:')} = {r['value']}"
                                for r in done)
            return (f"{LABEL} Done: {changed}. The game reads these every frame, so the "
                    "change is live, with no restart.")
        return f"{LABEL} Nothing was changed."
    return f"{LABEL} I watch the game's hits per minute against the goal of under 1."


def respond(body: dict[str, Any]) -> dict[str, Any]:
    fmt = format_name(body)
    content = str(body["input"][-1]["content"])
    if fmt == "samantha_cold_start_analysis":
        return message(json.dumps(cold_start()))
    if fmt == "samantha_wake":
        start = content.rfind('{"event"')
        return message(json.dumps(wake(json.loads(content[start:]) if start >= 0 else {})))
    if fmt == "samantha_runtime_plan":
        return message(json.dumps(plan(json.loads(content))))
    return message(chat(content))


def self_test() -> int:
    def text(name: str, content: str) -> dict[str, Any]:
        reply = respond({"text": {"format": {"name": name}}, "input": [{"content": content}]})
        return json.loads(reply["output"][0]["content"][0]["text"])

    assert text("samantha_cold_start_analysis", "x")["summary"].startswith(LABEL)
    hit = 'x{"event": "log-novelty", "evidence": {"line": "player hit"}}'
    assert text("samantha_wake", hit)["actions"] == []
    gap = 'x{"event": "gap", "evidence": {"series": "goal.met", "value": 0.0, "reference": 1.0}}'
    assert text("samantha_wake", gap)["actions"][0]["type"] == "flag"
    defaults = {"BOMB_ODDS": 60, "MAX_SHOTS": 2, "ALIEN_ODDS": 22, "Bomb.speed": 9,
                "Alien.speed": 13}
    declared = [{"path": f"exposed:{k}", "preview": repr(v)} for k, v in defaults.items()]
    ctx: dict[str, Any] = {"operator_request": "What do you suggest?", "prior_tool_results": [],
                           "declared_knobs": declared}
    assert text("samantha_runtime_plan", json.dumps(ctx))["reply"] == ADVICE
    ctx["operator_request"] = ("Standing approval (SAMANTHA_AUTONOMY=goal): plan your first "
                               "move now. Nothing changes now.")
    assert {o["path"]: o["value_json"] for o in plan(ctx)["operations"]} == {
        "exposed:BOMB_ODDS": "300"}
    ctx["operator_request"] = ("Standing approval (SAMANTHA_AUTONOMY=goal): goal.met is 0.0, "
                               "below its target 1.0. ... apply it now.")
    assert {o["path"]: o["value_json"] for o in plan(ctx)["operations"]} == {
        "exposed:BOMB_ODDS": "300"}
    declared[0]["preview"] = "300"  # the first move is in place: the next stage
    ctx["operator_request"] = "Standing approval: ... the operator's miss pattern ..."
    assert {o["path"]: o["value_json"] for o in plan(ctx)["operations"]} == {
        "exposed:BOMB_ODDS": "600", "exposed:MAX_SHOTS": "8", "exposed:Bomb.speed": "3"}
    for item in declared:
        name = item["path"].removeprefix("exposed:")
        item["preview"] = repr(CHANGE.get(name, defaults[name]))
    assert plan(ctx)["operations"] == [] and "Nothing left" in plan(ctx)["reply"]
    ctx["prior_tool_results"] = [{"results": [{"op": "set", "ok": False, "detail": "bounds"}]}]
    assert "bounds" in plan(ctx)["reply"]
    assert parse_press("0:p") == (0, "p") and parse_press("200:text-p") == (200, "text-p")
    print("story self-test: PASS")
    return 0


# --- the scripted player and the hooks ----------------------------------------

class Tee:
    """sys.__stdout__ that also keeps each line (the panel's "[demo]" lines)."""

    def __init__(self, stream) -> None:
        self.stream, self.lines, self.buffer = stream, [], ""

    def write(self, text: str) -> int:
        self.buffer += text
        *done, self.buffer = self.buffer.split("\n")
        self.lines += done
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


def say(text: str) -> None:
    """Write to the real stdout, past Samantha's output tap."""
    sys.__stdout__.write(f"[story] {text}\n")
    sys.__stdout__.flush()


class Bot:
    """A scripted, weak player: it wanders left and right, fires often, and
    never looks at bombs, so it gets hit."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed * 7919 + 1)
        self.frame = self.direction = self.hold = 0

    def step(self) -> None:
        self.frame += 1
        if self.hold <= 0:
            self.direction = self.rng.choice((-1, -1, 0, 1, 1))
            self.hold = self.rng.randint(6, 30)
        self.hold -= 1

    def keys(self, pg) -> "Keys":
        return Keys({pg.K_LEFT: self.direction < 0, pg.K_RIGHT: self.direction > 0,
                     pg.K_SPACE: self.frame % 2 == 0})


class Keys:
    def __init__(self, down: dict[int, bool]) -> None:
        self.down = down

    def __getitem__(self, key: int) -> int:
        return int(self.down.get(key, False))


KEYS = ("p", "P", "space", "unicode-p", "text-p", "p+text", "focus-lost", "active-lost")


def parse_press(text: str) -> tuple[int, str]:
    """'FRAME:KEY' -> (FRAME, KEY). Frame 0 is the start screen."""
    frame, _, key = text.partition(":")
    if key not in KEYS:
        raise argparse.ArgumentTypeError(f"KEY must be one of {', '.join(KEYS)}")
    return int(frame), key


def key_events(pg, key: str) -> list:
    """Real pygame events for one key press, as SDL makes them."""
    if key == "focus-lost":  # the window loses the keyboard (you click the terminal)
        return [pg.event.Event(pg.WINDOWFOCUSLOST)]
    if key == "active-lost":  # the same, as the older event
        return [pg.event.Event(pg.ACTIVEEVENT, gain=0, state=2)]
    if key == "text-p":  # only the typed letter (an input method)
        return [pg.event.Event(pg.TEXTINPUT, text="p")]
    if key == "unicode-p":  # a layout where the key code is not K_p
        return [pg.event.Event(pg.KEYDOWN, key=pg.K_UNKNOWN, unicode="p", mod=0, scancode=0)]
    if key == "space":
        return [pg.event.Event(pg.KEYDOWN, key=pg.K_SPACE, unicode=" ", mod=0, scancode=0)]
    down = pg.event.Event(pg.KEYDOWN, key=pg.K_p, unicode=key[0],
                          mod=pg.KMOD_SHIFT if key == "P" else 0, scancode=0)
    if key == "p+text":  # the key, then its text: one press, so one toggle
        return [down, pg.event.Event(pg.TEXTINPUT, text="p")]
    return [down]


def knob_values(module) -> dict[str, float]:
    return {"BOMB_ODDS": module.BOMB_ODDS, "MAX_SHOTS": module.MAX_SHOTS,
            "ALIEN_ODDS": module.ALIEN_ODDS, "Bomb.speed": module.Bomb.speed,
            "Alien.speed": module.Alien.speed}


def samantha_ready() -> bool:
    """True when Samantha has read the game and planned her first move (or is
    off): the game says "Press P"."""
    module = sys.modules.get("samantha")
    cog = getattr(module.resident(), "cognition", None) if module else None
    return cog is None or cog.provider_ready is False or (
        cog.analysis_ready is not None and not cog.preparing)


class Session:
    """Everything that happens once per frame, and what it records."""

    def __init__(self, args: argparse.Namespace, pg) -> None:
        self.args, self.pg = args, pg
        self.frame = 0
        self.bot = None if args.no_bot else Bot(args.seed)
        self.presses = sorted(args.press or [(0, "p")], key=lambda press: press[0])
        self.last_press = 0.0
        self.calls = 0
        self.hits: list[int] = []
        self.hit_times: list[float] = []  # time.monotonic() of each hit
        self.seen: dict[str, float] | None = None
        self.changes: list[dict[str, Any]] = []
        self.play_started: float | None = None
        self.first_unmet: int | None = None
        self.flag_frame: int | None = None
        self.goal_after: list[int] = []
        self.stop_at = args.frames or 0
        self.shots_due: list[tuple[int, str]] = []
        self.talk = Talker(self) if args.say and args.samantha != "off" else None

    def presses_due(self) -> None:
        """Post the next scripted key press when it is due (from pg.event.get)."""
        self.calls += 1
        if not self.presses or time.monotonic() - self.last_press < 0.3:
            return
        if self.frame == 0 and self.calls == 1:
            return  # the first call is the main loop's; press on the start screen
        frame, key = self.presses[0]
        if frame > self.frame:
            return
        if frame == 0 and not samantha_ready():
            return  # like a person who waits for "Press P to play"
        self.presses.pop(0)
        self.last_press = time.monotonic()
        say(f"frame {self.frame:05d}  press {key}")
        for event in key_events(self.pg, key):
            self.pg.event.post(event)

    def shot(self, name: str) -> None:
        if not self.args.shots:
            return
        out = Path(self.args.shots)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{name}.png"
        self.pg.image.save(self.pg.display.get_surface(), str(path))
        say(f"frame {self.frame:05d}  screenshot {path}")

    def on_tick(self, main_locals: dict) -> None:
        now = time.monotonic()
        if self.frame == 0:
            self.play_started = now
        self.frame += 1
        # Hits first: the game prints "player hit" (and Samantha sees it) before
        # this tick, so a change seen in this tick comes after the hit.
        self.hits = main_locals.get("hits") or []  # the game's list of hit frames
        while len(self.hit_times) < len(self.hits):
            self.hit_times.append(now)
            if len(self.hit_times) == 1:
                say(f"frame {self.frame:05d}  first hit")
        module = sys.modules["__main__"]
        values = knob_values(module)
        if self.seen is None:
            self.seen = values
        if values != self.seen:
            moved = {k: (self.seen[k], v) for k, v in values.items() if v != self.seen[k]}
            seconds = now - (self.play_started or now)
            change = {"frame": self.frame, "play_s": round(self.frame / 40, 1),
                      "wall_s": round(seconds, 1), "moved": moved}
            if self.hit_times:  # wall seconds since the first hit and since the last hit
                change["after_first_hit_s"] = round(now - self.hit_times[0], 2)
                change["after_last_hit_s"] = round(now - self.hit_times[-1], 2)
            self.changes.append(change)
            for k, (old, new) in moved.items():
                say(f"frame {self.frame:05d}  {k} changed live: {old:g} → {new:g}"
                    + (f" ({change['after_first_hit_s']:.2f} s after the first hit, "
                       f"{change['after_last_hit_s']:.2f} s after the last hit)"
                       if self.hit_times else " (no hit yet)"))
            self.stop_at = self.frame + self.args.after  # play on after each change
            self.shots_due.append((self.frame + self.args.shot_delay,
                                   f"after-change-{len(self.changes)}"))
            self.seen = values
        if self.frame % 80 == 0:
            minutes = min(self.frame, 2400) / 2400
            rate = len([f for f in self.hits if f > self.frame - 2400]) / minutes
            if rate >= 1 and self.first_unmet is None:
                self.first_unmet = self.frame
            if self.changes:
                self.goal_after.append(int(rate < 1))
            if not self.args.quiet and self.frame % 400 == 0:
                say(f"frame {self.frame:05d}  deaths {main_locals.get('deaths')}  "
                    + "  ".join(f"{k}={v:g}" for k, v in values.items()))
        cog = getattr(getattr(sys.modules.get("samantha"), "resident", lambda: None)(),
                      "cognition", None)
        # A finding she raised by herself: a flag, a question, or a proposal.
        spoke = getattr(cog, "open_flags", None) or any(
            n.startswith(("flag:", "question:", "proposal:"))
            for n in list(getattr(cog, "notices", None) or []))
        if self.flag_frame is None and spoke:
            self.flag_frame = self.frame
            self.shots_due.append((self.frame + 40, "finding"))
        for due in [d for d in self.shots_due if d[0] <= self.frame]:
            self.shots_due.remove(due)
            self.shot(due[1])
        if self.talk:
            self.talk.maybe_start()
        if self.bot:
            self.bot.step()
        if self.args.max_frames and self.frame == self.args.max_frames:
            self.stop_at = self.frame
        if self.stop_at and self.frame >= self.stop_at:
            self.shot("last")
            self.pg.event.post(self.pg.event.Event(self.pg.QUIT))
            self.stop_at = 0


class Talker:
    """Type a line to Samantha through her chat socket, as the samantha CLI does."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.thread: threading.Thread | None = None
        self.replies: list[str] = []

    def maybe_start(self) -> None:
        if self.thread:
            return
        cog = getattr(sys.modules["samantha"].resident(), "cognition", None)
        flagged = bool(getattr(cog, "open_flags", None))
        if flagged or self.session.frame >= self.session.args.talk_at:
            why = "Samantha raised a flag" if flagged else "talk-at frame"
            say(f"frame {self.session.frame:05d}  {why}; typing: {self.session.args.say}")
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self) -> None:
        from samantha.cli import runtime_dir

        path = runtime_dir() / f"{os.getpid()}.sock"
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(300)
            conn.connect(str(path))
            incoming = conn.makefile("rb")
            incoming.readline()  # hello
            request = {"type": "command", "text": self.session.args.say}
            conn.sendall((json.dumps(request) + "\n").encode())
            reply = json.loads(incoming.readline())
        for notice in reply.get("notices") or []:
            say(f"⚑ {notice}")
        text = str(reply.get("reply") or reply.get("error"))
        self.replies.append(text)
        for line in text.splitlines():
            say(f"samantha: {line}")


def journal_rows(state: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(state.glob("*/journal-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-test", action="store_true", help="check the scripted model and exit")
    p.add_argument("--headless", action="store_true",
                   help="no window and no sound (SDL dummy drivers)")
    p.add_argument("--fps", type=int, default=None,
                   help="frame cap (default 40; 400 with --headless and the scripted model)")
    p.add_argument("--live", action="store_true",
                   help="the real model: OPENAI_API_KEY and SAMANTHA_BASE_URL from the "
                        "environment (default: the scripted model)")
    p.add_argument("--seed", type=int, default=3, help="seed for the game and the player")
    p.add_argument("--samantha", choices=("act", "off"), default="act",
                   help="off: only the game and the scripted player")
    p.add_argument("--wait", action="store_true",
                   help="SAMANTHA_AUTONOMY=off: Samantha acts only when spoken to (--say)")
    p.add_argument("--say", default=None, help="a line to type to Samantha (with --wait: help)")
    p.add_argument("--talk-at", type=int, default=4800, metavar="FRAME",
                   help="type at Samantha's first flag, or at this frame (default 4800)")
    p.add_argument("--after", type=int, default=2400, metavar="FRAMES",
                   help="frames to play after the last change (default 2400, 1 min)")
    p.add_argument("--frames", type=int, default=0,
                   help="stop after this many frames if no change stops it first "
                        "(default 2400 with --samantha off, else 9600)")
    p.add_argument("--max-frames", type=int, default=0,
                   help="stop after this many frames of play in any case (0: no limit)")
    p.add_argument("--no-bot", action="store_true", help="no scripted player")
    p.add_argument("--press", type=parse_press, action="append", metavar="FRAME:KEY",
                   help=f"post a key press at FRAME (0: the start screen); KEY: {', '.join(KEYS)}."
                        " Default: 0:p. Repeat for more presses.")
    p.add_argument("--quiet", action="store_true", help="no status line every 400 frames")
    p.add_argument("--timeout", type=float, default=900,
                   help="stop the game and fail after this many seconds (default 900)")
    p.add_argument("--out", default=str(REPO / "examples" / "aliens" / "out"),
                   help="directory for Samantha's state of this run")
    p.add_argument("--shots", default=None, metavar="DIR",
                   help="save screenshots (the window with the panel) to DIR")
    p.add_argument("--shot-delay", type=int, default=160,
                   help="frames after the first change for the after-change screenshot")
    p.add_argument("--summary", default=None, metavar="FILE",
                   help="write the measured timeline as JSON to FILE")
    p.add_argument("--max-latency", type=float, default=5.0, metavar="S",
                   help="the most wall seconds from the first hit to the first change "
                        "(default 5)")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.wait and not args.say:
        args.say = "help"
    if not args.frames:
        args.frames = 2400 if args.samantha == "off" else 9600
    if args.fps is None:
        args.fps = 400 if args.headless and not args.live else 40

    # Run from the repository root, as demo.py does: Samantha takes the first
    # file under the working directory on the import stack as the entry point,
    # and that must be game/aliens.py, not this file.
    os.chdir(REPO)
    os.environ.update(SAMANTHA=args.samantha, PYGAME_HIDE_SUPPORT_PROMPT="1")
    server = None
    state = Path(args.out) / f"state-{time.strftime('%Y%m%d-%H%M%S')}"
    if args.samantha != "off":
        # demo.py's settings (the environment wins, as in demo.py). As demo.py:
        # no SAMANTHA_GOAL; the goal is the game's '# daemon: goal' line.
        sys.path.insert(0, str(REPO))
        try:
            from demo import settings
        finally:
            sys.path.remove(str(REPO))
        demo = settings("aliens", args.wait)
        del demo["SAMANTHA"]  # --samantha sets it
        os.environ.update(SAMANTHA_STATE_DIR=str(state),
                          SAMANTHA_AUTONOMY=demo.pop("SAMANTHA_AUTONOMY"))
        for key, value in demo.items():
            os.environ.setdefault(key, value)
        if not args.live:
            server = serve(respond)
            os.environ.update(scripted_env(server))
        say(f"model: {'real (live)' if args.live else 'scripted'}; Samantha state in {state}")
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
    import pygame as pg

    tee = sys.__stdout__ = Tee(sys.__stdout__)
    session = Session(args, pg)
    real_clock, real_get_pressed, real_event_get = pg.time.Clock, pg.key.get_pressed, pg.event.get

    class HookedClock:
        def __init__(self) -> None:
            self.clock = real_clock()

        def tick(self, framerate: int = 0) -> int:
            session.on_tick(sys._getframe(1).f_locals)
            return self.clock.tick(args.fps)

    def get_pressed():
        return session.bot.keys(pg) if session.bot else real_get_pressed()

    def event_get(*a, **k):
        session.presses_due()
        return real_event_get(*a, **k)

    pg.time.Clock, pg.key.get_pressed, pg.event.get = HookedClock, get_pressed, event_get
    random.seed(args.seed)
    started = time.monotonic()
    timed_out = threading.Event()

    def time_out() -> None:
        timed_out.set()
        say(f"timeout after {args.timeout:g} s; stopping the game")
        pg.event.post(pg.event.Event(pg.QUIT))

    timer = threading.Timer(args.timeout, time_out)
    timer.daemon = True
    timer.start()
    try:
        runpy.run_path(str(PLAY), run_name="__main__")
    except (KeyboardInterrupt, SystemExit):
        pass
    timer.cancel()
    say(f"stopped after {session.frame} frames, {time.monotonic() - started:.0f} s")
    if timed_out.is_set():
        return 1
    if args.samantha == "off":
        return 0
    if session.talk and session.talk.thread:
        session.talk.thread.join(timeout=300)

    rows = journal_rows(state)
    auto = [r for r in rows if r.get("type") == "chat" and r.get("direction") == "auto"]
    replies = session.talk.replies if session.talk else []
    panel = [line for line in tee.lines if line.startswith("[demo] Samantha changed")]
    checks = {"the game saw a live change (no restart)": bool(session.changes),
              "the panel and the terminal showed the change": bool(panel)}
    # Her finding comes from the gap wake, about 50 s of play after the first hit.
    # A live run with miss lines can stop before that (--max-frames).
    misses = bool(os.environ.get("SAMANTHA_AUTONOMY_TRIGGER")) and not args.wait
    if not (misses and args.live):
        checks["Samantha raised a finding by herself"] = session.flag_frame is not None
    if args.wait:
        checks["Samantha answered"] = bool(replies)
        checks["the reply has an intervention receipt"] = any(
            "Applied runtime intervention" in r for r in replies)
    else:
        checks["Samantha changed the game by herself (no chat)"] = bool(auto) and not replies
        first = session.changes[0] if session.changes else {}
        checks["no change before the first hit"] = all(
            "after_first_hit_s" in change for change in session.changes)
        latency = first.get("after_first_hit_s")
        checks[f"the first change came at most {args.max_latency:g} s after the first hit"] = (
            latency is not None and latency <= args.max_latency)
    first_hit = session.hits[0] if session.hits else None
    summary: dict[str, Any] = {
        "model": "live" if args.live else "scripted", "fps": args.fps, "seed": args.seed,
        "frames": session.frame, "first_unmet_frame": session.first_unmet,
        "first_hit_frame": first_hit, "hits": len(session.hits),
        "first_change_after_first_hit_s": (session.changes[0].get("after_first_hit_s")
                                           if session.changes else None),
        "flag_frame": session.flag_frame, "changes": session.changes,
        "autonomous_turns": len(auto), "panel_lines": panel,
        "findings": [f"{r.get('type')}: {r.get('summary') or r.get('body') or ''}"
                     for r in rows if r.get("type") in ("flag", "question", "proposal")],
        "replies_out": [r.get("text") for r in rows if r.get("type") == "chat"
                        and r.get("direction") == "out"],
    }
    if session.changes:
        # The hit frames come from the game; it counts frames only in play.
        # Before her first change, and after her last change.
        at, last, end = session.changes[0]["frame"], session.changes[-1]["frame"], session.frame
        before = sum(f < at for f in session.hits) / (at / 2400)
        after = sum(f >= last for f in session.hits) / max((end - last) / 2400, 1e-9)
        summary.update(hits_per_min_before=round(before, 2), hits_per_min_after=round(after, 2))
        latency = session.changes[0].get("after_first_hit_s")
        say(f"first change at frame {at} ({at / 40:.0f} s of play"
            + (f", {latency:.2f} s after the first hit" if latency is not None else "")
            + f"); {len(session.changes)} change(s); hits per minute: {before:.1f} before the "
            f"first, {after:.1f} after the last; goal met after the first change: "
            f"{'yes' if 1 in session.goal_after else 'not yet'}")
        if not args.live:
            checks["the player is hit at most half as often after her changes"] = (
                after <= before / 2)
    summary["checks"] = checks
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2, default=str))
    for name, ok in checks.items():
        say(f"{'PASS' if ok else 'FAIL'}: {name}")
    if server:
        server.shutdown()
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
