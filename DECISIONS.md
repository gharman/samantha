# Implementation decisions

## M0

The runner uses `runpy.run_path` in the current process and restores `sys.argv` exactly. Configuration is represented by a small dataclass, with command-line values applied only when non-null so environment precedence remains intact. State writes use atomic replacement for JSON snapshots and a locked append for journals. The runtime directory is discovered but is not created until the chat milestone, avoiding filesystem effects for dormant imports.
The declared `platformdirs` dependency has a minimal source-tree fallback so off-mode and development invocations work before package installation.

## M1

Observation uses synchronous, transparent stream wrappers and a root logging handler; metric extraction itself is bounded and cheap, while journaling is limited to lines that actually contain metrics. Carriage-return displays are sampled at most once per second. Census is deliberately namespace-led instead of retaining arbitrary heap objects, which avoids prolonging host object lifetimes. Static hot-read analysis exposes conservative candidates and requires later dynamic verification before reliance.

## M2

Sentinels evaluate bounded metric windows and suppress a signature after its first firing in a run. Cognition is invoked explicitly by the harness, allowing all keyless tests to inject `FakeLLM`; runner shutdown always journals deterministic sentinel evidence. The harness accepts only enumerated structured actions, downgrades unauthorized interventions, and isolates provider or parse failures.

## M3

Chat uses one daemon accept thread and handles one local client at a time, which is sufficient for the single-operator protocol and keeps synchronization small. Each inbound and outbound message is journaled through the common redaction path. The runtime directory must have no group or world permissions; socket files use mode 0600 and are removed on orderly shutdown.

## M4

Claims store the source path, optional enclosing function, and AST-derived fingerprint together. Validation demotes stale facts in place rather than deleting their history. Baselines retain compact envelopes and a conservative semantic direction; they are refreshed at orderly runner shutdown. Forget removes only state snapshots and intentionally preserves append-only journals unless an exported state directory is removed by the operator.

## M5

Actuation can reach only explicitly registered Python setters or object attributes. Numeric bounds are checked again at assignment time, terminal protected/dead states cannot transition, and only trusted knobs are authorized. Probes always attempt exact restoration in a `finally` block. The intervention manager permits one live change and records both prediction and inverse recipe data before outcome evaluation.

## M6

Recurrence identity is a stable textual signature of the affected knob, flag text, or proposal body, and the threshold counts distinct run IDs rather than raw event count. Reports remain useful without an API key by emitting a conservative diff sketch; callers may inject the same structured LLM interface to refine it. Export is an explicit CLI operation and is the only feature that writes an artifact in the current directory.

## Import-first product correction

The installed library now starts its complete resident from a plain `import samantha`; the host is always launched normally. A dependency-free nearest-ancestor `.env` reader supplies `OPENAI_API_KEY` without overriding an existing environment variable. Cognition uses OpenAI's Responses API with a configurable balanced-model default. The console script has one responsibility: discover and attach to an already-running resident socket; operational subcommands and the launch wrapper are no longer public product surfaces.

## Startup and operator experience correction

Project identity is displayed in full for state correlation, while attachment is explicitly PID-based. Startup reports the actual entry point, goal/directive inventory, watch set, credential source, and configured model; it no longer emits a generic placeholder theory. The entry source is retained in `model.md` so initial chat questions have useful project context. OpenAI credentials, connectivity, and model access are probed immediately on a daemon cognition thread, preserving non-blocking imports while surfacing sanitized failures before operator chat. The terminal client decodes NDJSON internally and renders a human-readable connection banner and replies.

## Cold-start comprehension correction

Source discovery now follows the local import closure first and then inventories the remaining project Python files, largest first, under a bounded 180,000-character budget while excluding environments, caches, VCS data, and build artifacts. README, pyproject configuration, docstrings, TODO/FIXME notes, and all daemon directives are included. After provider validation, a dedicated cold-start model call must produce a concrete summary, architecture, goals, watch plan, knob candidates, protected areas, and ambiguities; this becomes `model.md` and a journaled inference. Chat remains explicitly in an analyzing state until this pass succeeds and thereafter receives both the theory and recent journal evidence.

## Visible memory location correction

Persistent state now defaults explicitly to `~/.samantha/<project-id>/` rather than the platform-specific application-state path. Both the base and project directory are created during import with mode 0700 where supported, the resolved path is printed in the startup ritual, and `SAMANTHA_STATE_DIR` continues to take precedence. This makes resident memory discoverable and inspectable without knowing platform directory conventions.

## Console entry point and analysis timeout correction

Import-time entry discovery now examines the Python call stack and selects the nearest project application frame outside Samantha, virtual environments, and site-packages, falling back to `sys.argv[0]` only when necessary. This prevents generated console shims such as `.venv/bin/loop` from becoming the analyzed entry point. Provider health and cold-start comprehension have separate states: credential/model validation can remain healthy when theory formation times out, and chat stays usable. LLM reads default to 120 seconds through `SAMANTHA_LLM_TIMEOUT`, while the source payload is prioritized and bounded to 180,000 characters.

## Useful-before-ready correction

Cold-start model input is compacted to a project-wide digest of AST outlines, docstrings, imports, CLI help strings, directives, notes, configuration, and bounded source excerpts, with extra budget assigned to the entry point. The inventory briefing states the discovered purpose, entry point, file/module surface, operator directives, watch categories, observed metrics, and recent host output. Once analysis is ready, the chat prompt explicitly treats the persisted source theory as evidence and requires separate statements about source facts versus absent live telemetry.

## Responses parsing and conversational fallback correction

Raw Responses API payloads are no longer assumed to place message text in the first output item. The parser walks every output item and content block, skipping reasoning and tool records, recognizing refusals, and reporting incomplete/no-text states explicitly. When cold-start analysis is pending or failed, the deterministic inventory briefing becomes context for a normal model chat call together with the current operator message, prior chat events, and recent process activity. It is rendered directly only when the chat request itself fails, preserving a genuine conversation throughout initialization.

## Structured cognition and conversational CLI correction

Cold-start analysis now uses a strict Responses API JSON schema with a required summary and string arrays for architecture, goals, watching, knob candidates, protected areas, and ambiguities; prompt-only JSON formatting is no longer trusted. Operator chat uses a separate unconstrained text response path, so ordinary conversation is preserved and never parsed as a cognition action object. The CLI loads platform `readline` support and persists up to 1,000 prompts at `~/.samantha/cli-history` with mode 0600, providing arrow-key recall across sessions.

## Introspection-first actuation correction

Explicit application handlers and `expose()` registrations are optional hints rather than actuation prerequisites. In act mode Samantha constructs an ephemeral bounded graph of loaded project modules and active project frames, recursively indexing object dictionaries, mappings, lists, classes, instances, and callable signatures under exact runtime paths. A strict structured planner may iteratively search or inspect that graph before selecting fixed audited operations: assignment, append/enqueue, invocation of existing project-defined callables, generic argument interception, and operator receipt emission. Model-authored code, source writes, and model-directed imports remain prohibited. Calls are not limited to project-defined callables: any callable in the graph can be invoked except a small denylist (eval, exec, compile, subprocess, and os.system/popen/spawn*/execv*). Declared bounds are checked only on `exposed:` paths. See the limits in docs/GUIDE.md.

Mutations are grouped by operator intent rather than restricted to one lever. Each group records exact operation results, verification state, an inferred one-shot/temporary/process/until-failure/contextual lifetime, and available inverse assignments. One-shot interceptors restore themselves on first use; process and contextual assignments remain unless reverted. Per-path locks reject conflicting simultaneous mutation, and chat receipts are constructed from executor results so planned or failed work cannot be represented as applied.

## Conditional interception and result-bearing chat correction

Generic interceptors now accept declarative metadata rather than model-authored code: a named-argument equality condition, argument overrides, string result replacements, mapping result updates, and an optional receipt. One-shot restoration occurs after the first matching invocation rather than the first unrelated call. After any mutating or callable operation, the controller performs a second conversational synthesis over verified tool results and newly captured host output; return values are also rendered in the factual receipt. Every act-planning round receives recent bidirectional chat events, preserving the active subject for elliptical follow-ups such as “ok, and?”.

## Orphan removal and declared knobs

Modules left behind by the import-first and introspection-first corrections were
removed rather than kept as unreachable code: the one-lever intervention manager and
reversibility probe, the recurrence report, the in-process runner, the census and
hot-read helpers, the read-only tool facade, fingerprint-bound claims, and learned
baselines, along with the ledger's trust ladder and the unused metric helpers. The
ledger now records only protected paths and declared knobs. `samantha.expose()`
declarations are offered to the act planner as bounds-checked `exposed:` paths, and
`samantha.silence()` pauses sentinel wakes.

## Owning-thread calls without host hooks

Thread-affine objects reject calls from the chat thread. Rather than require a host
pump or executor, a call that fails with a thread-affinity error is retried on the
owning thread identified by the error. PEP 669 monitoring enables LINE events only
on code objects currently on that thread's stack, the first event on that thread
runs the call, and the monitoring is removed. A thread blocked in native code times
out, and a cancelled job never runs later.
