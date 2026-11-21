#!/usr/bin/env python3
"""ICE test/experiment runner.

Boots the embedded Postgres (pgserver), provisions a FRESH database per run
(dropping stale ice_test_* leftovers), and runs the given command inside the
repo with DATABASE_URL pointing at it.

Usage:
    python3 /home/z/my-project/scripts/ice_test.py <pytest args...>
    python3 /home/z/my-project/scripts/ice_test.py --script path/script.py [args...]
"""
import asyncio
import os
import subprocess
import sys
import uuid

REPO = "/home/z/my-project/ice"
DATA_DIR = "/tmp/ice_pgdata"


async def make_dsn() -> str:
    import asyncpg

    # Prefer an already-running server named by DATABASE_URL (the
    # manually-started embedded instance): attaching to it avoids a
    # second pgserver handle process fighting over the same data dir.
    env_dsn = (os.getenv("DATABASE_URL") or "").strip()
    base_uri = None
    if env_dsn.startswith(("postgresql://", "postgres://")):
        try:
            probe = await asyncpg.connect(env_dsn)
            await probe.close()
            base_uri = env_dsn
        except Exception:
            base_uri = None
    if base_uri is None:
        import pgserver

        db = pgserver.get_server(DATA_DIR)
        base_uri = db.get_uri()
    dbname = f"ice_test_{uuid.uuid4().hex[:8]}"

    conn = await asyncpg.connect(base_uri)
    try:
        # Drop stale test databases from earlier runs (best effort).
        stale = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE 'ice_test_%'"
        )
        for row in stale:
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{row["datname"]}"')
            except Exception:
                pass
        await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()

    # Rebuild the URI with the new database name, PRESERVING the query
    # string (?host=/path/to/socket) — dropping it would make asyncpg try
    # a TCP localhost connection that nothing listens on.
    if "?" in base_uri:
        prefix, query = base_uri.split("?", 1)
        query = "?" + query
    else:
        prefix, query = base_uri, ""
    base = prefix.rsplit("/", 1)[0]
    return f"{base}/{dbname}{query}"


def main() -> int:
    dsn = asyncio.run(make_dsn())
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    if "--script" in sys.argv:
        idx = sys.argv.index("--script")
        script = sys.argv[idx + 1]
        args = sys.argv[idx + 2 :]
        cmd = [sys.executable, script] + args
    else:
        cmd = [sys.executable, "-m", "pytest"] + sys.argv[1:]
    return subprocess.call(cmd, cwd=REPO, env=env)


if __name__ == "__main__":
    sys.exit(main())
