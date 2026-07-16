"""Small deterministic leak and novel-error acceptance workload."""
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--items", type=int, default=2000)
args = parser.parse_args()
leak = []
for item in range(args.items):
    leak.append(bytearray(1024))
    if item == args.items // 2:
        print(f"WARNING novel queue fault at item={item}")
print(f"processed={args.items}")

