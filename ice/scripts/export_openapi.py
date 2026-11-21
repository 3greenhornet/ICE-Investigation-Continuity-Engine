#!/usr/bin/env python3
"""scripts/export_openapi.py — generate docs/openapi.json from the FastAPI app.

Improvisation F (spec audit tail): a small, dependency-free way to keep a
published OpenAPI contract in the repo. Run after changing app/ routes or
schemas:

    python scripts/export_openapi.py [--out docs/openapi.json]

The app module imports cleanly without a database or LLM keys (routes only
touch the pool per-request), so this works in CI or offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
    parser.add_argument("--out", type=Path, default=default_out)
    args = parser.parse_args()

    spec = app.openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    paths = len(spec.get("paths", {}))
    print(f"wrote {args.out} ({paths} paths, openapi {spec.get('openapi')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())