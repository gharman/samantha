# Aliens: Samantha helps you win a game

pygame's small alien shooter, with `import samantha` and three `# daemon:`
comments added. The game has a goal: the player is hit less than once per
minute. When you keep losing, you type `help`, and Samantha makes the game
easier while it runs, with no restart.

## Install

You need Python 3.12 or newer, on Linux or macOS.

```console
git clone https://github.com/gharman/samantha.git
cd samantha
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e . -r examples/aliens/requirements.txt
cd examples/aliens
```

On macOS without `python3.12`, install it first (for example
`brew install python@3.12`).

To update later, pull in your clone. The editable install (`-e`) uses the
new code at once:

```console
cd samantha
git pull
```

## Try it offline (no API key)

```console
python rehearse.py               # a window; about 5 minutes
python rehearse.py --headless    # no window; about 30 seconds
```

`rehearse.py` starts a scripted model on your computer and a scripted
player that is not good at the game. Everything else is the real Samantha.
Its model text starts with "(scripted model)". With a window, the game
pauses when you click a different window. To continue, click the game
window and press P.

What you see:

1. `[game] paused`, then `[game] resumed` when Samantha is ready.
2. `player hit` lines, and every 5 seconds a line such as
   `5.7 hits per minute · goal met: 0 (target 1)`.
3. After about 100 seconds of game time, Samantha flags the unmet goal by
   itself: `[samantha] flag: ...`. The script then types `help`.
4. Samantha's receipt: `Applied runtime intervention ...` with
   `✓ set module:__main__::BOMB_ODDS: assignment verified → 600` (also
   `Bomb.speed` and `MAX_SHOTS`).
5. `BOMB_ODDS changed live: 60 → 600 (no restart)`. Then the hits per minute
   go down.

Expected result: four `PASS` lines. The last one is
`the player is hit at most half as often after the change`.

## Play it yourself (with an OpenAI key)

Samantha uses OpenAI's Responses API. The default model is `gpt-5.6-terra`.
To use a different model, set `SAMANTHA_MODEL`.

Terminal 1, the game:

```console
cd samantha/examples/aliens
. ../../.venv/bin/activate
export OPENAI_API_KEY=...        # your key
SAMANTHA=act python game/aliens.py
```

Terminal 2, the chat:

```console
cd samantha
. .venv/bin/activate
samantha
samantha> help
samantha> slow the aliens down
```

- The game starts paused while Samantha reads it. Press **P** (or
  **Space**) to play. **P** pauses and resumes. Arrows move, Space fires,
  F toggles full screen, Esc quits.
- If P does nothing, click the game window, then press P. On macOS, a
  window that a terminal opens does not always get the keyboard. The pause
  screen then says "Click the game window, then press P." The game also
  pauses when its window loses the keyboard, for example when you click
  the terminal to type to Samantha.
- Each press prints `[game] paused` or `[game] resumed` in terminal 1.
- Press P before you type to Samantha, and press P again after the reply.
- `help` makes one small change. Ask for more in your own words.

Instead of `export`, you can put the settings in `game/.env`. Samantha
reads this file. Do not commit it.

```dotenv
OPENAI_API_KEY=...
SAMANTHA=act
```

## What changed in pygame's file

`game/aliens.py` is pygame 2.6.1's `examples/aliens.py` with these changes:

- `import samantha` and three `# daemon:` comments: the goal, the
  constants that Samantha can change (with ranges), and what it must not
  touch.
- The game starts paused. P pauses and resumes (by key code and by typed
  letter), Space starts, and a lost window focus pauses.
- A hit respawns the player and prints a line. The original game stops at
  the first hit, and Samantha sees only what the program prints.
- Every 5 seconds of play, the game prints the hits per minute and
  `goal met: 0` or `1`. Samantha's sentinel watches this line.
- If there is no `data/` directory next to `aliens.py`, the game loads the
  images and sounds of the installed pygame. This repository does not
  include them.

`game/pyproject.toml` makes `game/` Samantha's project root. Samantha reads
only the files in it.

## Files

| File | Purpose |
|---|---|
| `game/aliens.py` | The game, with Samantha. |
| `game/pyproject.toml` | Marks `game/` as the project root. |
| `rehearse.py` | The offline run: scripted model and player. `--help` lists the options. |
| `requirements.txt` | pygame. |

## License

pygame's README says: "The programs in the `examples` subdirectory are in
the public domain." The changes to `aliens.py` have the same license as
this repository. pygame itself is LGPL 2.1; this example installs it and
does not include it.
