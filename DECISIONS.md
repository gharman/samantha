# Implementation decisions

## M0

The runner uses `runpy.run_path` in the current process and restores `sys.argv` exactly. Configuration is represented by a small dataclass, with command-line values applied only when non-null so environment precedence remains intact. State writes use atomic replacement for JSON snapshots and a locked append for journals. The runtime directory is discovered but is not created until the chat milestone, avoiding filesystem effects for dormant imports.
The declared `platformdirs` dependency has a minimal source-tree fallback so off-mode and development invocations work before package installation.
