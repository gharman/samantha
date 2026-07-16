# Implementation decisions

## M0

The runner uses `runpy.run_path` in the current process and restores `sys.argv` exactly. Configuration is represented by a small dataclass, with command-line values applied only when non-null so environment precedence remains intact. State writes use atomic replacement for JSON snapshots and a locked append for journals. The runtime directory is discovered but is not created until the chat milestone, avoiding filesystem effects for dormant imports.
The declared `platformdirs` dependency has a minimal source-tree fallback so off-mode and development invocations work before package installation.

## M1

Observation uses synchronous, transparent stream wrappers and a root logging handler; metric extraction itself is bounded and cheap, while journaling is limited to lines that actually contain metrics. Carriage-return displays are sampled at most once per second. Census is deliberately namespace-led instead of retaining arbitrary heap objects, which avoids prolonging host object lifetimes. Static hot-read analysis exposes conservative candidates and requires later dynamic verification before reliance.

## M2

Sentinels evaluate bounded metric windows and suppress a signature after its first firing in a run. Cognition is invoked explicitly by the harness, allowing all keyless tests to inject `FakeLLM`; runner shutdown always journals deterministic sentinel evidence. The harness accepts only enumerated structured actions, downgrades unauthorized interventions, and isolates provider or parse failures.
