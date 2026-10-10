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

The planner sees each declared knob as `exposed:<name>`, with its value and
bounds, and prefers these paths. A value outside the bounds is refused. Your
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

The limits:

- **No code from the model.** No `exec`, `eval`, `compile`, subprocesses,
  process launches, imports, or source file writes.
- **Act only in act mode,** and only on your request or, with
  `SAMANTHA_AUTONOMY=goal`, on a goal gap.
- **Bounds.** A declared knob refuses a value outside its bounds.
- **Protected paths.** Paths with the segments `eval`, `seed`, `test`, `tests`,
  `ckpt`, or `checkpoint` cannot be changed.
- **Your directives win.** The charter tells her to follow `# daemon:` lines and
  to prefer the smallest change unless you say otherwise.
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
`SAMANTHA_AUTONOMY_DWELL=90` (unless the environment sets it). Each demo folder
has its own `pyproject.toml`, so Samantha's project root is the demo program's
folder, and she reads only that program.

### The aliens demo

`examples/aliens/game/aliens.py` is pygame's `examples/aliens.py` (pygame
2.6.1; it runs on pygame-ce) with these changes:

- `import samantha` and four `# daemon:` lines: the goal (hit less than once
  per minute), the knobs that are fair game with their ranges, what not to
  touch (`SCORE`, `SCREENRECT`, and "never make the game harder"), and "when
  the goal is not met, act at once: change several of these knobs, enough to
  meet the goal". In earlier live tests without the last line, the model
  followed its charter ("prefer the smallest nudge"): `help` changed one knob
  a little (for example `BOMB_ODDS` 60 → 120), which did not help a player who
  kept getting hit.
- `declare_knobs()`: `samantha.expose()` for the five knobs (`BOMB_ODDS`,
  `MAX_SHOTS`, `ALIEN_ODDS`, `Bomb.speed`, `Alien.speed`), so the ranges are
  hard limits.
- The game starts paused while Samantha reads it; P pauses and resumes (by key
  code or typed letter), Space starts, and the game pauses when its window
  loses the keyboard (for example when you click the terminal).
- A hit respawns the player and prints `player hit · deaths: N · score: S`. The
  original game ends at the first hit, and Samantha sees only what the program
  prints.
- Every 2 s of play the game prints the hits in the last minute and
  `goal met: 0` or `1` `(target 1)`. This line is the metric for the gap
  sentinel. The hit rate is printed before the words, so it is not a metric
  with that target.
- If there is no `data/` folder next to `aliens.py`, the game loads the images
  and sounds of the installed pygame-ce. This repository does not include them.

The game reads `BOMB_ODDS`, `MAX_SHOTS`, `ALIEN_ODDS`, and `Bomb.speed` on every
frame, so a change takes effect on the next frame. `Alien.speed` applies to new
aliens. Samantha sets the values from her own thread; a module or class
attribute assignment is atomic in CPython.

`examples/aliens/play.py` runs the game unchanged and adds the panel under it:
Samantha's state, her flags and questions, and a green line for each change
she makes (it compares the five knobs on each frame). The same lines go to the
terminal as `[demo] Samantha changed ...`. `play.py` is outside the game's
project root, so Samantha does not read or change it.

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
the bound is 5.0, so the change is refused (or the model declines it).

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
a key. `RUN_EXAMPLE_STORIES=1` runs them (about 1 minute).

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
