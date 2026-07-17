# samantha

A resident, goal-seeking LLM daemon for Python programs. Python 3.12 or newer is required.

Install Samantha, place an OpenAI key in the project's `.env`, and import it:

```console
pip install samantha
```

```dotenv
OPENAI_API_KEY=sk-...
```

```python
import samantha
```

The import arms the complete resident: observation, deterministic sentinels,
advisory cognition, persistent state, and a local chat socket. The host program
is launched normally; it does not need to be wrapped by a Samantha command.

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

The command line exists only to attach to a resident that is already running:

```console
samantha             # connects when exactly one resident is running
samantha --list      # lists resident process IDs
samantha --pid 1234  # selects a resident explicitly
```

Windows is currently untested.
