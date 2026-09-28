"""Multi-head trainer testbed that exercises Samantha's observation and act surfaces.

It prints metric lines (including a constant one that trips the stall sentinel),
can emit a WARNING on demand, keeps live state in a dict, a dataclass, and a worker
queue, and reports how long its own prints took so host-thread stalls are visible.
"""

import samantha  # noqa: F401 - arming the resident is the behavior under test

import argparse
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass

# daemon: goal — every head reaches its fidelity ceiling
# daemon: sampling_weights are fair game within 0.1–5.0


@dataclass
class Settings:
    lr: float = 0.03
    batch_size: int = 32
    phase_label: str = "warmup"


settings = Settings()
cfg = {"sampling_weights": {f"head_{i}": 1.0 for i in range(3)}}
cfg["sampling_weights"]["head_1"] = 0.25
ceilings = [0.94, 0.91, 0.95]
jobs: queue.Queue[str] = queue.Queue()
completed: list[str] = []
log = logging.getLogger("testbed")
slowest_print = 0.0


def emit(text: str) -> None:
    """Print through the (possibly tapped) stdout and track the slowest write."""
    global slowest_print
    started = time.monotonic()
    print(text, flush=True)
    slowest_print = max(slowest_print, time.monotonic() - started)


def worker() -> None:
    while True:
        job = jobs.get()
        completed.append(job)
        emit(f"job done: {job}")


def train_step(step: int, scores: list[float]) -> None:
    for i, ceiling in enumerate(ceilings):
        weight = cfg["sampling_weights"][f"head_{i}"]
        gain = settings.lr * 0.2 * min(weight, 1.0)
        target = ceiling - (0.12 if weight < 0.8 else 0.01)
        scores[i] += min(gain, max(0.0, target - scores[i]))
        emit(f"fidelity head_{i}: {scores[i]:.3f} (ceiling {ceiling:.3f})")
    loss = sum(ceilings[i] - scores[i] for i in range(len(ceilings)))
    emit(f"[{settings.phase_label}] step={step} loss={loss:.3f} gpu/util: 72%")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Samantha testbed trainer")
    result.add_argument("--steps", type=int, default=60, help="0 runs forever")
    result.add_argument("--interval", type=float, default=0.1, help="seconds/step")
    result.add_argument(
        "--warn-at", type=int, default=-1, help="emit a WARNING line at this step"
    )
    result.add_argument(
        "--log", action="store_true", help="also route metrics through logging"
    )
    result.add_argument(
        "--fake-llm", action="store_true", help="install a scripted fake model"
    )
    result.add_argument(
        "--fake-delay", type=float, default=2.0, help="fake model latency seconds"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.fake_llm:
        from .fake import install

        install(args.fake_delay)
    if args.log:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    threading.Thread(target=worker, name="testbed-worker", daemon=True).start()
    scores = [0.45] * len(ceilings)
    step = 0
    try:
        while args.steps <= 0 or step < args.steps:
            train_step(step, scores)
            if step == args.warn_at:
                emit(f"WARNING novel queue fault at step={step}")
            if args.log:
                log.info("heartbeat step=%s", step)
            step += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    sys.__stderr__.write(
        f"testbed: {step} steps · slowest print {slowest_print * 1000:.1f} ms\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
