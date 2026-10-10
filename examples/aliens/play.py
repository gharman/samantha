"""Run the aliens game with a Samantha panel under the game.

`python demo.py aliens` runs this file. It runs game/aliens.py, unchanged, as
__main__, and adds a strip under the game that shows:

- Samantha's state (starting, reading the game, planning her first move,
  watching, or off),
- what she says by herself (her flags, questions, and proposals),
- each change she makes to the game, with the old and the new value, and
  whether it makes the game easier or harder (harder: back toward the starting
  values, after a "too easy" line; then the panel also says so in a playful line).

The same change lines also go to the terminal ("[demo] Samantha changed ...").
The panel is not part of the game. This file is outside Samantha's project
root (game/), so she does not read it, and she cannot change it.
"""

from __future__ import annotations

import logging
import os
import runpy
import sys
import threading
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME = HERE / "game" / "aliens.py"
PANEL_HEIGHT = 136
# Name on the panel -> (class name or None for a module constant, attribute).
KNOBS = {"BOMB_ODDS": (None, "BOMB_ODDS"), "MAX_SHOTS": (None, "MAX_SHOTS"),
         "ALIEN_ODDS": (None, "ALIEN_ODDS"), "Bomb.speed": ("Bomb", "speed"),
         "Alien.speed": ("Alien", "speed")}
COLORS = {"say": (235, 235, 235), "change": (120, 230, 130), "error": (250, 130, 120),
          "status": (170, 170, 185), "title": (255, 210, 120)}
# The panel's line when Samantha makes the game harder again (in turn).
CRUISING = ("You're cruising. Bringing the aliens back.",
            "Too easy? The aliens want a word.",
            "Nice dodging. Time for a little more challenge.")


def say(text: str) -> None:
    """Write a line to the terminal, past Samantha's output tap: the panel's
    lines are not program output for her to watch."""
    sys.__stdout__.write(f"[demo] {text}\n")
    sys.__stdout__.flush()


def short(value: float) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


# pygame's default font has no glyphs for these; the model often writes them.
PLAIN = str.maketrans({"→": "->", "—": "-", "–": "-", "·": "-", "≥": ">=", "≤": "<=",
                       "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "×": "x",
                       "≈": "~", "⚑": "!"})


def plain(text: str) -> str:
    return " ".join(text.translate(PLAIN).split())


class Feed:
    """The panel's lines. Samantha logs from her own threads, so it has a lock."""

    def __init__(self) -> None:
        self.items: deque[tuple[str, str]] = deque(maxlen=30)
        self.lock = threading.Lock()
        self.version = 0

    def add(self, kind: str, text: str) -> None:
        with self.lock:
            self.items.append((kind, plain(text)))
            self.version += 1

    def snapshot(self) -> list[tuple[str, str]]:
        with self.lock:
            return list(self.items)


class SamanthaLog(logging.Handler):
    """Samantha's own messages (the "[samantha] ..." lines) for the panel."""

    LABELS = {"flag": "Samantha", "question": "Samantha asks", "proposal": "Samantha suggests"}

    def __init__(self, feed: Feed) -> None:
        super().__init__(logging.INFO)
        self.feed = feed

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        kind, _, text = message.partition(": ")
        if kind in self.LABELS and text:
            self.feed.add("say", f"{self.LABELS[kind]}: {text}")
        elif kind == "acted":
            if not text.startswith("Applied runtime intervention"):
                # A turn that changed nothing: say why (for example, every knob
                # is already at its easy end).
                self.feed.add("say", f"Samantha: {text}")
                return
            # The knob watcher shows the knobs with their old values; show
            # only other changed paths from Samantha's receipt here.
            for line in text.splitlines():
                # "✓ set exposed:BOMB_ODDS: assignment verified → 600"
                if line.startswith("✓ ") and "→" in line:
                    path = line[2:].split(": ", 1)[0].partition(" ")[2]
                    name = path.removeprefix("exposed:").split("::")[-1].replace("/", ".")
                    if name not in KNOBS:
                        self.feed.add("change", f"Samantha set {name} -> "
                                      f"{line.rsplit('→', 1)[1].strip()}")
        elif "failed" in message and record.levelno >= logging.WARNING:
            self.feed.add("error", f"Samantha: {message}")


def samantha_status() -> tuple[str, str]:
    """(kind, text) for the panel's title line."""
    module = sys.modules.get("samantha")
    current = module.resident() if module else None
    if current is None:
        return "error", "is off (SAMANTHA=off, or she could not start: see the terminal)."
    cog = getattr(current, "cognition", None)
    if cog is None:
        return "error", "is off: no OpenAI key (see README.md)."
    if cog.provider_ready is False:
        return "error", "is off: the model check failed (see the terminal)."
    if cog.provider_ready is None:
        return "status", "is starting..."
    if cog.analysis_ready is None:
        return "status", "is reading the game (about 20 s)..."
    if cog.preparing:
        return "status", "is planning her first move..."
    autonomous = current.config.mode == "act" and current.config.autonomy == "goal"
    if autonomous and getattr(current.config, "autonomy_slack", None):
        return "status", "is watching: easier when you get hit, harder when you cruise."
    if autonomous and getattr(current.config, "autonomy_trigger", None):
        return "status", "is watching. She steps in when you get hit."
    if autonomous:
        return "status", "is watching. She steps in when you keep getting hit."
    return "status", "is watching. Talk to her: run samantha in a second terminal."


class Panel:
    """Draws the strip under the game on the display surface."""

    def __init__(self, pg, feed: Feed) -> None:
        self.pg, self.feed = pg, feed
        self.display = None
        self.rect = None
        self.drawn: tuple | None = None
        self.fonts: dict[int, object] = {}
        self.knobs: dict[str, float] | None = None
        self.start: dict[str, float] | None = None  # the knobs before any change
        self.harder = 0  # changes that made the game harder
        self.handler: SamanthaLog | None = None

    def font(self, size: int):
        if size not in self.fonts:
            self.fonts[size] = self.pg.font.Font(None, size)
        return self.fonts[size]

    def attach(self, display, rect) -> None:
        self.display, self.rect, self.drawn = display, rect, None
        if self.handler is None and "samantha" in sys.modules:
            # Add the handler only after Samantha armed: with no handler of its
            # own yet, Samantha would not write her lines to the terminal.
            self.handler = SamanthaLog(self.feed)
            logging.getLogger("samantha").addHandler(self.handler)

    def watch_knobs(self) -> None:
        """Compare the game's knobs with the last frame; a difference is Samantha's
        change (nothing else in the game changes them)."""
        game = sys.modules.get("__main__")
        try:
            now = {name: (getattr(game, attr) if owner is None
                          else getattr(getattr(game, owner), attr))
                   for name, (owner, attr) in KNOBS.items()}
        except AttributeError:
            return
        if self.start is None:
            self.start = now
        if self.knobs is not None:
            moved = [(name, self.knobs[name], value) for name, value in now.items()
                     if value != self.knobs[name]]
            if moved:
                # Harder: each knob moved back toward its starting value (the
                # game starts at its hardest). Easier: each moved away from it.
                toward = [abs(new - self.start[name]) < abs(old - self.start[name])
                          for name, old, new in moved]
                way = "harder" if all(toward) else "easier" if not any(toward) else ""
                if way == "harder":
                    line = f"Samantha: {CRUISING[self.harder % len(CRUISING)]}"
                    self.feed.add("say", line)
                    say(line)
                    self.harder += 1
                # pygame's default font has no arrow glyph: "->" on the panel.
                self.feed.add("change", "Samantha changed " + ", ".join(
                    f"{name} {short(old)} -> {short(new)}" for name, old, new in moved)
                    + (f" ({way})" if way else ""))
                for name, old, new in moved:
                    say(f"Samantha changed {name}: {short(old)} → {short(new)} "
                        f"(live, no restart{', ' + way if way else ''})")
        self.knobs = now

    def wrap(self, text: str, width: int, font) -> list[str]:
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if font.size(trial)[0] <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        return [*lines, line] if line else lines

    def refresh(self):
        """Redraw the panel if its content changed; return its rect, or None."""
        if self.display is None:
            return None
        self.watch_knobs()
        status = samantha_status()
        key = (self.feed.version, status)
        if key == self.drawn:
            return None
        self.drawn = key
        pg, rect = self.pg, self.rect
        self.display.fill((22, 22, 30), rect)
        pg.draw.line(self.display, (90, 90, 110), rect.topleft, rect.topright, 2)
        x, y, width = rect.x + 10, rect.y + 8, rect.width - 20
        title = self.font(24).render("Samantha", True, COLORS["title"])
        self.display.blit(title, (x, y))
        state = self.font(20).render(status[1], True, COLORS[status[0]])
        self.display.blit(state, (x + title.get_width() + 8, y + 3))
        body = self.font(20)
        rows: list[tuple[str, str]] = []
        for kind, text in self.feed.snapshot():
            wrapped = self.wrap(text, width, body)
            if len(wrapped) > 2:  # at most two rows per message
                wrapped = [wrapped[0], wrapped[1] + " ..."]
            rows += [(kind, row) for row in wrapped]
        line_height = body.get_linesize()
        room = (rect.bottom - (y + 30) - 4) // line_height
        for i, (kind, row) in enumerate(rows[-room:] if room > 0 else []):
            image = body.render(row, True, COLORS[kind])
            self.display.blit(image, (x, y + 30 + i * line_height))
        return rect


def main() -> None:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame as pg

    feed = Feed()
    panel = Panel(pg, feed)
    real_set_mode, real_update, real_flip = (pg.display.set_mode, pg.display.update,
                                             pg.display.flip)

    def set_mode(size=(0, 0), flags=0, depth=0, *args, **kwargs):
        # pygame-ce ignores depth (and warns about it), so it is not passed on.
        width, height = size
        display = real_set_mode((width, height + PANEL_HEIGHT), flags)
        panel.attach(display, pg.Rect(0, height, width, PANEL_HEIGHT))
        return display.subsurface(pg.Rect(0, 0, width, height))  # the game's screen

    def update(*args, **kwargs):
        if (rect := panel.refresh()) is not None:
            real_update(rect)
        return real_update(*args, **kwargs)

    def flip():
        panel.refresh()
        return real_flip()

    pg.display.set_mode, pg.display.update, pg.display.flip = set_mode, update, flip
    say("aliens: press P to play. Arrows move, Space fires, P pauses, Esc quits.")
    say("Samantha's messages show under the game and here, in the terminal.")
    try:
        runpy.run_path(str(GAME), run_name="__main__")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
