"""A foreign-project-shaped import smoke test."""

import os
import time

import samantha  # noqa: F401 - import is the behavior under test

print(os.getpid(), flush=True)
time.sleep(30)
