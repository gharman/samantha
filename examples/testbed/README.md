# Samantha testbed

A small, self-contained host program for exercising Samantha end to end. It has
its own `pyproject.toml`, so Samantha treats this directory as the project root.

It prints per-head metric lines plus a constant `gpu/util` series (which trips the
stall sentinel), can emit a WARNING on demand, and keeps live state for act mode
to find: the `cfg` dict, the `settings` dataclass, the `jobs` worker queue, and a
`trainer` object whose methods (`status`, `boost`, `pause`, `resume`) exercise method
calls and interception.
On exit it reports the slowest `print()`, which exposes host-thread stalls.

```console
cd examples/testbed
export SAMANTHA_STATE_DIR=/tmp/samantha-testbed XDG_RUNTIME_DIR=/tmp/sam-run

python -m testbed.app --steps 0 --fake-llm            # run until Ctrl-C, no API key
python -m testbed.app --warn-at 5 --fake-delay 3      # WARNING line at step 5
SAMANTHA=act python -m testbed.app --steps 0 --fake-llm

python ask.py "what is this program?"                 # scripted chat
python ask.py --hangup "slow question"                # disconnect mid-reply
samantha                                              # interactive attach
```

`--fake-llm` installs `testbed/fake.py`, a scripted model with `--fake-delay`
latency. In act mode, `TESTBED_PLANS=plans.json` supplies a list of
`plan_action` responses consumed one per planning round. Without `--fake-llm`,
the normal provider configuration (`OPENAI_API_KEY`, `.env`) applies.

`python -m testbed` (the package's `__main__.py`) currently never arms Samantha;
see issue #6. Use `python -m testbed.app` until that is fixed.
