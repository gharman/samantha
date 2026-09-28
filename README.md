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

Cold-start analysis and chat have no response timeout by default. Set
`SAMANTHA_LLM_TIMEOUT` to a positive number of seconds if a deployment needs a
limit. A theory-building timeout does not mark the provider or chat unavailable;
it is reported as a retryable analysis failure.

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

Samantha's cognition and act-planning loops are unbudgeted by default. Optional
positive limits can be set with `SAMANTHA_ACT_MAX_ROUNDS`,
`SAMANTHA_MAX_WAKES_PER_HOUR`, `SAMANTHA_TOKEN_BUDGET_PER_DAY`, and
`SAMANTHA_FLAG_BUDGET`; zero means unlimited for each setting. A value that is
not a number is ignored with a warning and treated as zero.

If Samantha cannot start, the host keeps running and a single
`[samantha] disabled: <error>` line goes to stderr (suppressed by
`SAMANTHA_QUIET`).

Worker processes started by `multiprocessing` (spawn or forkserver, including
PyTorch DataLoader workers) re-import the host's `__main__` but do not arm their
own resident. Set `SAMANTHA_ARM_CHILDREN=1` to arm one in each worker anyway.

In act mode, application-specific handlers are not required, but a host can
declare preferred knobs:

```python
samantha.expose("learning_rate", obj=cfg, attr="lr", bounds=(1e-5, 0.1))
samantha.silence(minutes=10)  # pause sentinel findings; chat keeps working
```

Declared knobs appear to the planner as `exposed:<name>` paths, and values outside
their bounds are rejected. Beyond them, Samantha indexes live project modules, active project frames, globals,
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
An interceptor can activate an output rewrite only when its matching future call
occurs, coupling a deferred state change and its presentation rather than racing
independent “next event” rules against already-queued work.
When a live progress, rendering, or printing callback is reachable, Samantha
intercepts that callable directly and can condition changes on message content.
Raw stdout/stderr rewriting is a fallback because console libraries may bypass
Python's current `sys.stdout` or `sys.stderr` wrappers.

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

Operator intent can be supplied by `SAMANTHA_GOAL`, `samantha.goal()`, `/goal <text>`
in chat (`/goal` alone shows the current goal), or read-only source comments. The
current goal and any standing goals in `goals.md` are included in chat, act planning,
and sentinel wakes:

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
It prints `Samantha is working…` immediately after sending a request because act
mode may require several sequential model-assisted inspection rounds; cold-start
analysis runs on a separate background thread and does not gate attached chat.
If the resident process exits, the CLI handles the closed socket without a
traceback and reports whether the last message was unsent or its outcome is
unknown. It then offers detached chat using the persisted project theory, recent
conversation, and the project's model credentials. Detached chat cannot inspect
or change live runtime state and says so explicitly.
Chat is a normal plain-text model conversation even while deeper source analysis
is incomplete; structured JSON is reserved for internal cognition actions.

Windows is currently untested.
