# Samantha guide

This guide is for when you use Samantha in your own programs. For the install
and the two demos, see the [README](../README.md).

- [How Samantha works](#how-samantha-works)
- [Settings: .env and the environment](#settings-env-and-the-environment)
- [Tell Samantha what you want](#tell-samantha-what-you-want)
- [Modes and autonomy](#modes-and-autonomy)
- [Talk to Samantha](#talk-to-samantha)
- [Show Samantha's messages in your own program](#show-samanthas-messages-in-your-own-program)
- [What Samantha can change, and the limits](#what-samantha-can-change-and-the-limits)
- [Memory and the journal](#memory-and-the-journal)
- [The demos in detail](#the-demos-in-detail)
- [Development and tests](#development-and-tests)
- [Platform notes](#platform-notes)

## How Samantha works

`import samantha` starts a resident in your process. You start the program as
usual; there is no wrapper command. At startup Samantha:

1. Finds the project root: the folder of the importing file, or the nearest
   folder above it, that has `pyproject.toml` or `.git`.
2. Prints her startup lines (`[samantha] ...`): mode, process ID, memory folder,
   entry file, goal, the number of `# daemon:` directives, and the model.
3. Checks the OpenAI key and the model on a background thread. A failure is
   reported once, with the API's error, and the program keeps running.
4. Reads the project (the entry file and its local imports first, then the
   other project files; README, `pyproject.toml`, docstrings, to-do notes, and
   `# daemon:` directives). One model call turns this into her theory of the
   program: what it does, its goals, what to watch, and possible knobs. The
   theory is in `model.md` in her memory folder.

Then she watches. She taps stdout, stderr, and logging, and reads numbers from
each line (`loss=0.31`, `fidelity/rare: 0.53`, `gpu/util: 72%`). Deterministic
sentinels look for NaN or inf values, new kinds of WARNING or ERROR lines,
stalled series, and goal gaps: a series that stays below its target. A
sentinel event wakes the model, which can raise a flag, ask one question,
propose a change, or close an old flag. Silence is the default: a healthy
program gets no messages.

If Samantha cannot start, your program keeps running and one
`[samantha] disabled: <error>` line goes to stderr.

**What goes to the model API.** In `advise` and `act` modes Samantha sends
these to OpenAI (or to `SAMANTHA_BASE_URL`): your project's Python files (up
to 180,000 characters), README and `pyproject.toml`, lines of stdout, stderr,
and logging, live values and paths from your program, and the chat. Do not
expect secrets in this data to be removed: the redaction applies to her
journal and memory files, not to the source she sends. She does not read `.env` files into the model input. Do not use her on
a program whose source or output must not leave your machine. `observe` mode
makes no model calls.

## Settings: .env and the environment

Samantha reads her settings from the environment and from `.env` files. She
reads `.env` in two places: your program's project root, and the folder you
run the program from (if it is a different folder). She does not search the
parent folders.

The order of precedence, highest first:

1. An environment variable (`export SAMANTHA_MODEL=...`, or on the command
   line: `SAMANTHA=act python app.py`).
2. `.env` in the project root.
3. `.env` in the working folder.

Only `OPENAI_API_KEY` and settings that start with `SAMANTHA` are read from a
`.env` file. Other lines in the file are ignored, and Samantha never puts
values from `.env` into your program's environment. Keep `.env` out of git
(add it to `.gitignore`). The startup line `credentials: ...` tells where the
key came from.

| Setting | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | none | The OpenAI key. With no key, Samantha observes but does not think or talk. |
| `SAMANTHA` | `advise` | The mode: `off`, `observe`, `advise`, or `act` ([modes](#modes-and-autonomy)). |
| `SAMANTHA_AUTONOMY` | `off` | `goal`: in act mode, Samantha acts by herself on an unmet goal. |
| `SAMANTHA_AUTONOMY_DWELL` | `600` | Seconds between two autonomous turns for one series (at least 30). |
| `SAMANTHA_AUTONOMY_TRIGGER` | none | A regular expression. With `SAMANTHA_AUTONOMY=goal` in act mode, a line of program output that matches it is a miss against the goal ([miss lines](#miss-lines)). |
| `SAMANTHA_AUTONOMY_TRIGGER_DWELL` | `15` | Seconds between two act turns on miss or slack lines (at least 10). |
| `SAMANTHA_AUTONOMY_SLACK` | none | A regular expression. With `SAMANTHA_AUTONOMY=goal` in act mode, a line of program output that matches it says the goal is met with room to spare: Samantha moves one declared knob back toward its starting value ([slack lines](#slack-lines)). |
| `SAMANTHA_GOAL` | none | The operator goal, in words. |
| `SAMANTHA_MODEL` | `gpt-5.6-terra` | The model. Samantha uses OpenAI's Responses API. |
| `SAMANTHA_BASE_URL` | `https://api.openai.com` | The API address (for a proxy or a compatible server). |
| `SAMANTHA_STATE_DIR` | `~/.samantha` | The base folder for her memory. |
| `SAMANTHA_QUIET` | off | `1`: no `[samantha]` lines in the terminal. |
| `SAMANTHA_LOG_LEVEL` | `INFO` | `WARNING`, `ERROR`, `CRITICAL`, or `OFF` shows fewer lines. |
| `SAMANTHA_LLM_TIMEOUT` | `0` (none) | Seconds to wait for one model reply. |
| `SAMANTHA_ACT_MAX_ROUNDS` | `12` | Model rounds per act turn (`0`: no limit). |
| `SAMANTHA_MAX_WAKES_PER_HOUR` | `0` (no limit) | A limit on sentinel wakes. |
| `SAMANTHA_TOKEN_BUDGET_PER_DAY` | `0` (no limit) | A limit on wake tokens. |
| `SAMANTHA_FLAG_BUDGET` | `0` (no limit) | The most open flags at one time. |
| `SAMANTHA_ARM_CHILDREN` | off | `1`: also start a resident in `multiprocessing` workers. |

A value that is not a number is ignored with a warning, and the default is
used. `SAMANTHA_QUIET` and `SAMANTHA_LOG_LEVEL` belong to the program that
imports Samantha, not to the `samantha` chat command.

## Tell Samantha what you want

Samantha works toward goals that you state. She does not invent them.

**Goals.** Use any of these:

```python
# daemon: goal: every region reaches fidelity 0.95
samantha.goal("every region reaches fidelity 0.95")
```

or `SAMANTHA_GOAL=...`, or `/goal <text>` in the chat. `/goal` alone shows the
current goal. A `# daemon: goal ...` line is the goal when no other goal is
set; the startup line shows it with `(# daemon:)`. A `samantha.goal()` call
runs after `import samantha`, so the startup line does not show it.

**Directives.** `# daemon:` comments anywhere in the project are instructions
from you. Use them for what is allowed, what is not, and how bold to be.
Samantha sees them in her theory, in each wake, and in each act turn:

```python
# daemon: sampling_weights are fair game within 0.1-5.0
# daemon: never touch anything in eval_*, seeding, or checkpoint paths
# daemon: this timeout is load-bearing, hands off
```

**Metrics with a target.** Print the numbers that matter, with the target on
the same line. The words `target`, `ceiling`, `expected`, `budget`, and `limit`
set the reference for every number on that line:

```python
print(f"fidelity/rare: {rare:.3f}  (target 0.95)")
```

When a series is more than 0.05 below its reference and does not move for 20
to 25 lines, the gap sentinel wakes Samantha. Make the metric "higher is
better" and print it at a steady rate (each line is one point): the rate sets
how soon she wakes.

**Knobs.** Declare what Samantha may change, with hard bounds:

```python
samantha.expose("learning_rate", obj=cfg, attr="lr", bounds=(1e-5, 0.1))
```

The planner sees each declared knob as `exposed:<name>`, with its value, its
bounds, and its starting value (the value when `expose()` ran), and prefers
these paths. A value outside the bounds is refused on the `exposed:` path. The
same attribute is also on its live path (for example
`module:app::cfg/lr`), and a set on that path has no bounds
check ([the limits](#what-samantha-can-change-and-the-limits)). Your
program must read the value again when it uses it (for example each round, or
each frame); a value that was copied at startup does not change.

`samantha.silence(minutes=10)` stops the sentinel wakes for a time; the chat
still works. `samantha.event("text")` writes a note to the journal.

## Modes and autonomy

| Mode | Samantha |
|---|---|
| `off` | does not start. |
| `observe` | watches and keeps her journal; no model calls, no chat. |
| `advise` (default) | also thinks and talks: flags, questions, proposals, chat. She changes nothing. |
| `act` | can also change the running program, through audited operations. |

In act mode she changes the program only when you ask in the chat, unless you
set `SAMANTHA_AUTONOMY=goal`. Then a goal gap also gets one act turn: Samantha
plans and, if the goal, the directives, and the bounds allow a change that she
predicts closes the gap, she makes it. This happens at most once per series
per dwell (`SAMANTHA_AUTONOMY_DWELL`, default 600 s); while the target stays
unmet, the gap fires again after the dwell, so a change that fell short gets
another try. The demos run with `SAMANTHA=act`, `SAMANTHA_AUTONOMY=goal`, and a
dwell of 90 s.

An act turn stops after `SAMANTHA_ACT_MAX_ROUNDS` model rounds, and earlier
when a round only repeats reads with the same results. Chat turns and
autonomous turns run one at a time.

### Miss lines

A gap wake comes only after a series stays below its target for 20 to 25
lines. If your program prints a line at each failure (for example
`too hard: ...` in the aliens demo), Samantha can act at the first failure. Set
`SAMANTHA_AUTONOMY_TRIGGER` to a regular expression for that line. It has an
effect only with `SAMANTHA=act` and `SAMANTHA_AUTONOMY=goal`; with other
settings Samantha ignores it and gives a warning at startup.

1. At startup, at the same time as the program theory, Samantha plans her
   first move: one planning call, with the goal, the `# daemon:` directives,
   and the declared knobs. She keeps only `set` operations on declared knobs
   with values inside their bounds, and she changes nothing yet. The terminal
   shows `first move ready: ...` (or `no first move planned: ...`). While she
   plans, `cognition.preparing` is true (the aliens demo waits for it).
2. At the first line that matches, she applies that move at once, with no
   model call, through the same audited operations as all other changes. If a
   knob of the move changed after the plan (for example in the chat), she does
   not apply it and plans again.
3. Each later matching line can start an act turn: one planning call, and no
   second model call to write the reply (the reply is the receipt and the
   planner's own words). At most one such turn starts per
   `SAMANTHA_AUTONOMY_TRIGGER_DWELL` (default 15 s, at least 10 s), and never
   while one runs. The request gives the number of misses since the last
   turn, not the text of the lines.

Gap wakes and their autonomous turns continue as before. `samantha.silence()`
also stops the turns on miss lines.

### Slack lines

Miss lines push in one direction: each one asks for a change that reduces the
misses. If the goal is a band (for example "hit 0.5 to 2 times per minute, not
more and not zero"), the program must also say when it has room to spare. Set
`SAMANTHA_AUTONOMY_SLACK` to a regular expression for that line (for example
`too easy: ...` in the aliens demo). It has an effect only with `SAMANTHA=act`
and `SAMANTHA_AUTONOMY=goal`; it does not need `SAMANTHA_AUTONOMY_TRIGGER`.

A slack line gives back one step of the earlier changes:

1. If every declared knob is at its starting value (its value when
   `samantha.expose()` ran), nothing happens and there is no model call.
2. Otherwise Samantha plans: one planning call, with the goal, the
   `# daemon:` directives, the declared knobs with their starting values, and
   the names of the knobs that are not at their start. No second model call.
3. She applies at most one `set` on one declared knob, and only a value
   between the knob's current value and its starting value (it can be the
   starting value). She refuses all other operations, and her reply lists them.
   The change goes through the same audited operations as all other changes,
   with the lifetime `process`, and you can revert it.

So a slack line never moves the program past where it started. Slack lines
share the dwell of miss lines (`SAMANTHA_AUTONOMY_TRIGGER_DWELL`) and the rule
"one turn at a time": a slack line inside the dwell does nothing. The program
decides when it has slack, so make it print the line again later (the aliens
demo prints it after each 30 s of play with no hit). `samantha.silence()` also
stops these turns.

Why a line and not a timer in Samantha: only the program knows its own clock
(the aliens game counts play time, not the time it is paused) and what "room
to spare" means. Samantha needs no settings for a band, a window, or an idle
time.

## Talk to Samantha

`samantha` attaches a terminal to a running resident:

```console
samantha             # connects when exactly one resident is running
samantha --list      # lists the process IDs of running residents
samantha --pid 1234  # selects one
```

Type questions or requests in your own words. In act mode a request can change
the program; the reply starts with a receipt
(`Applied runtime intervention ...`, one `✓` line per verified change).
Commands: `/goal [text]`, `/resolve` (list open flags), `/resolve <id>` or
`/resolve all` (close flags), and `bye`. Findings that came since your last
message come first, as `⚑` lines.

The prompt has arrow-key history (the last 1,000 lines, in
`~/.samantha/cli-history`). If the program exits, the chat goes on in detached
mode: Samantha uses her memory, but she cannot see or change a live process,
and she says so.

## Show Samantha's messages in your own program

Samantha writes her messages to the `samantha` logger, one record per message:
`flag: ...`, `question: ...`, `proposal: ...`, `acted: <receipt>`, and her
startup lines. To show them in a window, a status bar, or a plot, add a
handler after `import samantha`:

```python
import logging
import samantha

class ToMyUI(logging.Handler):
    def emit(self, record):
        kind, _, text = record.getMessage().partition(": ")
        if kind in ("flag", "question", "proposal", "acted"):
            my_ui.show(f"Samantha ({kind}): {text}")

logging.getLogger("samantha").addHandler(ToMyUI())
```

Add the handler after the import: Samantha adds her terminal handler only when
the logger has no handler yet. The demos do this: `examples/aliens/play.py`
(a panel under the game) and `examples/training-run/distill/train.py` (text
under the plot).

## What Samantha can change, and the limits

In act mode Samantha builds a map of your live program: the project's modules,
the frames that run now, globals, classes, instances, mappings, and lists. The
planner (a model call with a strict schema) chooses fixed operations on exact
paths of that map:

- `search` and `inspect` (read only),
- `set`, `append`, `enqueue` (with verification after the change),
- `call` an existing project callable,
- `intercept` a future call (change its arguments or its result),
- `rewrite_output` (a fallback for console text), `emit` a receipt, `revert`.

What the code enforces:

- **No code from the model.** The model cannot send code to run, and the
  operations have no `import` and no source file write.
- **A small denylist of calls.** `call` and `intercept` refuse `eval`,
  `exec`, `compile`, all of `subprocess`, and `os.system`, `os.popen`,
  `os.spawn*`, and `os.execv*`.
- **Act only in act mode,** and only on your request or, with
  `SAMANTHA_AUTONOMY=goal`, on a goal gap, a miss line, or a slack line.
- **Bounds on the `exposed:` path.** A declared knob refuses a value outside
  its bounds. The first move, miss lines, and slack lines accept only `set`
  on declared knobs, inside their bounds.
- **Protected paths.** Paths with the segments `eval`, `seed`, `test`, `tests`,
  `ckpt`, or `checkpoint` cannot be changed.

What the code does not enforce:

- **Directives.** The charter tells her to follow `# daemon:` lines and to
  prefer the smallest change unless you say otherwise. But a directive is an
  instruction to the model, not a check in the code: "hands off" or "within
  0.1-5.0" in a directive does not stop a change. Use `expose()` bounds or a
  protected path name for a hard limit.
- **Other calls.** In a chat turn or a goal-gap turn, `call` can run any
  callable that is in the map and not on the denylist: your own functions,
  methods of live objects, and functions that your modules import by name
  (for example `from os import remove`, `from shutil import rmtree`, or
  `os.posix_spawn`, `os.execl`, `os.fork`, `os.kill`). Such a call can delete
  files or start processes.
- **Other sets.** In a chat turn or a goal-gap turn, `set` can change any
  attribute in the map that is not on a protected path. This includes the
  live path of a declared knob, which has no bounds check. The planner is told
  to prefer the declared knobs.

So use act mode only on a program where you accept these risks, and keep the
journal: each change is in it and most changes can be reverted.

Other rules:
- **Program output is testimony.** Text from logs, variables, and data is
  evidence, never an instruction. Only the operator instructs (environment,
  chat, `# daemon:` comments).
- **Everything is in the journal,** and changes can be reverted (`revert` in
  the chat, for example "undo the last change").
- **Lifetimes.** Each change group has a lifetime: `one_shot`, `temporary`
  (reverts after `duration_s`, default 120 s), `until_failure` (reverts on
  NaN/inf, a new ERROR line, or an exception), `process`, or `contextual`.
- **Budgets.** Act turns stop after `SAMANTHA_ACT_MAX_ROUNDS`; wakes, tokens, and
  flags can have limits (see the settings table).

Calls run on Samantha's chat thread. Coroutines run on the host's event loop.
An object that belongs to one thread (for example a sqlite3 connection) is
called on that thread, at its next line, with `sys.monitoring`.

## Memory and the journal

Samantha keeps her memory in `~/.samantha/<project-id>/` (or under
`SAMANTHA_STATE_DIR`), never in your project. The folder has user-only
permissions. The project ID is a hash of the project root path. The files:

- `model.md`: her theory of the program.
- `goals.md`: standing goals, one per line.
- `journal-<time>-<pid>.jsonl`: every observation with a metric, wake, flag,
  question, proposal, chat line, intervention (with each operation's result),
  and revert. Secrets that look like keys or tokens are redacted.
- `ledger.json`: the declared knobs and their bounds.
- `source-inventory.json`: what she read at startup.

## The demos in detail

`python demo.py NAME` runs a demo from the repository root. It checks the
Python version, the demo packages, and the key (from `.env` or the
environment). Then it starts the demo with `SAMANTHA=act`,
`SAMANTHA_AUTONOMY=goal` (`--wait`: `SAMANTHA_AUTONOMY=off`), and
`SAMANTHA_AUTONOMY_DWELL=90`. For the aliens demo (not with `--wait`) it also
sets `SAMANTHA_AUTONOMY_TRIGGER=^too hard\b`,
`SAMANTHA_AUTONOMY_SLACK=^too easy\b`, and
`SAMANTHA_AUTONOMY_TRIGGER_DWELL=10`. A setting in the environment wins over
these values, except `SAMANTHA` and `SAMANTHA_AUTONOMY`. Each demo folder
has its own `pyproject.toml`, so Samantha's project root is the demo program's
folder, and she reads only that program.

### The aliens demo

`examples/aliens/game/aliens.py` is pygame's `examples/aliens.py` (pygame
2.6.1; it runs on pygame-ce) with these changes:

- `import samantha` and six `# daemon:` lines: the goal (a fun game: hit 0.5
  to 2 times per minute of play, not more and not zero), the knobs that are
  fair game with their ranges, that the starting values are the hardest
  allowed and what not to touch (`SCORE`, `SCREENRECT`, `FUN_BAND`,
  `CRUISE_SECONDS`), the size of a move (a notch is about a quarter of a
  knob's range; at most two knobs at a time), and what to do on a "too hard"
  line (one notch easier on one or two knobs) and on a "too easy" line (one
  notch harder on one knob, back toward its starting value). An earlier
  directive, "change several of these knobs, enough to meet the goal", made
  the model's first move set all five knobs to their easy ends, which left
  nothing to do later.
- `declare_knobs()`: `samantha.expose()` for the five knobs (`BOMB_ODDS`,
  `MAX_SHOTS`, `ALIEN_ODDS`, `Bomb.speed`, `Alien.speed`), so the ranges are
  hard limits. Each range starts at the game's original value, so no change
  can make the game harder than the original.
- The game starts paused while Samantha reads it and plans her first move; P
  pauses and resumes (by key code or typed letter), Space starts, and the game
  pauses when its window loses the keyboard (for example when you click the
  terminal).
- A hit respawns the player and prints `player hit · deaths: N · score: S`. The
  original game ends at the first hit, and Samantha sees only what the program
  prints.
- The first hit prints `too hard: the first hit` (the game starts at its
  hardest), and a hit that makes more than 2 hits in the last minute of play
  prints `too hard: N hits in the last minute of play`. These are
  [miss lines](#miss-lines): Samantha applies her first move at the first hit,
  and later she can make the game easier again, at most every 10 s.
- After `CRUISE_SECONDS` of play (30 s; the environment variable
  `ALIENS_CRUISE_SECONDS` changes it) with no hit, the game prints
  `too easy: N s of play with no hit`, and the clock starts again. These are
  [slack lines](#slack-lines): Samantha makes the game a notch harder, one
  knob at a time, until the knobs are back at their starting values.
- Every 2 s of play the game prints the hits in the last minute of play and
  where that is against the fun band, for example
  `1.0 hits per minute (last minute of play) · in the fun band (0.5-2)`. This
  line is for you: it gives no metric to Samantha's sentinels, so it starts no
  gap or stall wake.

With the default of 30 s, Samantha steers toward about one hit every 30 s, the
top of the band: hits that come more often give "too hard" lines (3 in a
minute), and hits that come less often give "too easy" lines. For a game with
fewer hits, set `ALIENS_CRUISE_SECONDS` higher. Then hits that come every 30 s
to every `ALIENS_CRUISE_SECONDS` get no change: with 60, 1 to 2 hits per
minute; with 120, the whole band of 0.5 to 2.
- If there is no `data/` folder next to `aliens.py`, the game loads the images
  and sounds of the installed pygame-ce. This repository does not include them.

The game reads `BOMB_ODDS`, `MAX_SHOTS`, `ALIEN_ODDS`, and `Bomb.speed` on every
frame, so a change takes effect on the next frame. `Alien.speed` applies to new
aliens. Samantha sets the values from her own thread; a module or class
attribute assignment is atomic in CPython.

`examples/aliens/play.py` runs the game unchanged and adds the panel under it:
Samantha's state, her flags and questions, her reply when a turn changes
nothing, and a green line for each change she makes (it compares the five
knobs on each frame), marked `(easier)` or `(harder)`. Harder means each knob
moved back toward its starting value; then the panel also shows a playful
line, for example "You're cruising. Bringing the aliens back." The panel writes
these lines, not the model. The same lines go to the terminal as
`[demo] Samantha ...`. `play.py` is outside the game's project root, so
Samantha does not read or change it.

Two live runs (gpt-5.6-terra, the scripted player of `tests/stories`,
2026-10-10, 11 model calls in total):

- The theory call took 21 s and the first-move plan 5 to 6 s, at the same
  time. Each first move was one notch on two knobs (`ALIEN_ODDS` 22 → 42 and
  `BOMB_ODDS` 60 → 195; then `BOMB_ODDS` 60 → 195 and `Bomb.speed` 9 → 7). The
  green line came 0.10 s after the first hit.
- A player who was not hit after the first change (`--dodge`): each "too
  easy" line gave back one knob, 4.7 to 6.2 s after the line (one planning
  call): `ALIEN_ODDS` 42 → 32 → 22, then `BOMB_ODDS` 195 → 60. Then the knobs
  were at their starting values, and the next "too easy" line made no model
  call.
- The weak player: after 30 s with no hit, `BOMB_ODDS` went back to 60. Then
  the hits came back (3 to 5 in a minute), and three "too hard" turns made the
  game easier, one knob each, 12 to 13 s apart (`Bomb.speed` 7 → 5,
  `ALIEN_ODDS` 22 → 42, `MAX_SHOTS` 2 → 4).

License: pygame's README says "The programs in the `examples` subdirectory are
in the public domain" (pygame-ce's README says the same). The changes have the
same license as this repository. pygame-ce is LGPL 2.1; this repository
installs it and does not include it.

### The training demo

`examples/training-run/distill/train.py` teaches a small student network to
imitate a teacher function. A generator makes the training points on the fly
from three regions: common, edge, and rare. Each round prints the loss and the
fidelity per region, with `(target 0.95)`, and redraws the plot (a live window
with `--window`, and `out/curves.png`).

The rare region gets no training: `Generator.quotas()` gives each region
`int(batch × share)` points, and at weight 1.0 the rare share is 1%, so it gets
`int(96 × 0.01) = 0` points. The total loss looks healthy and flat, but
`fidelity/rare` stays near 0.53. At about round 30 the gap sentinel wakes
Samantha. She flags the gap and, in the demo, sets `sampling_weights.rare`
(declared with `samantha.expose(..., bounds=(0.1, 5.0))`). At weight 4.0 the
rare region gets 3 points per batch and passes 0.9 in about 10 rounds. The
generator reads the weights at the start of each round, so the change takes
effect at the next round. The plot marks the change, and her last messages are
under the curves.

To see the limits in the chat, type `Now set sampling_weights.rare to 9.`:
the bound is 5.0, so a set on the declared knob is refused (or the model
declines it). A set on the live path would not be refused (see
[the limits](#what-samantha-can-change-and-the-limits)).

## Development and tests

```console
python -m pip install -e ".[demos,dev]"
python -m pytest
RUN_EXAMPLE_STORIES=1 python -m pytest tests/test_examples.py
```

The test process does not start a resident of its own (`tests/conftest.py`),
so a `.env` with a real key in the repository root does not cause model calls.
The tests that skip without pygame-ce, numpy, or matplotlib need the `demos`
extra.

`tests/stories/` has the demos as unattended stories with a scripted model
(text that starts with "(scripted model)") and, for the game, a scripted
player. Only the tests use them; they are not a way to run the demos without
a key. `RUN_EXAMPLE_STORIES=1` runs them (about 3 minutes). The aliens story
has a mode in which the player is not hit after Samantha's first change
(`--dodge`), to test the "too easy" lines.

## Platform notes

- Python 3.12 or newer is required (`sys.monitoring`).
- macOS and Linux. Windows is not tested.
- macOS: a window that a terminal opens does not always get the keyboard. If P
  does nothing in the game, click the game window, then press P.
- The training demo's window uses matplotlib's GUI backend (macOS: `macosx`;
  Linux: Tk, which needs tkinter, for example `sudo apt install python3-tk`).
  With no GUI backend or no display, the demo says so and writes only
  `examples/training-run/out/curves.png`. Close the window or press Ctrl-C to
  stop.
- `multiprocessing` workers (spawn or forkserver, also PyTorch DataLoader
  workers) import your `__main__` again but do not start a resident, unless
  `SAMANTHA_ARM_CHILDREN=1`.
