"""Send scripted chat messages to a running resident without the interactive CLI.

python ask.py "what is this program?" "and the loss?"
python ask.py --hangup "slow question"   # disconnect before the reply arrives
"""

import os

os.environ["SAMANTHA"] = "off"  # this client must not arm a resident of its own

import argparse  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402

from samantha.cli import residents, runtime_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("messages", nargs="+")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--hangup", action="store_true", help="close after sending")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.pid:
        path = runtime_dir() / f"{args.pid}.sock"
    else:
        found = residents()
        if len(found) != 1:
            print(f"expected one resident, found {[pid for pid, _ in found]}")
            return 2
        path = found[0][1]
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(args.timeout)
        client.connect(str(path))
        incoming = client.makefile("rb")
        hello = json.loads(incoming.readline())
        print(f"hello: pid={hello.get('pid')} mode={hello.get('mode')}")
        for text in args.messages:
            client.sendall(
                (json.dumps({"type": "command", "text": text}) + "\n").encode()
            )
            if args.hangup:
                print(f"> {text}\n(hung up before reply)")
                return 0
            raw = incoming.readline()
            if not raw:
                print(f"> {text}\n(connection closed without a reply)")
                return 1
            reply = json.loads(raw)
            print(f"> {text}\n{reply.get('reply') or reply.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
