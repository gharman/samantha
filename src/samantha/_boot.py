"""Safe startup and configuration resolution."""

from __future__ import annotations

import atexit
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .identity import discover_project
from .state import Journal, state_root

LOG = logging.getLogger("samantha")
_resident: Resident | None = None


@dataclass
class Config:
    mode: str = "advise"
    goal: str | None = None
    model: str = "gpt-5.6-terra"
    base_url: str = "https://api.openai.com"
    api_key: str | None = None
    state_dir: str | None = None
    max_wakes_per_hour: int = 6
    token_budget_per_day: int = 2_000_000
    flag_budget: int = 3
    dotenv_file: str | None = None

    @classmethod
    def resolve(cls, **overrides: object) -> "Config":
        dotenv_start = overrides.pop("_dotenv_start", None)
        dotenv_file = _load_dotenv(dotenv_start)
        values = dict(
            mode=os.getenv("SAMANTHA", "advise"),
            goal=os.getenv("SAMANTHA_GOAL"),
            model=os.getenv("SAMANTHA_MODEL", "gpt-5.6-terra"),
            base_url=os.getenv("SAMANTHA_BASE_URL", "https://api.openai.com"),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            state_dir=os.getenv("SAMANTHA_STATE_DIR"),
            max_wakes_per_hour=int(os.getenv("SAMANTHA_MAX_WAKES_PER_HOUR", "6")),
            token_budget_per_day=int(
                os.getenv("SAMANTHA_TOKEN_BUDGET_PER_DAY", "2000000")
            ),
            flag_budget=int(os.getenv("SAMANTHA_FLAG_BUDGET", "3")),
            dotenv_file=str(dotenv_file) if dotenv_file else None,
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        if values["mode"] not in {"off", "observe", "advise", "act"}:
            values["mode"] = "advise"
        return cls(**values)


def _load_dotenv(start: Path | None = None) -> Path | None:
    """Load the nearest .env without adding a runtime dependency."""
    origin = (start or Path.cwd()).resolve()
    for directory in (origin, *origin.parents):
        path = directory / ".env"
        if not path.is_file():
            continue
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        return path
    return None


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


def _logging() -> None:
    if not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[samantha] %(message)s"))
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def arm(
    entry: str | Path | None = None, *, force: bool = False, **overrides: object
) -> Resident | None:
    global _resident
    root, key = discover_project(entry)
    config = Config.resolve(_dotenv_start=root, **overrides)
    if config.mode == "off":
        return None
    if _resident:
        return _resident
    _logging()
    state = state_root(key, config.state_dir)
    journal = Journal(state)
    _resident = Resident(config, root, key, journal, state)
    from .claims import ClaimStore
    from .baselines import Baselines

    _resident.claims = ClaimStore(state / "claims.json")
    demoted = _resident.claims.validate()
    _resident.baselines = Baselines(state / "baselines.json")
    from .act.ledger import Ledger

    _resident.ledger = Ledger(state / "ledger.json")
    for claim in demoted:
        journal.write(
            "demotion",
            knob=claim,
            **{"from": "fact", "to": "hypothesis"},
            cause="fingerprint changed",
        )
    if not config.api_key and config.mode in {"advise", "act"} and not force:
        LOG.info("no API key; cognition dormant")
    LOG.info(
        "resident armed · mode=%s · pid=%s · project=%s",
        config.mode,
        os.getpid(),
        key,
    )
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
            goal_status = config.goal or "not specified"
            LOG.info(
                "source: %s · goal: %s · directives: %s",
                relative_entry,
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
        source = config.dotenv_file or "process environment"
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

        current.sentinels = Sentinels(current.observer.metrics)
        if current.config.api_key and current.config.mode in {"advise", "act"}:
            current.cognition = Cognition(
                current,
                OpenAILLM(
                    current.config.api_key,
                    current.config.model,
                    current.config.base_url,
                ),
            )
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
    events = current.sentinels.inspect_line(text)
    events.extend(current.sentinels.inspect_metrics())
    for event in events:
        current.journal.write(
            "observation",
            source="sentinel",
            payload={"sentinel": event.sentinel, "evidence": event.evidence},
        )
        if cognition := getattr(current, "cognition", None):
            cognition.wake(event)


def resident() -> Resident | None:
    return _resident
