"""Safe startup and configuration resolution."""

from __future__ import annotations

import atexit
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import discover_project
from .state import Journal, save_json, state_root

LOG = logging.getLogger("samantha")
# Each act-planning round is a full model call over the live inventory; an
# unbounded default let one chat message run up cost and latency indefinitely.
DEFAULT_ACT_MAX_ROUNDS = 12
# One autonomous act turn per series per this many seconds (SAMANTHA_AUTONOMY_DWELL).
DEFAULT_AUTONOMY_DWELL = 600
MIN_AUTONOMY_DWELL = 30
# One act turn on miss lines (SAMANTHA_AUTONOMY_TRIGGER) per this many seconds
# (SAMANTHA_AUTONOMY_TRIGGER_DWELL).
DEFAULT_TRIGGER_DWELL = 15
MIN_TRIGGER_DWELL = 10
_resident: Resident | None = None


@dataclass
class Config:
    mode: str = "advise"
    goal: str | None = None
    model: str = "gpt-5.6-terra"
    base_url: str = "https://api.openai.com"
    api_key: str | None = None
    state_dir: str | None = None
    max_wakes_per_hour: int = 0
    token_budget_per_day: int = 0
    flag_budget: int = 0
    dotenv_file: str | None = None
    llm_timeout: float = 0.0
    act_max_rounds: int = DEFAULT_ACT_MAX_ROUNDS
    # "goal": in act mode, an aspiration-gap wake also gets one act turn.
    autonomy: str = "off"
    # Seconds between autonomous act turns for one series (and gap re-fires).
    autonomy_dwell: float = DEFAULT_AUTONOMY_DWELL
    # A regular expression: a program output line that matches it is a miss
    # against the goal (goal autonomy in act mode only). The first miss applies
    # a move planned at startup; later misses get act turns, one per trigger_dwell.
    autonomy_trigger: str | None = None
    trigger_dwell: float = DEFAULT_TRIGGER_DWELL
    log_level: str = "INFO"
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def resolve(cls, **overrides: object) -> "Config":
        dotenv_start = overrides.pop("_dotenv_start", None)
        dotenv_file, dotenv_values = _load_dotenv(dotenv_start)
        warnings: list[str] = []

        def env(key: str, default: str | None = None) -> str | None:
            if key in os.environ:
                return os.environ[key]
            return dotenv_values.get(key, default)

        def number(
            key: str, cast: type[int] | type[float], default: int = 0
        ) -> int | float:
            return _env_number(key, cast, warnings, env, default)

        values = dict(
            mode=env("SAMANTHA", "advise"),
            goal=env("SAMANTHA_GOAL"),
            model=env("SAMANTHA_MODEL", "gpt-5.6-terra"),
            base_url=env("SAMANTHA_BASE_URL", "https://api.openai.com"),
            api_key=env("OPENAI_API_KEY"),
            state_dir=env("SAMANTHA_STATE_DIR"),
            max_wakes_per_hour=number("SAMANTHA_MAX_WAKES_PER_HOUR", int),
            token_budget_per_day=number("SAMANTHA_TOKEN_BUDGET_PER_DAY", int),
            flag_budget=number("SAMANTHA_FLAG_BUDGET", int),
            dotenv_file=str(dotenv_file) if dotenv_file else None,
            llm_timeout=number("SAMANTHA_LLM_TIMEOUT", float),
            act_max_rounds=number(
                "SAMANTHA_ACT_MAX_ROUNDS", int, DEFAULT_ACT_MAX_ROUNDS
            ),
            autonomy=(env("SAMANTHA_AUTONOMY", "off") or "off").strip().lower(),
            autonomy_dwell=number(
                "SAMANTHA_AUTONOMY_DWELL", float, DEFAULT_AUTONOMY_DWELL
            ),
            autonomy_trigger=(env("SAMANTHA_AUTONOMY_TRIGGER") or "").strip() or None,
            trigger_dwell=number(
                "SAMANTHA_AUTONOMY_TRIGGER_DWELL", float, DEFAULT_TRIGGER_DWELL
            ),
            log_level=(
                "OFF"
                if _truthy(env("SAMANTHA_QUIET"))
                else env("SAMANTHA_LOG_LEVEL", "INFO").upper()
            ),
            warnings=warnings,
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        if values["mode"] not in {"off", "observe", "advise", "act"}:
            values["mode"] = "advise"
        if values["autonomy"] not in {"off", "goal"}:
            warnings.append(
                f"SAMANTHA_AUTONOMY={values['autonomy']!r} is not 'off' or 'goal'; "
                "using 'off'"
            )
            values["autonomy"] = "off"
        if values["autonomy_dwell"] < MIN_AUTONOMY_DWELL:
            warnings.append(
                f"SAMANTHA_AUTONOMY_DWELL={values['autonomy_dwell']:g} is below "
                f"{MIN_AUTONOMY_DWELL} seconds; using {MIN_AUTONOMY_DWELL}"
            )
            values["autonomy_dwell"] = float(MIN_AUTONOMY_DWELL)
        if values["trigger_dwell"] < MIN_TRIGGER_DWELL:
            warnings.append(
                f"SAMANTHA_AUTONOMY_TRIGGER_DWELL={values['trigger_dwell']:g} is below "
                f"{MIN_TRIGGER_DWELL} seconds; using {MIN_TRIGGER_DWELL}"
            )
            values["trigger_dwell"] = float(MIN_TRIGGER_DWELL)
        if trigger := values["autonomy_trigger"]:
            try:
                re.compile(trigger)
            except re.error as exc:
                warnings.append(
                    f"SAMANTHA_AUTONOMY_TRIGGER={trigger!r} is not a valid regular "
                    f"expression ({exc}); ignoring it"
                )
                values["autonomy_trigger"] = None
        if values["autonomy_trigger"] and not (
            values["mode"] == "act" and values["autonomy"] == "goal"
        ):
            warnings.append(
                "SAMANTHA_AUTONOMY_TRIGGER needs SAMANTHA=act and "
                "SAMANTHA_AUTONOMY=goal; ignoring it"
            )
            values["autonomy_trigger"] = None
        return cls(**values)


def _env_number(
    name: str,
    cast: type[int] | type[float],
    warnings: list[str],
    lookup: Callable[[str], str | None] = os.getenv,
    default: int = 0,
) -> int | float:
    """Read a numeric setting; unset or malformed values use the default."""
    raw = (lookup(name) or "").strip()
    if not raw:
        return cast(default)
    try:
        return cast(raw)
    except ValueError:
        warnings.append(f"ignoring {name}={raw!r}: expected a number; using {default}")
        return cast(default)


def _dotenv_key_allowed(key: str) -> bool:
    """Only Samantha's own settings and the OpenAI key may come from .env."""
    return key == "OPENAI_API_KEY" or key.startswith("SAMANTHA")


_INLINE_COMMENT = re.compile(r"\s+#")


def _dotenv_value(raw: str) -> str:
    value = raw.strip()
    if value and value[0] in "'\"":
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    return _INLINE_COMMENT.split(value, maxsplit=1)[0].strip()


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.removeprefix("export ").partition("=")
        key = key.strip()
        if not _dotenv_key_allowed(key):
            continue
        values[key] = _dotenv_value(raw_value)
    return values


def _load_dotenv(start: Path | None = None) -> tuple[Path | None, dict[str, str]]:
    """Parse .env files without touching the host environment.

    Two places are read: the project root (start) and the working directory,
    when it is a different directory. A setting in the project root's file wins
    over the same setting in the working directory's file. Parent directories
    are never searched. The returned path is the first file that was found.
    """
    found = _dotenv_files(start)
    values: dict[str, str] = {}
    for path in reversed(found):
        values.update(_read_dotenv(path))
    return (found[0] if found else None), values


def _dotenv_files(start: Path | None = None) -> list[Path]:
    """The .env files that apply, most important first (see _load_dotenv)."""
    found: list[Path] = []
    for folder in (start or Path.cwd(), Path.cwd()):
        path = folder.resolve() / ".env"
        if path.is_file() and path not in found:
            found.append(path)
    return found


def _key_source(start: Path | None) -> str:
    """Where OPENAI_API_KEY comes from, for the startup line."""
    if "OPENAI_API_KEY" in os.environ:
        return "process environment"
    for path in _dotenv_files(start):
        if "OPENAI_API_KEY" in _read_dotenv(path):
            return str(path)
    return "process environment"


@dataclass
class Resident:
    config: Config
    root: Path
    key: str
    journal: Journal
    state: Path

    def stop(self) -> None:
        for name in ("observer", "cognition", "chat"):
            component = getattr(self, name, None)
            if component and hasattr(component, "stop"):
                component.stop()
        self.journal.close()


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _logging(config: Config) -> None:
    if not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[samantha] %(message)s"))
        LOG.addHandler(handler)
    level = config.log_level.upper()
    LOG.disabled = level == "OFF"
    LOG.setLevel(getattr(logging, level, logging.INFO))
    LOG.propagate = False


def arm(entry: str | Path | None = None, **overrides: object) -> Resident | None:
    global _resident
    root, key = discover_project(entry)
    config = Config.resolve(_dotenv_start=root, **overrides)
    if config.mode == "off":
        return None
    if _resident:
        return _resident
    _logging(config)
    for warning in config.warnings:
        LOG.warning(warning)
    state = state_root(key, config.state_dir)
    journal = Journal(state)
    _resident = Resident(config, root, key, journal, state)
    from .act.ledger import Ledger

    _resident.ledger = Ledger(state / "ledger.json")
    if not config.api_key and config.mode in {"advise", "act"}:
        LOG.info("no API key; cognition dormant")
    LOG.info(
        "resident armed · mode=%s · pid=%s · project=%s",
        config.mode,
        os.getpid(),
        key,
    )
    LOG.info("memory: %s", state)
    journal.write(
        "observation", source="boot", payload={"mode": config.mode, "project": key}
    )
    try:
        from .observe import Observer

        _resident.observer = Observer(journal)
        _resident.observer.start()
    except Exception as exc:
        LOG.warning("observer disabled: %s", exc)
    if entry:
        try:
            from .observe.source import read_source

            inventory = read_source(Path(entry).resolve(), root)
            save_json(state / "source-inventory.json", inventory)
            docstring = next(
                (v["docstring"] for v in inventory["files"].values() if v["docstring"]),
                None,
            )
            relative_entry = str(Path(entry).resolve().relative_to(root))
            theory = config.goal or docstring or f"Entry point {relative_entry}"
            entry_source = inventory["files"].get(relative_entry, {}).get("text", "")
            directives = inventory["directives"]
            model = (
                "# Program inventory\n\n"
                f"- Project root: {root}\n"
                f"- Entry point: {relative_entry}\n"
                f"- Current purpose: {theory}\n"
                f"- Operator goal: {config.goal or 'not specified'}\n"
                f"- Daemon directives: {directives or 'none'}\n\n"
                "## Entry source\n\n```python\n"
                f"{entry_source[:20_000]}\n```\n"
            )
            (state / "model.md").write_text(model)
            (state / "goals.md").touch(exist_ok=True)
            from .cognition.harness import directive_goal

            goal_status = config.goal or (
                f"{found} (# daemon:)"
                if (found := directive_goal([str(d) for d in directives]))
                else "not specified"
            )
            LOG.info(
                "source: %s · files inventoried: %s · goal: %s · directives: %s",
                relative_entry,
                len(inventory["files"]),
                goal_status,
                len(inventory["directives"]),
            )
        except Exception as exc:
            LOG.warning("source inventory disabled: %s", exc)
    (state / "model.md").touch(exist_ok=True)
    (state / "goals.md").touch(exist_ok=True)
    LOG.info(
        "watching: logs, stdout/stderr, numeric metrics, exceptions, stalls, "
        "aspiration gaps, regressions"
    )
    if config.api_key:
        source = _key_source(root)
        LOG.info(
            "cognition: checking OpenAI %s · credentials: %s", config.model, source
        )
    LOG.info("attach: samantha --pid %s", os.getpid())
    _start_runtime(_resident)
    return _resident


def _start_runtime(current: Resident) -> None:
    """Start cognition and local chat; failures cannot escape into the host."""
    try:
        from .cognition.harness import Cognition
        from .cognition.llm import OpenAILLM
        from .sentinels import Sentinels

        autonomous = current.config.mode == "act" and current.config.autonomy == "goal"
        # An unmet target keeps waking cognition, so a nudge that fell short (or a
        # turn that failed) gets another act turn after the dwell.
        dwell = float(getattr(current.config, "autonomy_dwell", DEFAULT_AUTONOMY_DWELL))
        current.sentinels = Sentinels(
            current.observer.metrics,
            rearm={"gap": dwell} if autonomous else None,
        )
        trigger = getattr(current.config, "autonomy_trigger", None)
        current.trigger = re.compile(trigger) if autonomous and trigger else None
        if current.trigger is not None:
            LOG.info(
                "miss lines: /%s/ · first move planned at startup · then one act "
                "turn per %g s while misses continue",
                trigger,
                float(getattr(current.config, "trigger_dwell", DEFAULT_TRIGGER_DWELL)),
            )
        if current.config.api_key and current.config.mode in {"advise", "act"}:
            current.cognition = Cognition(
                current,
                OpenAILLM(
                    current.config.api_key,
                    current.config.model,
                    current.config.base_url,
                    current.config.llm_timeout,
                ),
            )
            if current.config.mode == "act":
                from .act.controller import ActController

                current.actuator = ActController(current, current.cognition.llm)
            current.cognition.start()
        current.observer.on_observation = lambda source, text: _inspect(current, text)
    except Exception as exc:
        LOG.warning("cognition dormant: %s", exc)
    try:
        from .chat.server import ChatServer
        from .cli import runtime_dir

        current.chat = ChatServer(current, runtime_dir())
        current.chat.start()
    except Exception as exc:
        LOG.warning("chat unavailable: %s", exc)
    atexit.register(current.stop)


def _inspect(current: Resident, text: str) -> None:
    trigger = getattr(current, "trigger", None)
    if trigger is not None and trigger.search(text):
        # A miss line (SAMANTHA_AUTONOMY_TRIGGER). It goes past the wake queue,
        # so a wake's model call in progress does not hold it up. The act turn
        # runs on its own thread; this call returns at once.
        if cognition := getattr(current, "cognition", None):
            cognition.on_trigger(text)
    events = current.sentinels.inspect_line(text)
    events.extend(current.sentinels.inspect_metrics())
    for event in events:
        current.journal.write(
            "observation",
            source="sentinel",
            payload={"sentinel": event.sentinel, "evidence": event.evidence},
        )
        if _is_failure(event) and (actuator := getattr(current, "actuator", None)):
            actuator.runtime.on_failure(f"{event.sentinel}: {event.evidence}")
        if cognition := getattr(current, "cognition", None):
            # Sentinels run inside host print/logging calls; the model call must not.
            cognition.submit(event)


def _is_failure(event: Any) -> bool:
    """Sentinel events that end an until_failure intervention."""
    if event.sentinel == "nan-inf":
        return True
    line = (
        str(event.evidence.get("line", "")) if event.sentinel == "log-novelty" else ""
    )
    return bool(re.search(r"\b(ERROR|CRITICAL|Traceback|Exception)\b", line))


def resident() -> Resident | None:
    return _resident
