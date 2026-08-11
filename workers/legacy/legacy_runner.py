# -*- coding: utf-8 -*-
from __future__ import print_function

"""One-request fallback runner.

Rust may invoke this only when a persistent-worker handshake fails before any
write begins. It intentionally shares the same whitelist and JSONL contract.
"""

import argparse
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--worker", required=True)
    args = parser.parse_args()
    process = subprocess.Popen(
        [args.python, "-u", os.path.abspath(args.worker)],
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
    )
    return process.wait()


if __name__ == "__main__":
    sys.exit(main())
