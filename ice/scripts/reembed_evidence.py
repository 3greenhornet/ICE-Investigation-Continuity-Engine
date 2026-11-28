"""Recompute ICE evidence embeddings with the configured EMBEDDING_BACKEND.

Run after switching backend (local <-> gemini) or to fill missing vectors:

    python scripts/reembed_evidence.py              # every row
    python scripts/reembed_evidence.py --missing    # only rows without a vector
    python scripts/reembed_evidence.py --case case_0123456789ab

Needs DATABASE_URL. Writes only evidence.embedding (a derived index).
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envfiles import load_env_files  # noqa: E402

load_env_files()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--missing", action="store_true")
    args = ap.parse_args()
    from case_engine.evidence import reembed_evidence
    from db.connection import close_db, init_db

    await init_db()
    try:
        print(json.dumps(await reembed_evidence(args.case, args.missing), indent=1))
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
