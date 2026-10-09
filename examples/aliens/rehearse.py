"""Run the aliens example offline: a scripted model and a scripted player.

No API key and no network. A small server on 127.0.0.1 answers Samantha's
model requests with fixed text that starts with "(scripted model)". All else
is the real Samantha: it reads the game, watches its output, wakes on the
unmet goal, takes the chat message "help", plans, changes the game's
constants in the running process, and writes the change to its journal.

  python rehearse.py                # a window, normal speed (about 5 minutes)
  python rehearse.py --headless     # no window, as fast as possible (for CI)
  python rehearse.py --self-test    # check the scripted model only

The game file is not changed for this. Before the game starts, this script
replaces three pygame functions from the outside: pg.key.get_pressed (the
scripted player), pg.time.Clock (one hook per frame), and pg.event.get (it
posts real KEYDOWN events, for example P to start). Lines that start with
"[rehearse]" go past Samantha's output tap, so Samantha does not see them.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GAME = HERE / "game" / "aliens.py"
GOAL = "the player should be hit less than once per minute"
LABEL = "(scripted model)"

# --- the scripted model ------------------------------------------------------

CHANGE = {"BOMB_ODDS": 600, "Bomb/speed": 3, "MAX_SHOTS": 8}
ADVICE = (f"{LABEL} The player is far from the goal: goal.met stays 0, and the game "
          "prints about 6 hits per minute against a goal of under 1. Most hits are "
          "bombs. I suggest three changes, each to the easy end of its range: "
          "BOMB_ODDS 60 to 600, Bomb.speed 9 to 3, MAX_SHOTS 2 to 8. Say help and I "
          "will change them live.")
ADVICE_WORDS = r"\b(suggest|recommend|advise|what should|what would)\b"
ACT_WORDS = r"\b(help|yes|do it|go ahead|ok|apply|fix it|make it easier)\b"


def _message(text: str) -> dict[str, Any]:
    """One Responses API payload with this output text."""
    return {"id": "resp_scripted", "object": "response", "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}]}


def cold_start() -> dict[str, Any]:
    return {
        "summary": f"{LABEL} aliens.py is pygame's alien shooter. A hit respawns the "
                   "player and prints a 'player hit' line. Every 5 s it prints the hits "
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
    """One hit is not a finding; a goal that stays unmet is."""
    evidence = event.get("evidence", {})
    if event.get("event") != "gap" or evidence.get("series") != "goal.met":
        return {"assessment": f"{LABEL} nothing to report", "actions": []}
    value, reference = float(evidence["value"]), float(evidence["reference"])
    return {"assessment": f"{LABEL} the player misses the goal", "actions": [{
        "type": "flag",
        "summary": f"{LABEL} The player does not meet the goal: goal.met is {value:g} "
                   f"(target {reference:g}) and does not move.",
        "evidence": f"gap sentinel: goal.met={value}, target={reference}",
        "prediction": "disproved if goal.met reaches 1 with no change to the game",
    }]}


def _knob_paths(context: dict[str, Any]) -> dict[str, str]:
    """CHANGE keys to the live paths that Samantha listed or found."""
    seen = [item.get("path", "") for item in context.get("live_paths") or []]
    for step in context.get("prior_tool_results") or []:
        for result in step.get("results", []):
            if result.get("op") == "search" and isinstance(result.get("value"), list):
                seen += [item.get("path", "") for item in result["value"]]
    found = {key: next((p for p in seen if p.startswith("module:")
                        and p.endswith("::" + key)), None) for key in CHANGE}
    return {k: v for k, v in found.items() if v}


def _op(op: str, path: str, effect: str, **extra: Any) -> dict[str, Any]:
    base = {"op": op, "effect": effect, "path": path, "query": None, "value_json": None,
            "args_json": None, "kwargs_json": None, "lifetime": "process",
            "duration_s": None, "reason": "the player misses the goal; the operator asked"}
    return {**base, **extra}


def plan(context: dict[str, Any]) -> dict[str, Any]:
    request = str(context.get("operator_request", ""))
    prior = context.get("prior_tool_results") or []
    base = {"done": True, "lifetime": "process"}
    if re.search(ADVICE_WORDS, request, re.I):
        return {**base, "intent": "converse", "operations": [], "reply": ADVICE}
    if not re.search(ACT_WORDS, request, re.I):
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} I can make the game easier. Say help."}
    failures = [r for step in prior for r in step["results"]
                if not r["ok"] and r.get("op") != "search"]
    if failures:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing changed: {failures[0]['detail']}."}
    paths = _knob_paths(context)
    if len(paths) < len(CHANGE):
        if prior:
            missing = sorted(set(CHANGE) - set(paths))
            return {**base, "intent": "converse", "operations": [],
                    "reply": f"{LABEL} I could not find {missing}."}
        search = _op("search", "", "read", query="BOMB_ODDS MAX_SHOTS Bomb speed")
        return {**base, "done": False, "intent": "inspect", "operations": [search], "reply": ""}
    ops = [_op("set", paths[key], "write", value_json=json.dumps(value))
           for key, value in CHANGE.items()]
    return {**base, "intent": "act", "operations": ops,
            "reply": f"{LABEL} Making the game easier: BOMB_ODDS 600, Bomb.speed 3, MAX_SHOTS 8."}


def chat(content: str) -> str:
    if content.startswith("Answer the operator's request now"):
        start = content.find("{")
        context = json.loads(content[start:]) if start >= 0 else {}
        done = [r for r in context.get("verified_tool_results", [])
                if r.get("ok") and r.get("op") == "set"]
        if done:
            changed = ", ".join(f"{r['path'].split('::')[-1].replace('/', '.')} = {r['value']}"
                                for r in done)
            return (f"{LABEL} Done: {changed}. The game reads these every frame, so the "
                    "change is live, with no restart.")
        return f"{LABEL} Nothing was changed."
    return f"{LABEL} I watch the game's hits per minute against the goal of under 1."


def respond(body: dict[str, Any]) -> dict[str, Any]:
    """The Responses API payload for one request body."""
    fmt = ((body.get("text") or {}).get("format") or {}).get("name")
    content = str(body["input"][-1]["content"])
    if fmt == "samantha_cold_start_analysis":
        return _message(json.dumps(cold_start()))
    if fmt == "samantha_wake":
        start = content.rfind('{"event"')
        return _message(json.dumps(wake(json.loads(content[start:]) if start >= 0 else {})))
    if fmt == "samantha_runtime_plan":
        return _message(json.dumps(plan(json.loads(content))))
    return _message(chat(content))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/v1/models/"):
            self._send(200, {"id": self.path.rsplit("/", 1)[-1], "object": "model"})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/responses":
            self._send(404, {"error": {"message": "not found"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        try:
            self._send(200, respond(body))
        except Exception as exc:
            self._send(500, {"error": {"message": f"scripted model: {exc!r}"}})

    def log_message(self, *args: Any) -> None:
        pass


def serve() -> ThreadingHTTPServer:
    """Start the scripted model on a free local port, on a daemon thread."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, name="scripted-model", daemon=True).start()
    return server


def self_test() -> int:
    def text(name: str, content: str) -> dict[str, Any]:
        reply = respond({"text": {"format": {"name": name}}, "input": [{"content": content}]})
        return json.loads(reply["output"][0]["content"][0]["text"])

    assert text("samantha_cold_start_analysis", "x")["summary"].startswith(LABEL)
    hit = 'x{"event": "log-novelty", "evidence": {"line": "player hit"}}'
    assert text("samantha_wake", hit)["actions"] == []
    gap = 'x{"event": "gap", "evidence": {"series": "goal.met", "value": 0.0, "reference": 1.0}}'
    assert text("samantha_wake", gap)["actions"][0]["type"] == "flag"
    ctx: dict[str, Any] = {"operator_request": "What do you suggest?", "prior_tool_results": [],
                           "live_paths": []}
    assert text("samantha_runtime_plan", json.dumps(ctx))["reply"] == ADVICE
    ctx["operator_request"] = "help"
    assert [o["op"] for o in plan(ctx)["operations"]] == ["search"]
    ctx["prior_tool_results"] = [{"results": [{"op": "search", "ok": True, "value": [
        {"path": f"module:__main__::{k}"} for k in CHANGE]}]}]
    assert {o["path"]: o["value_json"] for o in plan(ctx)["operations"]} == {
        "module:__main__::BOMB_ODDS": "600", "module:__main__::MAX_SHOTS": "8",
        "module:__main__::Bomb/speed": "3"}
    ctx["prior_tool_results"] = [{"results": [{"op": "set", "ok": False, "detail": "bounds"}]}]
    assert "bounds" in plan(ctx)["reply"]
    assert parse_press("0:p") == (0, "p") and parse_press("200:text-p") == (200, "text-p")
    print("rehearse self-test: PASS")
    return 0


# --- the scripted player and the hooks ----------------------------------------

def say(text: str) -> None:
    """Write to the real stdout, past Samantha's output tap."""
    sys.__stdout__.write(f"[rehearse] {text}\n")
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
        self.seen: dict[str, float] | None = None
        self.change_frame: int | None = None
        self.goal_after: list[int] = []  # the game's goal values after the change
        self.stop_at = args.frames or 0
        self.talk = None if args.samantha == "off" or args.no_talk else Talker(self)

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

    def on_tick(self, main_locals: dict) -> None:
        self.frame += 1
        module = sys.modules["__main__"]
        values = knob_values(module)
        if self.seen is None:
            self.seen = values
        if values != self.seen:
            for k, v in values.items():
                if v != self.seen[k]:
                    say(f"frame {self.frame:05d}  {k} changed live: {self.seen[k]:g} → {v:g} "
                        "(no restart)")
            if self.change_frame is None:
                self.change_frame = self.frame
                self.stop_at = self.frame + self.args.after
            self.seen = values
        self.hits = main_locals.get("hits") or []  # the game's list of hit frames
        if self.frame % 200 == 0:
            hits = self.hits
            minutes = min(self.frame, 4800) / 2400
            rate = len([f for f in hits if f > self.frame - 4800]) / minutes
            if self.change_frame is not None:
                self.goal_after.append(int(rate < 1))
            if not self.args.quiet:
                say(f"frame {self.frame:05d}  deaths {main_locals.get('deaths')}  "
                    + "  ".join(f"{k}={v:g}" for k, v in values.items()))
        if self.talk:
            self.talk.maybe_start()
        if self.bot:
            self.bot.step()
        if self.stop_at and self.frame >= self.stop_at:
            self.pg.event.post(self.pg.event.Event(self.pg.QUIT))
            self.stop_at = 0


def samantha_ready() -> bool:
    """True when Samantha has read the game (or is off): the game says "Press P"."""
    module = sys.modules.get("samantha")
    cog = getattr(module.resident(), "cognition", None) if module else None
    return cog is None or cog.provider_ready is False or cog.analysis_ready is not None


class Talker:
    """Type "help" to Samantha through its chat socket, as the samantha CLI does."""

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
            conn.settimeout(120)
            conn.connect(str(path))
            incoming = conn.makefile("rb")
            incoming.readline()  # hello
            message = {"type": "command", "text": self.session.args.say}
            conn.sendall((json.dumps(message) + "\n").encode())
            reply = json.loads(incoming.readline())
        for notice in reply.get("notices") or []:
            say(f"⚑ {notice}")
        text = str(reply.get("reply") or reply.get("error"))
        self.replies.append(text)
        for line in text.splitlines():
            say(f"samantha: {line}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-test", action="store_true", help="check the scripted model and exit")
    p.add_argument("--headless", action="store_true",
                   help="no window and no sound (SDL dummy drivers); runs as fast as possible")
    p.add_argument("--fps", type=int, default=None,
                   help="frame cap (default 40 with a window, no cap with --headless)")
    p.add_argument("--seed", type=int, default=3, help="seed for the game and the player")
    p.add_argument("--samantha", choices=("act", "off"), default="act",
                   help="off: only the game and the scripted player")
    p.add_argument("--say", default="help", help="what to type to Samantha (default: help)")
    p.add_argument("--talk-at", type=int, default=4800, metavar="FRAME",
                   help="type at Samantha's first flag, or at this frame (default 4800, 2 min)")
    p.add_argument("--after", type=int, default=7200, metavar="FRAMES",
                   help="frames to play after the change (default 7200, 3 min)")
    p.add_argument("--frames", type=int, default=0,
                   help="stop after this many frames, if no change stops it first "
                        "(default 2400 with --samantha off, else talk-at + 2 x after)")
    p.add_argument("--no-talk", action="store_true", help="type nothing to Samantha")
    p.add_argument("--no-bot", action="store_true", help="no scripted player (you play)")
    p.add_argument("--press", type=parse_press, action="append", metavar="FRAME:KEY",
                   help=f"post a key press at FRAME (0: the start screen); KEY: {', '.join(KEYS)}."
                        " Default: 0:p. Repeat for more presses.")
    p.add_argument("--quiet", action="store_true", help="no status line every 200 frames")
    p.add_argument("--timeout", type=float, default=900,
                   help="stop the game and fail after this many seconds (default 900)")
    p.add_argument("--out", default=str(HERE / "out"),
                   help="directory for Samantha's state of this run (default out/)")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.frames:  # stop at last, also when no change comes
        args.frames = 2400 if args.samantha == "off" else args.talk_at + 2 * args.after
    if args.fps is None:
        args.fps = 400 if args.headless else 40  # Samantha works in real time; 400 is 10x

    os.environ["SAMANTHA"] = args.samantha
    server = None
    if args.samantha != "off":
        server = serve()
        state = Path(args.out) / f"state-{time.strftime('%Y%m%d-%H%M%S')}"
        os.environ.update(
            OPENAI_API_KEY="none",  # not a key: the scripted model does not check it
            SAMANTHA_MODEL="scripted-model",
            SAMANTHA_BASE_URL=f"http://127.0.0.1:{server.server_address[1]}",
            SAMANTHA_STATE_DIR=str(state), SAMANTHA_GOAL=GOAL, SAMANTHA_AUTONOMY="off")
        say(f"scripted model on {os.environ['SAMANTHA_BASE_URL']}; Samantha state in {state}")
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame as pg

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
        runpy.run_path(str(GAME), run_name="__main__")
    except KeyboardInterrupt:
        pass
    timer.cancel()
    say(f"stopped after {session.frame} frames, {time.monotonic() - started:.0f} s")
    if timed_out.is_set():
        return 1
    if args.samantha == "off" or args.no_talk:
        return 0
    if session.talk and session.talk.thread:
        session.talk.thread.join(timeout=120)

    replies = session.talk.replies if session.talk else []
    checks = {
        "Samantha answered help": bool(replies),
        "the reply has an intervention receipt": any(
            "Applied runtime intervention" in r for r in replies),
        "the game saw a live change (no restart)": session.change_frame is not None,
    }
    if session.change_frame:
        # The hit frames come from the game; the game counts frames only in play.
        # The frame of the change moves the game's random sequence, so the rate
        # after it is different from run to run.
        at, end = session.change_frame, session.frame
        before = sum(f < at for f in session.hits) / (at / 2400)
        after = sum(f >= at for f in session.hits) / max((end - at) / 2400, 1e-9)
        say(f"hits per minute: {before:.1f} before the change, {after:.1f} after; "
            f"goal met after the change: {'yes' if 1 in session.goal_after else 'not yet'}")
        checks["the player is hit at most half as often after the change"] = after <= before / 2
    for name, ok in checks.items():
        say(f"{'PASS' if ok else 'FAIL'}: {name}")
    if server:
        server.shutdown()
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
