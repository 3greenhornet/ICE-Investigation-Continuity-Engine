#!/usr/bin/env python3
"""fix-4 runtime attribute checks — ICE fixes doc Part 4 §2.3, sandbox form.

The doc runs these checks inside the api container. This sandbox has no
container, so the same checks run against a stubbed import environment
(pattern: .scratch/final_import_sweep.py / fix2b_verify.py). What is
checked is the ENGINE CODE ITSELF — classes, functions, signatures and
source text — none of which depends on the stubs (the stubs only stand
in for third-party drivers at import time).

Covers (doc §2.3 plus the Part 2 dispatch/surface extensions):
  1. EventLogger has the 5 case-mode methods (R1).
  2. next_best_action has compute_priority / compute_priority_with_factors /
     select_next_best_action (R4) and the factors source computes a
     debt_bonus (Part 2 §8).
  3. action.py has edit_action + ActionExecutor + the 3 real executors +
     default_executor_for (R4/R5, Part 2 §4).
  4. find_contradicting_claims prefilter markers (_CANDIDATE_MIN_OVERLAP, R3).
  5. recompute_hypothesis_confidence: direct_ev_rows query + relation branch
     (Part 2 §1 P2).
  6. decide is a case_type dispatcher (_decide_research/_decide_explore).
  7. generate_actions dispatches (_generate_actions_research).
  8. run_investigation_cycle signature carries enable_auto_execute (Part 2 §4.4).
  9. case_engine.investigation shim re-exports the SAME function object.
 10. evidence.py: DEFAULT_QUALITY_SCORES (P1) + ingest_external_analysis (2A).
 11. endogenous.py + nli.py import with their entry points (2D/2E, §10).
 12. schemas: Case.case_type / Hypothesis.origin / Evidence.source_llm (015).
 13. nli.classify_claim_against_hypothesis returns None (no transformers
     here) WITHOUT raising — the never-raises contract (§10.2).
 14. default_executor_for maps SEARCH/INSPECT/ASK_HUMAN to the real
     executors and RETRIEVE to NullExecutor (Part 2 §4.1), with fake
     Action objects.
"""
import asyncio
import inspect
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

HARI = Path("/home/z/my-project/hari")
sys.path.insert(0, str(HARI))

FAILURES = []


def check(name, cond, extra=""):
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {extra}" if (extra and not ok) else ""))
    if not ok:
        FAILURES.append(name)
    return ok


# --------------------------------------------------------------------------
# stubs (pattern from .scratch/final_import_sweep.py)
# --------------------------------------------------------------------------

def _mk_async(name):
    async def f(*a, **k):
        raise RuntimeError("stub-%s" % name)
    return f


def install_stubs():
    litellm = types.ModuleType("litellm")
    litellm.acompletion = _mk_async("acompletion")
    litellm.completion = lambda *a, **k: None
    litellm.drop_params = True
    litellm.num_retries = 2
    sys.modules["litellm"] = litellm

    asyncpg = types.ModuleType("asyncpg")
    asyncpg.Pool = type("Pool", (), {})
    asyncpg.Connection = type("Connection", (), {})
    asyncpg.create_pool = _mk_async("create_pool")
    asyncpg.exceptions = types.SimpleNamespace(
        UniqueViolationError=type("UniqueViolationError", (Exception,), {}),
        PostgresError=type("PostgresError", (Exception,), {}),
    )
    sys.modules["asyncpg"] = asyncpg

    pgv = types.ModuleType("pgvector")
    pgv_pg = types.ModuleType("pgvector.asyncpg")
    pgv_pg.register_vector = lambda pool: None
    pgv_pg.register_vector_resources = lambda conn: None
    pgv.asyncpg = pgv_pg
    sys.modules["pgvector"] = pgv
    sys.modules["pgvector.asyncpg"] = pgv_pg

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    gtypes = types.ModuleType("google.genai.types")
    genai.Client = type("Client", (), {"__init__": lambda self, *a, **k: None})
    genai.types = gtypes
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = gtypes

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv


install_stubs()

# --------------------------------------------------------------------------
# 1. EventLogger methods (doc §2.3 check 1 — R1)
# --------------------------------------------------------------------------
from engine.events import EventLogger  # noqa: E402

required_events_methods = [
    "set_turn", "set_case_id", "log_hari_expression",
    "log_background_tick", "log_case_event",
]
missing = [m for m in required_events_methods if not hasattr(EventLogger, m)]
check("EventLogger has all 5 required methods (R1)",
      not missing, extra=f"missing: {missing}")

# --------------------------------------------------------------------------
# 2. next_best_action (doc §2.3 check 2 — R4 + Part 2 §8 debt bonus)
# --------------------------------------------------------------------------
from case_engine import next_best_action as nba  # noqa: E402

for fn in ("compute_priority", "compute_priority_with_factors",
           "select_next_best_action"):
    check(f"next_best_action has {fn} (R4)", hasattr(nba, fn))

src = inspect.getsource(nba.compute_priority_with_factors)
check("compute_priority_with_factors source contains debt_bonus (Part 2 §8)",
      "debt_bonus" in src)

# --------------------------------------------------------------------------
# 3. action.py surface (doc §2.3 check 3 — R4/R5 + Part 2 §4)
# --------------------------------------------------------------------------
from case_engine import action  # noqa: E402

for attr in ("edit_action", "ActionExecutor", "NullExecutor",
             "WebSearchExecutor", "FileInspectExecutor", "HumanAskExecutor",
             "default_executor_for"):
    check(f"action.py has {attr} (R4/R5)", hasattr(action, attr))

# --------------------------------------------------------------------------
# 4. find_contradicting_claims prefilter (doc §2.3 check 4 — R3)
# --------------------------------------------------------------------------
from case_engine.claim import find_contradicting_claims  # noqa: E402

src = inspect.getsource(find_contradicting_claims)
check("find_contradicting_claims has prefilter (_CANDIDATE_MIN_OVERLAP, R3)",
      "_CANDIDATE_MIN_OVERLAP" in src)

# --------------------------------------------------------------------------
# 5. recompute_hypothesis_confidence (doc §2.3 check 5 — Part 2 §1 P2)
# --------------------------------------------------------------------------
from case_engine.hypothesis import recompute_hypothesis_confidence  # noqa: E402

src = inspect.getsource(recompute_hypothesis_confidence)
check("recompute_hypothesis_confidence has direct evidence query "
      "(direct_ev_rows, P2)", "direct_ev_rows" in src)
check("recompute_hypothesis_confidence has relation branch (P2/Correction 6)",
      "relation" in src)

# --------------------------------------------------------------------------
# 6-7. case_type dispatchers (Part 2 §3)
# --------------------------------------------------------------------------
from case_engine.decision import decide  # noqa: E402
from case_engine.action import generate_actions  # noqa: E402

src = inspect.getsource(decide)
check("decide dispatches to _decide_research", "_decide_research" in src)
check("decide dispatches to _decide_explore (stub per doc §3.2/§12 #6)",
      "_decide_explore" in src)

src = inspect.getsource(generate_actions)
check("generate_actions dispatches to _generate_actions_research",
      "_generate_actions_research" in src)

# --------------------------------------------------------------------------
# 8-9. cycle signature + shim identity (Part 2 §4.4 / §0.6 Option B)
# --------------------------------------------------------------------------
from case_engine import cycle as cycle_mod  # noqa: E402

sig = inspect.signature(cycle_mod.run_investigation_cycle)
check("run_investigation_cycle signature has enable_auto_execute",
      "enable_auto_execute" in sig.parameters,
      extra=f"params: {list(sig.parameters)}")

import case_engine.investigation as shim  # noqa: E402

check("investigation.py shim re-exports the SAME run_investigation_cycle",
      shim.run_investigation_cycle is cycle_mod.run_investigation_cycle)

# --------------------------------------------------------------------------
# 10. evidence.py (doc §2.3 extensions — P1 + Part 2 §2)
# --------------------------------------------------------------------------
from case_engine import evidence as evidence_mod  # noqa: E402

check("evidence.py defines DEFAULT_QUALITY_SCORES (P1)",
      hasattr(evidence_mod, "DEFAULT_QUALITY_SCORES"))
check("evidence.py defines ingest_external_analysis (Part 2 §2)",
      hasattr(evidence_mod, "ingest_external_analysis"))
if hasattr(evidence_mod, "DEFAULT_QUALITY_SCORES"):
    dq = evidence_mod.DEFAULT_QUALITY_SCORES
    check("DEFAULT_QUALITY_SCORES has MEASUREMENT=0.95 / OBSERVATION=0.65",
          dq.get("MEASUREMENT") == 0.95 and dq.get("OBSERVATION") == 0.65,
          extra=str(dq))

# --------------------------------------------------------------------------
# 11. endogenous + nli import with entry points (Part 2 §5 + §10)
# --------------------------------------------------------------------------
from case_engine import endogenous, nli  # noqa: E402

check("endogenous.collect_case_endogenous_candidates exists",
      callable(endogenous.collect_case_endogenous_candidates))
check("nli.classify_claim_against_hypothesis exists",
      callable(nli.classify_claim_against_hypothesis))

# --------------------------------------------------------------------------
# 12. schema columns from migration 015 (Part 2 §1)
# --------------------------------------------------------------------------
from case_engine.schemas import Case, Hypothesis, Evidence  # noqa: E402

check("Case schema has case_type", "case_type" in Case.model_fields)
check("Hypothesis schema has origin", "origin" in Hypothesis.model_fields)
check("Evidence schema has source_llm", "source_llm" in Evidence.model_fields)

# --------------------------------------------------------------------------
# 13. NLI never-raises contract without transformers (Part 2 §10.2)
# --------------------------------------------------------------------------
try:
    import transformers  # noqa: F401
    transformers_present = True
except ImportError:
    transformers_present = False
check("precondition: transformers absent in this sandbox (NLI optional dep)",
      not transformers_present)

result = asyncio.run(nli.classify_claim_against_hypothesis(
    "cache hit rate dropped", "the connection pool is exhausted"))
check("classify_claim_against_hypothesis returns None without transformers "
      "(never raises)", result is None, extra=f"got {result!r}")

# --------------------------------------------------------------------------
# 14. default_executor_for mapping (Part 2 §4.1) with fake Action objects
# --------------------------------------------------------------------------
from case_engine.schemas import Action  # noqa: E402


def fake_action(action_type):
    return Action(
        action_id=f"act_fake_{action_type.lower()}",
        case_id="case_fake",
        action_type=action_type,
        created_at=datetime.now(timezone.utc),
    )


mapping = {
    "SEARCH": action.WebSearchExecutor,
    "INSPECT": action.FileInspectExecutor,
    "ASK_HUMAN": action.HumanAskExecutor,
    "RETRIEVE": action.NullExecutor,
}
for atype, executor_cls in mapping.items():
    ex = action.default_executor_for(fake_action(atype))
    check(f"default_executor_for({atype}) -> {executor_cls.__name__}",
          isinstance(ex, executor_cls), extra=type(ex).__name__)

# one no-op spot-check beyond RETRIEVE (7 no-op types documented §4.6)
ex = action.default_executor_for(fake_action("COMPARE"))
check("default_executor_for(COMPARE) -> NullExecutor (documented no-op)",
      isinstance(ex, action.NullExecutor))

# --------------------------------------------------------------------------
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURES: {FAILURES}")
    sys.exit(1)
print("ALL RUNTIME ATTRIBUTE CHECKS PASSED")