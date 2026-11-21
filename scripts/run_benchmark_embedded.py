#!/usr/bin/env python3
"""Run the offline benchmark with a fresh embedded postgres, in one process."""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, "/home/z/my-project/scripts")
from ice_test import make_dsn  # noqa: E402


def main() -> int:
    dsn = asyncio.run(make_dsn())
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    cmd = [sys.executable, "scripts/run_benchmark_offline.py", "--legacy",
           "--execute-actions"]
    return subprocess.call(cmd, cwd="/home/z/my-project/ice", env=env)


if __name__ == "__main__":
    sys.exit(main())
