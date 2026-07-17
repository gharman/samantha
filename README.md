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

Startup identifies the full project and attach PID, summarizes the entry source
and active watch set, and asynchronously validates OpenAI credentials and model
access. Provider failures are reported at startup with a sanitized API error.
After validation, Samantha follows local imports and inventories the bounded
project source surface, README, configuration, docstrings, TODO/FIXME notes, and
`# daemon:` directives. A cold-start model pass turns that material into a
persistent program theory, architecture map, inferred goals, specific watch
plan, knob candidates, protected areas, and explicit ambiguities before chat is
considered ready.

Cold-start analysis uses a separate 120-second read timeout, configurable with
`SAMANTHA_LLM_TIMEOUT`. A theory-building timeout does not mark the provider or
chat unavailable; it is reported as a retryable analysis failure.

While deeper analysis is pending or has failed, chat returns an immediate source
briefing built from AST outlines, docstrings, CLI help, imports, directives, and
the bounded project manifest as context to a normal LLM conversation. The user’s
message and prior chat history are still honored. The deterministic briefing is
shown directly only when that chat model request also fails. Recent host output
and extracted metrics keep source knowledge and live activity distinct.

State is stored under `~/.samantha/<project-id>/`, never in the observed project.
The directory is created during import with user-only permissions. Set
`SAMANTHA_STATE_DIR` to override the base directory for tests or custom deployments.

Set `SAMANTHA_QUIET=1` in the host program's environment (or its `.env`) to
suppress Samantha's `[samantha]` console messages. This setting belongs to the
program importing Samantha, not the later attach CLI. For selective visibility,
use `SAMANTHA_LOG_LEVEL=WARNING`, `ERROR`, or `CRITICAL` instead; `INFO` is the
default and `OFF` suppresses all Samantha logger output.

Modes are `off`, `observe`, `advise` (default), and `act`. Actuation additionally
requires an explicit `SAMANTHA=act` in the host process. No API key is needed for
observe mode or tests.

In act mode, application-specific handlers and `expose()` declarations are not
required. Samantha indexes live project modules, active project frames, globals,
mappings, collections, classes, instances, and existing project callables. Its
audited generic operations can inspect/search paths, assign attributes or items,
append/enqueue values, invoke reachable project and application-held API callables,
pass existing live objects by reference, install fixed argument
interceptors, and emit operator receipts. It never executes model-authored code.
Interceptors can match named argument values and declaratively transform string
or mapping results. They can also merge or append values inside a future call's
mapping arguments, so a one-shot generation constraint is attached where it will
actually be consumed instead of being added to an already-running batch. Audited
stdout/stderr rewrite rules support narrow future presentation changes such as a
single phase label without modifying source or requiring an application handler.

Related operations form an intervention group with an inferred `one_shot`,
`temporary`, `process`, `until_failure`, or contextual lifetime. Successful
changes remain when that lifetime calls for it; retained inverse assignments
support rollback. Multiple related changes may be applied together, while
conflicting changes to the same path are rejected.

After a callable or mutation runs, Samantha performs a post-tool response pass
over verified return values and newly captured host output. Intervention receipts
therefore include the result and answer the operator’s original question rather
than merely promising a later retrieval. Act planning also includes recent chat
history so follow-ups retain their subject.

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

The interactive client supports up/down-arrow history through `readline` and
persists the last 1,000 prompts in `~/.samantha/cli-history` with mode 0600.
Chat is a normal plain-text model conversation even while deeper source analysis
is incomplete; structured JSON is reserved for internal cognition actions.

Windows is currently untested.
