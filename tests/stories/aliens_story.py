"""The aliens demo as an unattended story: a scripted player that is not good
at the game, and (by default) a scripted model on 127.0.0.1. Only the tests and
the maintainers use it; the demo itself is `python demo.py aliens`.

All else is the real thing: examples/aliens/play.py (the panel) runs
game/aliens.py with demo.py's settings, and the real Samantha reads the game,
plans her first move, and applies it at the first hit (the first "too hard"
line, SAMANTHA_AUTONOMY_TRIGGER). With SAMANTHA_AUTONOMY=goal she changes the
game's knobs by herself in the running process: easier on later "too hard"
lines, and one notch harder on "too easy" lines (SAMANTHA_AUTONOMY_SLACK), never
harder than the starting values. The summary has the time from the first hit
to the first change, each change (easier or harder), and the hits per minute
every 10 s of play.

--dodge: after Samantha's first change, the player is never hit (the story
ignores the player's collisions; a stand-in for a player who dodges everything).
Then the game prints "too easy" every 30 s of play, and she gives back her
changes one knob at a time. The scripted model's give-back plan also asks for
Alien.speed 15 (harder than the start of 13); Samantha must refuse that.

  python aliens_story.py --headless            # about 15 s, scripted model
  python aliens_story.py --headless --dodge    # about 60 s, scripted model
  python aliens_story.py --headless --live     # the real model: OPENAI_API_KEY and
                                               # SAMANTHA_BASE_URL from the environment
  python aliens_story.py --self-test           # check the scripted model only

Before the game starts, this script replaces pygame functions from the
outside: pg.key.get_pressed (the scripted player), pg.time.Clock (one hook per
frame), pg.event.get (it posts real KEYDOWN events, for example P to start),
and pg.sprite.spritecollide (only for --dodge). Lines that start with "[story]"
go past Samantha's output tap.
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
GOAL = ("keep the game fun: the player is hit 0.5 to 2 times per minute of play, not more "
        "and not zero")

# --- the scripted model ------------------------------------------------------

# The knobs at the start (the hardest values) and at their easy ends.
START = {"BOMB_ODDS": 60, "MAX_SHOTS": 2, "ALIEN_ODDS": 22, "Bomb.speed": 9, "Alien.speed": 13}
EASY = {"BOMB_ODDS": 600, "MAX_SHOTS": 8, "ALIEN_ODDS": 100, "Bomb.speed": 3, "Alien.speed": 4}
# One notch toward the easy end (about a quarter of each range). The scripted
# model makes the game easier by one notch on the first two knobs in this order
# that are not at their easy end, and harder by one notch on the first knob that
# is not at its start.
NOTCH = {"BOMB_ODDS": 135, "Bomb.speed": -2, "ALIEN_ODDS": 20, "Alien.speed": -2, "MAX_SHOTS": 2}
FIRST_MOVE = {"BOMB_ODDS": 195, "Bomb.speed": 7}
# Harder than the start (13) and outside the bounds: the scripted give-back plan
# asks for it too, and Samantha must refuse it.
BEYOND_START = {"Alien.speed": 15}
PREPARE_WORDS = r"plan your first move now"
MISS_WORDS = r"miss pattern"
SLACK_WORDS = r"slack pattern"
ADVICE = (f"{LABEL} The game prints about 6 hits per minute, above the fun band of 0.5 to "
          "2. Most hits are bombs. I suggest one notch on two knobs: BOMB_ODDS 60 to 195 "
          "and Bomb.speed 9 to 7. Say help and I will change them live.")
ADVICE_WORDS = r"\b(suggest|recommend|advise|what should|what would)\b"
ACT_WORDS = r"\b(help|yes|do it|go ahead|ok|apply|fix it|make it easier)\b"


def cold_start() -> dict[str, Any]:
    return {
        "summary": f"{LABEL} aliens.py is pygame's alien shooter. A hit respawns the "
                   "player and prints a 'player hit' line. The first hit and each hit "
                   "above the fun band print 'too hard'; 30 s of play with no hit prints "
                   "'too easy'. Every 2 s it prints the hits per minute.",
        "architecture": ["main() runs a 40 fps loop and reads the module constants each frame",
                         "Bomb.speed moves bombs every frame; BOMB_ODDS sets how often they drop"],
        "goals": [GOAL],
        "watching": ["too hard and too easy lines", "hits per minute"],
        "knob_candidates": ["BOMB_ODDS (60-600)", "MAX_SHOTS (2-8)", "ALIEN_ODDS (22-100)",
                            "Bomb.speed (3-9)", "Alien.speed (4-13)"],
        "protected": ["SCORE", "SCREENRECT", "FUN_BAND", "CRUISE_SECONDS"],
        "ambiguities": [],
    }


def wake(event: dict[str, Any]) -> dict[str, Any]:
    """The game prints no metric with a target, so a wake has nothing to report."""
    return {"assessment": f"{LABEL} nothing to report", "actions": []}


def declared_values(context: dict[str, Any]) -> dict[str, float]:
    """The declared knobs' current values (from their previews)."""
    values = {}
    for knob in context.get("declared_knobs") or []:
        name = str(knob.get("path", "")).removeprefix("exposed:")
        try:
            values[name] = float(knob.get("preview"))
        except (TypeError, ValueError):
            continue
    return values


def easier(values: dict[str, float]) -> dict[str, int]:
    """One notch toward the easy end on the first two knobs that are not there."""
    change = {}
    for name, step in NOTCH.items():
        if name in values and values[name] != EASY[name] and len(change) < 2:
            low, high = sorted((START[name], EASY[name]))
            change[name] = int(min(high, max(low, values[name] + step)))
    return change


def harder(values: dict[str, float]) -> dict[str, int]:
    """One notch back toward the start on the first knob that is not there."""
    for name, step in NOTCH.items():
        if name in values and values[name] != START[name]:
            low, high = sorted((START[name], EASY[name]))
            return {name: int(min(high, max(low, values[name] - step)))}
    return {}


def _op(op: str, path: str, effect: str, **extra: Any) -> dict[str, Any]:
    base = {"op": op, "effect": effect, "path": path, "query": None, "value_json": None,
            "args_json": None, "kwargs_json": None, "lifetime": "process",
            "duration_s": None, "reason": "keep the game in the fun band"}
    return {**base, **extra}


def plan(context: dict[str, Any]) -> dict[str, Any]:
    request = str(context.get("operator_request", ""))
    prior = context.get("prior_tool_results") or []
    base = {"done": True, "lifetime": "process"}
    preparing = bool(re.search(PREPARE_WORDS, request, re.I))
    slack = bool(re.search(SLACK_WORDS, request, re.I))
    if re.search(ADVICE_WORDS, request, re.I):
        return {**base, "intent": "converse", "operations": [], "reply": ADVICE}
    if not (preparing or slack or re.search(ACT_WORDS + "|" + MISS_WORDS, request, re.I)):
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} I can make the game easier. Say help."}
    failures = [r for step in prior for r in step["results"] if not r["ok"]]
    if failures:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing changed: {failures[0]['detail']}."}
    values = declared_values(context)
    change = harder(values) if slack else easier(values)
    if not change:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} Nothing left to change: every knob I use is at its "
                         + ("start." if slack else "easy end.")}
    declared = {str(k.get("path", "")) for k in context.get("declared_knobs") or []}
    missing = sorted(key for key in change if f"exposed:{key}" not in declared)
    if missing:
        return {**base, "intent": "converse", "operations": [],
                "reply": f"{LABEL} I could not find {missing}."}
    ops = [_op("set", f"exposed:{key}", "write", value_json=json.dumps(value))
           for key, value in {**change, **(BEYOND_START if slack else {})}.items()]
    said = ", ".join(f"{key} {value}" for key, value in change.items())
    return {**base, "intent": "act", "operations": ops,
            "reply": f"{LABEL} Making the game {'harder' if slack else 'easier'}: {said}."}


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

    def ops(context: dict[str, Any]) -> dict[str, str]:
        return {o["path"]: o["value_json"] for o in plan(context)["operations"]}

    def set_values(values: dict[str, float]) -> None:
        for item in declared:
            item["preview"] = repr(values[item["path"].removeprefix("exposed:")])

    assert text("samantha_cold_start_analysis", "x")["summary"].startswith(LABEL)
    hit = 'x{"event": "log-novelty", "evidence": {"line": "player hit"}}'
    assert text("samantha_wake", hit)["actions"] == []
    declared = [{"path": f"exposed:{k}", "preview": repr(v)} for k, v in START.items()]
    ctx: dict[str, Any] = {"operator_request": "What do you suggest?", "prior_tool_results": [],
                           "declared_knobs": declared}
    assert text("samantha_runtime_plan", json.dumps(ctx))["reply"] == ADVICE
    ctx["operator_request"] = ("Standing approval (SAMANTHA_AUTONOMY=goal): plan your first "
                               "move now. Nothing changes now.")
    assert ops(ctx) == {f"exposed:{k}": str(v) for k, v in FIRST_MOVE.items()}
    set_values({**START, **FIRST_MOVE})  # the first move is in place: the next notch
    ctx["operator_request"] = "Standing approval: ... the operator's miss pattern ..."
    assert ops(ctx) == {"exposed:BOMB_ODDS": "330", "exposed:Bomb.speed": "5"}
    # Slack: one knob one notch back toward its start, and a request past the start.
    ctx["operator_request"] = "Standing approval: ... the operator's slack pattern ..."
    assert ops(ctx) == {"exposed:BOMB_ODDS": "60", "exposed:Alien.speed": "15"}
    set_values({**START, "Bomb.speed": 7})
    assert ops(ctx) == {"exposed:Bomb.speed": "9", "exposed:Alien.speed": "15"}
    set_values(START)
    assert plan(ctx)["operations"] == [] and "at its start" in plan(ctx)["reply"]
    set_values(EASY)
    ctx["operator_request"] = "help"
    assert plan(ctx)["operations"] == [] and "easy end" in plan(ctx)["reply"]
    set_values(START)
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
        self.start: dict[str, float] | None = None  # the knobs before any change
        self.changes: list[dict[str, Any]] = []
        self.play_started: float | None = None
        self.flag_frame: int | None = None
        # "too easy" lines: (frame, time.monotonic()); the game's "calm" frame moves
        # without a hit.
        self.too_easy: list[tuple[int, float]] = []
        self.calm = 0
        self.trace: list[tuple[float, int]] = []  # (play s, hits in the last minute)
        self.beyond_start: list[str] = []  # knob values harder than the start
        self.dodging = False  # --dodge, after the first change: no collisions
        self.cruise: float | None = None  # the game's CRUISE_SECONDS
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

    def on_change(self, values: dict[str, float], now: float) -> None:
        """Record a change of the knobs: what moved, which way, and when."""
        assert self.seen is not None and self.start is not None
        moved = {k: (self.seen[k], v) for k, v in values.items() if v != self.seen[k]}
        toward = [abs(new - self.start[k]) < abs(old - self.start[k])
                  for k, (old, new) in moved.items()]
        way = "harder" if all(toward) else "easier" if not any(toward) else "mixed"
        last_hit = self.hits[-1] if self.hits else 0
        change = {"frame": self.frame, "play_s": round(self.frame / 40, 1),
                  "wall_s": round(now - (self.play_started or now), 1), "moved": moved,
                  "way": way, "play_s_since_last_hit": round((self.frame - last_hit) / 40, 1)}
        if self.hit_times:  # wall seconds since the first hit and since the last hit
            change["after_first_hit_s"] = round(now - self.hit_times[0], 2)
            change["after_last_hit_s"] = round(now - self.hit_times[-1], 2)
        if self.too_easy and self.too_easy[-1][0] > last_hit:
            # wall seconds since the last "too easy" line (none since the last hit: absent)
            change["after_too_easy_s"] = round(now - self.too_easy[-1][1], 2)
        self.changes.append(change)
        for k, (old, new) in moved.items():
            say(f"frame {self.frame:05d}  {k} changed live: {old:g} → {new:g} ({way}"
                + (f", {change['after_first_hit_s']:.2f} s after the first hit, "
                   f"{change['after_last_hit_s']:.2f} s after the last hit"
                   if self.hit_times else ", no hit yet")
                + (f", {change['after_too_easy_s']:.2f} s after a too easy line"
                   if "after_too_easy_s" in change else "") + ")")
        self.stop_at = self.frame + self.args.after  # play on after each change
        self.shots_due.append((self.frame + self.args.shot_delay,
                               f"after-change-{len(self.changes)}"))
        self.seen = values
        if self.args.dodge and not self.dodging:
            self.dodging = True
            say(f"frame {self.frame:05d}  the player dodges everything from now on")

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
        calm = main_locals.get("calm", 0)
        if calm != self.calm and not (self.hits and self.hits[-1] == calm):
            self.too_easy.append((self.frame, now))  # the game printed "too easy"
            say(f"frame {self.frame:05d}  too easy line")
        self.calm = calm
        module = sys.modules["__main__"]
        values = knob_values(module)
        self.cruise = getattr(module, "CRUISE_SECONDS", None)
        if self.seen is None:
            self.seen = self.start = values
        self.beyond_start += [f"frame {self.frame}: {k}={v:g}" for k, v in values.items()
                              if not min(START[k], EASY[k]) <= v <= max(START[k], EASY[k])]
        if values != self.seen:
            self.on_change(values, now)
        if self.frame % 400 == 0:  # every 10 s of play
            recent = len([f for f in self.hits if f > self.frame - 2400])
            self.trace.append((self.frame / 40, recent))
            if not self.args.quiet:
                say(f"frame {self.frame:05d}  deaths {main_locals.get('deaths')}  "
                    f"hits in the last minute {recent}  "
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
                   help="frame cap (default 40; with --headless and the scripted model 400, "
                        "or 100 with --dodge: then 30 s of play take 12 s, more than the "
                        "10 s between two of her turns)")
    p.add_argument("--dodge", action="store_true",
                   help="after Samantha's first change the player is never hit (the story "
                        "ignores the player's collisions)")
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
        args.fps = 40 if args.live or not args.headless else 100 if args.dodge else 400

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
    real_spritecollide = pg.sprite.spritecollide

    def spritecollide(sprite, group, dokill, collided=None):
        if session.dodging and type(sprite).__name__ == "Player":
            return []  # --dodge: the player dodges everything
        return real_spritecollide(sprite, group, dokill, collided)

    if args.dodge:
        pg.sprite.spritecollide = spritecollide

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
    replies_out = [str(r.get("text")) for r in rows
                   if r.get("type") == "chat" and r.get("direction") == "out"]
    changes = session.changes
    harder = [c for c in changes if c["way"] == "harder"]
    easier = [c for c in changes if c["way"] == "easier"]
    cruise = float(session.cruise or 30)
    # "too easy" lines after the last change (or all, with no change): no turn.
    idle = [f for f, _ in session.too_easy if not changes or f > changes[-1]["frame"]]
    playful = [line for line in tee.lines if line.startswith("[demo] Samantha: ")]
    checks = {"the game saw a live change (no restart)": bool(changes),
              "the panel and the terminal showed the change": bool(panel),
              "no knob was ever harder than at the start": not session.beyond_start}
    if args.wait:
        checks["Samantha answered"] = bool(replies)
        checks["the reply has an intervention receipt"] = any(
            "Applied runtime intervention" in r for r in replies)
    else:
        checks["Samantha changed the game by herself (no chat)"] = bool(auto) and not replies
        first = changes[0] if changes else {}
        checks["no change before the first hit"] = all(
            "after_first_hit_s" in change for change in changes)
        latency = first.get("after_first_hit_s")
        checks[f"the first change came at most {args.max_latency:g} s after the first hit"] = (
            latency is not None and latency <= args.max_latency)
        checks["the first change made the game easier"] = first.get("way") == "easier"
        checks["each change moved one or two knobs"] = all(
            1 <= len(c["moved"]) <= 2 for c in changes)
        checks["each harder change moved one knob, after a too easy line"] = all(
            len(c["moved"]) == 1 and "after_too_easy_s" in c for c in harder)
    if args.dodge:
        checks[f"after {cruise:g} s of play with no hit, the game got a notch harder"] = (
            bool(harder) and harder[0]["play_s_since_last_hit"] >= cruise)
        checks["the panel said so in a playful line"] = bool(harder) and len(playful) == len(
            harder)
        if not args.live:
            checks["back at the start, a too easy line changed nothing"] = (
                session.seen == session.start and bool(idle))
            checks["the give-back past the start (Alien.speed 15) was refused"] = any(
                "Alien.speed: 15 is outside (4, 13)" in text for text in replies_out)
    first_hit = session.hits[0] if session.hits else None
    summary: dict[str, Any] = {
        "model": "live" if args.live else "scripted", "fps": args.fps, "seed": args.seed,
        "dodge": args.dodge, "cruise_s": cruise,
        "frames": session.frame, "first_hit_frame": first_hit, "hits": len(session.hits),
        "first_change_after_first_hit_s": (changes[0].get("after_first_hit_s")
                                           if changes else None),
        "easier_steps": len(easier), "harder_steps": len(harder),
        "flag_frame": session.flag_frame, "changes": changes,
        "too_easy_frames": [f for f, _ in session.too_easy],
        "too_easy_frames_with_no_turn_after_the_last_change": idle,
        # (play seconds, hits in the last minute of play), every 10 s of play
        "hits_per_minute_trace": session.trace,
        "knobs_at_start_at_end": session.seen == session.start,
        "beyond_start": session.beyond_start[:10],
        "autonomous_turns": len(auto), "panel_lines": panel, "playful_lines": playful,
        "findings": [f"{r.get('type')}: {r.get('summary') or r.get('body') or ''}"
                     for r in rows if r.get("type") in ("flag", "question", "proposal")],
        "replies_out": replies_out,
    }
    if changes:
        # The hit frames come from the game; it counts frames only in play.
        # Before her first change, and after her last change.
        at, last, end = changes[0]["frame"], changes[-1]["frame"], session.frame
        before = sum(f < at for f in session.hits) / (at / 2400)
        after = sum(f >= last for f in session.hits) / max((end - last) / 2400, 1e-9)
        summary.update(hits_per_min_before=round(before, 2), hits_per_min_after=round(after, 2))
        latency = changes[0].get("after_first_hit_s")
        say(f"first change at frame {at} ({at / 40:.0f} s of play"
            + (f", {latency:.2f} s after the first hit" if latency is not None else "")
            + f"); {len(changes)} change(s): {len(easier)} easier, {len(harder)} harder; "
            f"hits per minute: {before:.1f} before the first, {after:.1f} after the last")
        if not args.live and not args.dodge:
            checks["the player is hit at most half as often after her changes"] = (
                after <= before / 2)
    say("hits in the last minute, every 10 s of play: "
        + " ".join(str(recent) for _, recent in session.trace))
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
