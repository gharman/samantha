"""Safe startup and configuration resolution."""
from __future__ import annotations

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
    model: str = "claude-sonnet-4-6"
    base_url: str = "https://api.anthropic.com"
    api_key: str | None = None
    state_dir: str | None = None
    max_wakes_per_hour: int = 6
    token_budget_per_day: int = 2_000_000
    flag_budget: int = 3

    @classmethod
    def resolve(cls, **overrides: object) -> "Config":
        values = dict(
            mode=os.getenv("SAMANTHA", "advise"), goal=os.getenv("SAMANTHA_GOAL"),
            model=os.getenv("SAMANTHA_MODEL", "claude-sonnet-4-6"),
            base_url=os.getenv("SAMANTHA_BASE_URL", "https://api.anthropic.com"),
            api_key=os.getenv("ANTHROPIC_API_KEY"), state_dir=os.getenv("SAMANTHA_STATE_DIR"),
            max_wakes_per_hour=int(os.getenv("SAMANTHA_MAX_WAKES_PER_HOUR", "6")),
            token_budget_per_day=int(os.getenv("SAMANTHA_TOKEN_BUDGET_PER_DAY", "2000000")),
            flag_budget=int(os.getenv("SAMANTHA_FLAG_BUDGET", "3")),
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        if values["mode"] not in {"off", "observe", "advise", "act"}:
            values["mode"] = "advise"
        return cls(**values)


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


def arm(entry: str | Path | None = None, *, force: bool = False, **overrides: object) -> Resident | None:
    global _resident
    config = Config.resolve(**overrides)
    if config.mode == "off":
        return None
    if _resident:
        return _resident
    _logging()
    root, key = discover_project(entry)
    state = state_root(key, config.state_dir)
    journal = Journal(state)
    _resident = Resident(config, root, key, journal, state)
    if not config.api_key and config.mode in {"advise", "act"} and not force:
        LOG.info("no API key; cognition dormant")
    LOG.info("resident armed (%s) · project %s…", config.mode, key[:4])
    journal.write("observation", source="boot", payload={"mode": config.mode, "project": key})
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
            theory = config.goal or next((v["docstring"] for v in inventory["files"].values() if v["docstring"]), "Python program")
            (state / "model.md").write_text(f"# Program theory\n\n{theory}\n")
            (state / "goals.md").touch(exist_ok=True)
            LOG.info("theory: %s", theory.splitlines()[0][:160])
        except Exception as exc:
            LOG.warning("source inventory disabled: %s", exc)
    return _resident


def resident() -> Resident | None:
    return _resident
