import os as _os

# Unit tests never load the local embedding model through the SEARCH router
# (case_engine.router); tests that exercise it opt in explicitly.
_os.environ.setdefault("ICE_SEMANTIC_ROUTER", "0")
_os.environ.setdefault("ICE_FINGERPRINT_EMBED", "0")
_os.environ.setdefault("ICE_WARM_EMBEDDER", "0")
"""tests/conftest.py — test-suite-local fixtures and import shims.

Why this file exists (documented deviation from the doc §5.1 tree, which
lists only the 20 test files + __init__ files):

    ``case_engine`` modules import two OPTIONAL runtime dependencies at
    module scope:

      * ``litellm``        — via case_engine/llm.py and engine/generate.py
      * ``google.genai``   — via engine/memory.py (the embedding substrate
                             pulled in by case_engine.evidence and friends)

    Neither is installed in every environment that must be able to
    *collect* the test suite (e.g. a lint/collect-only sandbox, or a CI
    unit job that installs only the test extras). When they are absent,
    merely importing ``case_engine.contradiction`` (or any module that
    transitively touches ``engine.memory``) raises ModuleNotFoundError,
    which would turn every unit test into a collection ERROR even though
    the unit tests never call an LLM.

    Fix: install minimal stub modules via ``sys.modules.setdefault`` BEFORE
    any test module imports case_engine. ``setdefault`` guarantees the REAL
    package wins whenever it is actually installed (the docker image, the
    eval CI job), so behavior in fully-provisioned environments is
    unchanged. The stubs raise if ever actually called — no test silently
    passes against a fake LLM.

    The root ``conftest.py`` (harness, spec Part 4 §18.1) already provides
    ``postgres_container`` / ``db_pool`` / ``case_id``; this file adds
    nothing DB-related on purpose.
"""

import sys
import types
from pathlib import Path

_ICE_ROOT = str(Path(__file__).resolve().parent.parent)
if _ICE_ROOT not in sys.path:
    sys.path.insert(0, _ICE_ROOT)


def _install_optional_dependency_stubs() -> None:
    """Stub litellm + google.genai when absent so collection never breaks."""
    # --- litellm -----------------------------------------------------------
    # case_engine/llm.py does:  import litellm; from litellm import acompletion
    if "litellm" not in sys.modules:
        try:  # pragma: no cover - real install path
            import litellm  # noqa: F401
        except ImportError:
            litellm_stub = types.ModuleType("litellm")

            async def _stub_acompletion(*_args, **_kwargs):  # pragma: no cover
                raise RuntimeError(
                    "litellm is stubbed in this environment — real LLM "
                    "calls are impossible; this stub should never be called"
                )

            litellm_stub.acompletion = _stub_acompletion
            sys.modules["litellm"] = litellm_stub

    # --- google.genai (engine/memory.py: from google import genai;
    #     from google.genai import types) -----------------------------------
    if "google.genai" not in sys.modules:
        try:  # pragma: no cover - real install path
            import google.genai  # noqa: F401
        except ImportError:
            google_stub = types.ModuleType("google")
            genai_stub = types.ModuleType("google.genai")
            types_stub = types.ModuleType("google.genai.types")

            class _StubClient:  # pragma: no cover
                def __init__(self, *args, **kwargs):
                    raise RuntimeError(
                        "google.genai is stubbed in this environment — real "
                        "embeddings are impossible"
                    )

            genai_stub.Client = _StubClient
            genai_stub.types = types_stub
            google_stub.genai = genai_stub
            sys.modules["google"] = google_stub
            sys.modules["google.genai"] = genai_stub
            sys.modules["google.genai.types"] = types_stub


_install_optional_dependency_stubs()