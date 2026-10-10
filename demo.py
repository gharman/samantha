"""Run a Samantha demo from the repository root.

    python demo.py aliens      pygame's alien shooter: Samantha helps you win
    python demo.py training    a small training run: Samantha fixes a starved region

Samantha runs in act mode and acts by herself when a goal stays unmet
(SAMANTHA_AUTONOMY=goal; another try every 90 s while it stays unmet). In the
aliens demo, a hit is a miss against the goal (SAMANTHA_AUTONOMY_TRIGGER): she
plans her first move before you press P, applies it at your first hit, and
makes another change at most every 10 s while the hits continue.
Add --wait to make her wait for you: then talk to her with `samantha` in a
second terminal. The OpenAI key comes from .env in this directory or from the
environment (the environment wins). See README.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMOS = {
    "aliens": (["examples/aliens/play.py"], ["pygame"]),
    "training": (["examples/training-run/distill/train.py", "--window",
                  "--out", "examples/training-run/out"], ["numpy", "matplotlib"]),
}
INSTALL = 'python -m pip install -e ".[demos]"'
# Settings for one demo, when Samantha acts by herself (not with --wait). The
# environment wins. Aliens: the game prints "player hit · deaths: N · score: S"
# at each hit; that line is a miss against the goal, so she acts at the first
# hit, not after the hit rate has been over the goal for a minute.
AUTONOMY = {
    "aliens": {"SAMANTHA_AUTONOMY_TRIGGER": r"^player hit\b",
               "SAMANTHA_AUTONOMY_TRIGGER_DWELL": "10"},
}


def settings(demo: str, wait: bool = False) -> dict[str, str]:
    """The Samantha settings of a demo (the story tests use them too)."""
    values = {"SAMANTHA": "act", "SAMANTHA_AUTONOMY": "off" if wait else "goal",
              # A change that falls short gets another try within the demo (default 600 s).
              "SAMANTHA_AUTONOMY_DWELL": "90"}
    if not wait:
        values.update(AUTONOMY.get(demo, {}))
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python demo.py", description="Run a Samantha demo.",
        epilog="The key comes from .env in this directory, or from the environment.")
    parser.add_argument("demo", choices=sorted(DEMOS), help="the demo to run")
    parser.add_argument("--wait", action="store_true",
                        help="Samantha changes nothing by herself; talk to her with "
                             "`samantha` in a second terminal")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        print(f"Samantha needs Python 3.12 or newer; this is {sys.version.split()[0]}.")
        return 2
    script, modules = DEMOS[args.demo]
    missing = [m for m in ["samantha", *modules] if importlib.util.find_spec(m) is None]
    if missing:
        print(f"Missing: {', '.join(missing)}. In this directory, with the venv active, run:")
        print(f"  {INSTALL}")
        return 2
    os.chdir(ROOT)
    # Read the settings the way Samantha does, without starting her in this process.
    child = dict(os.environ)
    os.environ["SAMANTHA"] = "off"
    from samantha._boot import Config

    if not Config.resolve(_dotenv_start=ROOT).api_key:
        print("No OpenAI API key. Put it in .env in this directory:")
        print("  cp .env.example .env      # then edit .env: OPENAI_API_KEY=sk-...")
        print("or set it for this shell:  export OPENAI_API_KEY=sk-...")
        return 2
    for key, value in settings(args.demo, args.wait).items():
        if key in ("SAMANTHA", "SAMANTHA_AUTONOMY"):
            child[key] = value
        else:
            child.setdefault(key, value)
    child["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    command = [sys.executable, *script]
    os.execve(sys.executable, command, child)
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
