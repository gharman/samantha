# samantha

A resident, goal-seeking LLM daemon for Python programs. Python 3.12 or newer is required.

```console
samantha run examples/toy_training.py -- --steps 240
```

State is stored under the platform user-state directory, never in the observed project.

Modes are `off`, `observe`, `advise` (default), and `act`. Actuation additionally
requires earned per-knob trust. No API key is needed for observe mode or tests.

Operator intent can be supplied by `SAMANTHA_GOAL`, `samantha.goal()`, chat, or
read-only source comments:

```python
# daemon: goal — all heads within 0.5pt of fidelity ceiling by end of run
# daemon: sampling_weights are fair game within 0.1–5.0
# daemon: never touch anything in eval_*, seeding, or checkpoint paths
# daemon: this timeout is load-bearing, hands off
```

Useful commands include `samantha ps`, `samantha status`, `samantha chat`,
`samantha report`, `samantha export`, and `samantha forget --claims`.

Windows is currently untested.
