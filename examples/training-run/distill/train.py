"""distill: teach a small student network to imitate a teacher function.

There is no dataset. A generator makes synthetic training points on the fly
from three regions of the feature space: common, edge, and rare. It fills each
batch region by region; a region's share is how often it occurs naturally times
its sampling weight, so sampling_weights is how we bias generation to a corner.

Training runs in rounds. Each round takes a fixed number of optimizer steps,
then measures loss and fidelity (how closely the student tracks the teacher;
1.0 is exact) per region on fixed probe sets. Each round prints those numbers
and redraws out/curves.png. The generator reads sampling_weights again at the
start of every round, so a change takes effect at the next round.
"""

import samantha

# daemon: goal — every region reaches fidelity 0.95: common, edge, and rare
# daemon: sampling_weights are fair game within 0.1–5.0
# daemon: never touch the seed, the teacher, or the probe sets

import argparse
import json
import os
import signal
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

REGIONS = ("common", "edge", "rare")
# How often the world produces each region when generation is not biased.
NATURAL_SHARE = {"common": 0.70, "edge": 0.29, "rare": 0.01}
TARGET = 0.95  # fidelity target for every region
TOLERANCE = 0.2  # a probe point with this error counts as 0.61 right
RARE_CENTER = (0.62, 0.62)
RARE_RADIUS = 0.3
BUMP_HEIGHT = 1.0


@dataclass
class SamplingWeights:
    """Multipliers on NATURAL_SHARE. 1.0 everywhere means unbiased sampling."""

    common: float = 1.0
    edge: float = 1.0
    rare: float = 1.0


sampling_weights = SamplingWeights()


def teacher(x: np.ndarray) -> np.ndarray:
    """The function the student must learn: a smooth surface, plus a bump in the rare corner."""
    x0, x1 = x[:, 0], x[:, 1]
    smooth = np.sin(2.0 * x0 + 0.4) * np.cos(1.6 * x1) + 0.35 * x0 * x1
    u = np.clip(np.hypot(x0 - RARE_CENTER[0], x1 - RARE_CENTER[1]) / RARE_RADIUS, 0.0, 1.0)
    return smooth + BUMP_HEIGHT * np.cos(0.5 * np.pi * u) ** 2


def region_of(x: np.ndarray) -> np.ndarray:
    r = np.hypot(x[:, 0] - RARE_CENTER[0], x[:, 1] - RARE_CENTER[1])
    edge = np.abs(x).max(axis=1) > 0.75
    return np.where(r < RARE_RADIUS, "rare", np.where(edge, "edge", "common"))


class Generator:
    """Synthetic training points, drawn region by region."""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng

    def points(self, region: str, n: int) -> np.ndarray:
        found = np.empty((0, 2))
        while len(found) < n:
            candidates = self.rng.uniform(-1.0, 1.0, size=(max(64, 16 * n), 2))
            found = np.vstack([found, candidates[region_of(candidates) == region]])
        return found[:n]

    def quotas(self, n: int, weights: dict[str, float]) -> dict[str, int]:
        """Points per region in a batch of n: each region's share, rounded down;
        common takes the remainder."""
        share = {r: NATURAL_SHARE[r] * weights[r] for r in REGIONS}
        total = sum(share.values())
        counts = {r: int(n * share[r] / total) for r in REGIONS}
        counts["common"] += n - sum(counts.values())
        return counts

    def batch(self, n: int, weights: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
        counts = self.quotas(n, weights)
        x = np.vstack([self.points(r, c) for r, c in counts.items() if c])
        return x, teacher(x)


class Student:
    """A small tanh MLP trained with AdamW on mean squared error."""

    def __init__(
        self,
        rng: np.random.Generator,
        hidden: tuple[int, ...] = (32, 32),
        lr: float = 3e-3,
        weight_decay: float = 0.0,
    ) -> None:
        sizes = (2, *hidden, 1)
        self.weights = [
            rng.normal(0.0, 1.0 / np.sqrt(a), (a, b)) for a, b in zip(sizes, sizes[1:])
        ]
        self.biases = [np.zeros(b) for b in sizes[1:]]
        self.lr, self.weight_decay, self.t = lr, weight_decay, 0
        params = self.weights + self.biases
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]

    def _forward(self, x: np.ndarray) -> list[np.ndarray]:
        acts = [x]
        for w, b in zip(self.weights[:-1], self.biases[:-1]):
            acts.append(np.tanh(acts[-1] @ w + b))
        return acts

    def predict(self, x: np.ndarray) -> np.ndarray:
        return (self._forward(x)[-1] @ self.weights[-1] + self.biases[-1])[:, 0]

    def step(self, x: np.ndarray, y: np.ndarray) -> None:
        acts = self._forward(x)
        out = (acts[-1] @ self.weights[-1] + self.biases[-1])[:, 0]
        grad = (2.0 / len(y)) * (out - y)[:, None]
        grads_w, grads_b = [], []
        for i in reversed(range(len(self.weights))):
            grads_w.insert(0, acts[i].T @ grad)
            grads_b.insert(0, grad.sum(axis=0))
            if i:
                grad = (grad @ self.weights[i].T) * (1.0 - acts[i] ** 2)
        self.t += 1
        params = self.weights + self.biases
        for k, (p, g) in enumerate(zip(params, grads_w + grads_b)):
            self.m[k] = 0.9 * self.m[k] + 0.1 * g
            self.v[k] = 0.999 * self.v[k] + 0.001 * g * g
            m_hat = self.m[k] / (1 - 0.9**self.t)
            v_hat = self.v[k] / (1 - 0.999**self.t)
            p -= self.lr * (m_hat / (np.sqrt(v_hat) + 1e-8) + self.weight_decay * p)


def make_probes(seed: int, n: int = 1000) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Fixed evaluation points per region, never used for training."""
    gen = Generator(np.random.default_rng(seed + 1_000_003))
    probes = {}
    for region in REGIONS:
        x = gen.points(region, n)
        probes[region] = (x, teacher(x))
    return probes


def evaluate(student: Student, probes: dict) -> dict[str, dict[str, float]]:
    result = {}
    for region, (x, y) in probes.items():
        err = student.predict(x) - y
        result[region] = {
            "loss": float(np.mean(err**2)),
            "fidelity": float(np.mean(np.exp(-0.5 * (err / TOLERANCE) ** 2))),
        }
    return result


def plot(history: dict, path: Path) -> None:
    """Redraw the per-region curves; replace the PNG in one step."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    colors = {"common": "#4C72B0", "edge": "#55A868", "rare": "#C44E52"}
    rounds = history["round"]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(12.8, 7.2), sharex=True)
    for region in REGIONS:
        top.plot(rounds, history["loss_by_region"][region], color=colors[region], lw=2.2)
        bottom.plot(rounds, history["fidelity"][region], color=colors[region], lw=2.2)
    top.plot(rounds, history["loss"], color="black", ls="--", lw=1.6)
    top.set_yscale("log")
    top.set_ylabel("loss per region\n(MSE, log scale)")
    bottom.axhline(TARGET, color="gray", ls=":", lw=1.5)
    bottom.set_ylim(0.0, 1.02)
    bottom.set_ylabel("fidelity per region\n(1.0 = exact)")
    bottom.set_xlabel("round")
    handles = [Line2D([], [], color=colors[r], lw=2.2, label=r) for r in REGIONS] + [
        Line2D([], [], color="black", ls="--", lw=1.6,
               label="total loss (the number you usually watch)"),
        Line2D([], [], color="gray", ls=":", lw=1.5, label=f"fidelity target {TARGET}"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=5, frameon=False, fontsize=11,
               bbox_to_anchor=(0.5, 0.945))
    for change in history["changes"]:
        for ax in (top, bottom):
            ax.axvline(change["round"], color="#8172B2", lw=1.8)
        moved = [f"{r} {change['from'][r]:g} → {change['to'][r]:g}" for r in REGIONS
                 if change["from"][r] != change["to"][r]]
        label = "sampling_weights " + ", ".join(moved)
        label += " (rehearsal)" if change.get("rehearsal") else " (live, no restart)"
        top.annotate(label, xy=(change["round"], 1.0), xycoords=("data", "axes fraction"),
                     xytext=(6, -16), textcoords="offset points", color="#8172B2",
                     fontsize=11, fontweight="bold")
    now = history["weights"][-1]
    fig.suptitle(
        f"distill · round {rounds[-1]} · sampling_weights: "
        + ", ".join(f"{r}={now[r]:g}" for r in REGIONS),
        fontsize=14, y=0.985,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.935))
    tmp = path.with_suffix(".tmp.png")
    fig.savefig(tmp, dpi=150)
    plt.close(fig)
    os.replace(tmp, path)


def save_json(value: dict, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


def train(args: argparse.Namespace) -> dict:
    rng = np.random.default_rng(args.seed)
    generator = Generator(rng)
    student = Student(rng, hidden=tuple(args.hidden), lr=args.lr,
                      weight_decay=args.weight_decay)
    probes = make_probes(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    history: dict = {
        "seed": args.seed, "round": [], "loss": [], "weights": [], "changes": [],
        "loss_by_region": {r: [] for r in REGIONS},
        "fidelity": {r: [] for r in REGIONS},
    }
    rehearsal = None
    if args.rehearse_change:
        at, value = args.rehearse_change.split(":")
        rehearsal = (int(at), float(value))
    seen = asdict(sampling_weights)
    round_number = 0
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        # Ctrl-C stops at the next round boundary; a second Ctrl-C stops now.
        # (A KeyboardInterrupt raised inside a garbage-collector callback is
        # printed and dropped, so one Ctrl-C was not always enough.)
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, request_stop)
    while (not args.rounds or round_number < args.rounds) and not stop.is_set():
        started = time.monotonic()
        if rehearsal and round_number == rehearsal[0]:
            sampling_weights.rare = rehearsal[1]
        weights = asdict(sampling_weights)  # read live, once per round
        if weights != seen:
            for region in REGIONS:
                if weights[region] != seen[region]:
                    print(f"round {round_number:03d}  sampling_weights.{region} changed "
                          f"live: {seen[region]:g} → {weights[region]:g} (no restart)",
                          flush=True)
            history["changes"].append({
                "round": round_number, "from": seen, "to": weights,
                "rehearsal": bool(rehearsal and round_number == rehearsal[0]),
            })
            seen = weights
        for _ in range(args.steps):
            x, y = generator.batch(args.batch, weights)
            student.step(x, y)
        scores = evaluate(student, probes)
        total = sum(NATURAL_SHARE[r] * scores[r]["loss"] for r in REGIONS)
        history["round"].append(round_number)
        history["loss"].append(total)
        history["weights"].append(weights)
        for region in REGIONS:
            history["loss_by_region"][region].append(scores[region]["loss"])
            history["fidelity"][region].append(scores[region]["fidelity"])
        if not args.quiet:
            losses = "  ".join(f"loss/{r}: {scores[r]['loss']:.5f}" for r in REGIONS)
            fidelity = "  ".join(f"fidelity/{r}: {scores[r]['fidelity']:.4f}" for r in REGIONS)
            print(f"round {round_number:03d}  loss: {total:.5f}  {losses}", flush=True)
            print(f"round {round_number:03d}  {fidelity}  (target {TARGET})", flush=True)
        if not args.no_plot:
            plot(history, out / "curves.png")
        save_json(history, out / "history.json")
        round_number += 1
        stop.wait(max(0.0, args.round_seconds - (time.monotonic() - started)))
    return history


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Distill a teacher function into a small student.")
    p.add_argument("--seed", type=int, default=11, help="random seed for data, model, probes")
    p.add_argument("--rounds", type=int, default=300, help="rounds to run; 0 runs until Ctrl-C")
    p.add_argument("--steps", type=int, default=200, help="optimizer steps per round")
    p.add_argument("--batch", type=int, default=96, help="training points per step")
    p.add_argument("--hidden", type=int, nargs="+", default=[32, 32], help="hidden layer sizes")
    p.add_argument("--lr", type=float, default=6e-3, help="AdamW learning rate")
    p.add_argument("--weight-decay", type=float, default=0.0, help="AdamW weight decay")
    p.add_argument("--round-seconds", type=float, default=1.0,
                   help="minimum wall time per round, so a person can follow along")
    p.add_argument("--out", default="out", help="directory for curves.png and history.json")
    p.add_argument("--no-plot", action="store_true", help="do not draw curves.png")
    p.add_argument("--quiet", action="store_true", help="do not print per-round metrics")
    p.add_argument("--rehearse-change", metavar="ROUND:VALUE",
                   help="test only: set sampling_weights.rare to VALUE at ROUND by itself")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    for region in REGIONS:
        samantha.expose(f"sampling_weights.{region}", obj=sampling_weights, attr=region,
                        bounds=(0.1, 5.0))
    # Sentinel wakes see the operator goal but not the '# daemon:' comments
    # above, so state the goal and the lever here too.
    samantha.goal("every region (common, edge, rare) reaches fidelity 0.95; "
                  "sampling_weights.<region> are fair game within 0.1–5.0")
    try:
        history = train(args)
    except KeyboardInterrupt:
        return 0
    if history["round"]:
        last = {r: history["fidelity"][r][-1] for r in REGIONS}
        print(f"done after round {history['round'][-1]:03d}: "
              + "  ".join(f"fidelity/{r}: {v:.4f}" for r, v in last.items()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
