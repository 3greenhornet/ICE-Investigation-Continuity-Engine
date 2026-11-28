"""envfiles.py — load KEY=VALUE env files before ICE reads its configuration.

ICE's API and MCP server read configuration from the process environment
only (the CLI used python-dotenv). This loader closes that gap without adding
a dependency:

  * ``ice/.env`` next to this file, if present;
  * every path in ``ICE_ENV_FILES`` (``;`` or ``,`` separated), e.g. a sibling
    project's ``.env`` so ICE can use the same LLM provider keys.

Existing environment variables always win (nothing is overridden), values are
never logged, and missing files are ignored. Must run before ``case_engine.llm``
is imported, because the active model chain is computed at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED: list[str] = []


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key.replace("_", "").isalnum():
            out[key] = value
    return out


def load_env_files() -> list[str]:
    """Load the configured env files once; returns the files that were read."""
    if _LOADED:
        return list(_LOADED)
    candidates = [Path(__file__).resolve().parent / ".env"]
    extra = os.getenv("ICE_ENV_FILES", "")
    for part in extra.replace(",", ";").split(";"):
        if part.strip():
            candidates.append(Path(part.strip()).expanduser())
    for path in candidates:
        try:
            if path.is_file():
                for k, v in _parse(path).items():
                    if v and not os.environ.get(k):
                        os.environ[k] = v
                _LOADED.append(str(path))
        except OSError:
            continue
    return list(_LOADED)
