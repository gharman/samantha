# Training run: Samantha finds a starved region

A small training run: a student network learns a teacher function from
synthetic data. The total loss looks healthy, but one region of the data
never trains. Samantha notices by itself, explains why when you ask, and
fixes it in the running process when you say yes.

## Install

You need Python 3.12 or newer, on Linux or macOS. A CPU is sufficient.

```console
git clone https://github.com/gharman/samantha.git
cd samantha
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e . -r examples/training-run/requirements.txt
cd examples/training-run
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
python rehearse.py               # about 30 seconds
```

`rehearse.py` starts a scripted model on your computer, starts
`distill/train.py` with Samantha in act mode, and types the two chat
messages for you. Everything else is the real Samantha. Its model text
starts with "(scripted model)".

What you see:

1. `host |` lines from the training run, every 10 rounds:
   `fidelity/common`, `fidelity/edge`, and `fidelity/rare`, with
   `(target 0.95)`. Common and edge are near 1.0. Rare stays near 0.53.
2. At about round 30, Samantha flags the gap by itself:
   `[samantha] flag: (scripted model) fidelity.rare is 0.54, 0.41 below its 0.95 target ...`.
3. `you | What do you suggest?` and the advice: raise
   `sampling_weights.rare` from 1.0 to 4.0.
4. `you | Yes, do it.` and the receipt: `Applied runtime intervention ...`
   with `✓ set exposed:sampling_weights.rare: assignment verified → 4.0`.
5. `round 032  sampling_weights.rare changed live: 1 → 4 (no restart)`.
   Then `fidelity/rare` goes up.

Expected result: five `PASS` lines, and
`fidelity/rare: 0.530 before round 32, best 0.97 after` (about). The plot is
in `out/rehearse/curves.png`.

## Run it with an OpenAI key

Samantha uses OpenAI's Responses API. The default model is `gpt-5.6-terra`.
To use a different model, set `SAMANTHA_MODEL`.

Terminal 1, the training run:

```console
cd samantha/examples/training-run
. ../../.venv/bin/activate
export OPENAI_API_KEY=...        # your key
SAMANTHA=act python distill/train.py
```

Terminal 2, the chat:

```console
cd samantha
. .venv/bin/activate
samantha
samantha> What do you suggest?
samantha> Yes, do it.
```

- Each round prints two lines: the losses, then the fidelity per region.
  One round takes about 1 second.
- At about round 30, `[samantha] flag: ...` comes in terminal 1. Then ask.
- `out/curves.png` is drawn again each round. A viewer that reloads changed
  files shows it live (macOS Preview does).
- Press Ctrl-C to stop at the end of the round. Without Ctrl-C, the run
  stops after round 299.
- To see the limits, type `Now set sampling_weights.rare to 9.` The
  allowed range is 0.1 to 5.0, so Samantha does not apply it.

Instead of `export`, you can put the key in `distill/.env`. Samantha reads
this file. Do not commit it.

## Why the rare region gets no training

`Generator.quotas()` gives each region `int(batch × share)` points. At
weight 1.0 the rare share is 1%, and `int(96 × 0.01) = 0`. Thus the rare
region gets no points, and its fidelity stays flat. At weight 4.0 it gets
3 points per batch, and it learns in about 20 rounds.

## How the program talks to Samantha

`distill/train.py` has `import samantha`, three `# daemon:` comments (the
goal, the knob and its range, and what not to touch), and one
`samantha.expose(...)` call per region with `bounds=(0.1, 5.0)`. Samantha
rejects a value outside the bounds. The program reads `sampling_weights`
at the start of each round, so a change takes effect at the next round.

`distill/pyproject.toml` makes `distill/` Samantha's project root. Samantha
reads only the files in it.

## Files

| File | Purpose |
|---|---|
| `distill/train.py` | The training run, with Samantha. `--help` lists the options. |
| `distill/pyproject.toml` | Marks `distill/` as the project root. |
| `rehearse.py` | The offline run with a scripted model. `--help` lists the options. |
| `requirements.txt` | numpy and matplotlib. |
