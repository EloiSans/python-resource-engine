"""Prepare the dev Garage node (idempotent): layout, bucket and a fixed dev key.

    docker compose -f compose.emulators.yaml up -d garage
    uv run python docker/garage/init.py

The key and bucket match the defaults of the S3 tests (adapters/resource-engine-s3).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

CONTAINER = os.getenv("GARAGE_CONTAINER", "resource-engine-emulators-garage-1")
BUCKET = "resource-engine"
KEY_ID = "GK5e6f1a2b3c4d5e6f708192a3"
KEY_SECRET = "5e6f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7"


def garage(*args: str, check: bool = True) -> str | None:
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "/garage", *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        if check:
            raise RuntimeError(f"garage {' '.join(args)} failed: {result.stderr.strip()}")
        return None
    return result.stdout


def main() -> int:
    status = None
    for _ in range(30):
        status = garage("status", check=False)
        if status:
            break
        time.sleep(1)
    if not status:
        print("Garage did not start (docker compose logs garage)", file=sys.stderr)
        return 1
    if not re.search(r"\bdc1\b", status):
        node = re.search(r"^([0-9a-f]{16})\s", status, re.M)
        if not node:
            raise RuntimeError(f"Could not read the node id:\n{status}")
        garage("layout", "assign", "-z", "dc1", "-c", "1G", node.group(1))
        garage("layout", "apply", "--version", "1")
    if not garage("bucket", "info", BUCKET, check=False):
        garage("bucket", "create", BUCKET)
    if not garage("key", "info", KEY_ID, check=False):
        garage("key", "import", "--yes", "-n", "resource-engine-dev", KEY_ID, KEY_SECRET)
    garage("bucket", "allow", "--read", "--write", "--owner", BUCKET, "--key", KEY_ID)
    print(f"Garage ready: http://127.0.0.1:3910 bucket {BUCKET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
