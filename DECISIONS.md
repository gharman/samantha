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
