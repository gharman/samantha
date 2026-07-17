"""Deterministic multi-head trainer used as samantha's acceptance rig."""

import argparse
import random

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=7)
parser.add_argument("--steps", type=int, default=240)
args = parser.parse_args()
random.seed(args.seed)

cfg = {"sampling_weights": {f"head_{i}": 1.0 for i in range(4)}, "lr": 0.03}
cfg["sampling_weights"]["head_2"] = 0.25
ceilings = [0.94, 0.91, 0.92, 0.95]
scores = [0.45] * 4

for step in range(args.steps):
    for i, ceiling in enumerate(ceilings):
        weight = cfg["sampling_weights"][f"head_{i}"]
        effective = weight if i == 2 else 1.0
        target = ceiling - (0.12 if i == 2 and effective < 0.8 else 0.01)
        scores[i] += min(0.006 * effective, max(0.0, target - scores[i]))
        print(f"fidelity head_{i}: {scores[i]:.3f} (ceiling {ceiling:.3f})")
    print(f"loss={sum(ceilings[i] - scores[i] for i in range(4)):.3f} gpu/util: 72%")
