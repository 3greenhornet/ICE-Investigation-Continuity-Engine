"""cli/cli.py — Investigation Continuity Engine CLI.

Command groups (ICE spec §14.1):
    case          create / list / show / update / summary
                  (create takes --type debug|research|explore — Part 2 §3.4;
                  required_confidence now defaults per case_type when the
                  flag is omitted: 0.85 debug / 0.0 research & explore)
    evidence      add / add-llm / upload / url / list / show / supersede /
                  chain  (add-llm files an external LLM analysis as
                  MODEL_INFERENCE evidence — Part 2 §2.5)
    claim         extract / create / list / show / link
    hypothesis    create / list / show / versions / recompute / status / graph
    contradiction list / detect / resolve
    gap           create / list / show / resolve
    action        list / show / approve / execute / reject / generate
    decision      run / show
    investigate   run / state   (delegates to
                  case_engine.cycle.run_investigation_cycle — audit #22,
                  the same logic the API route uses; accepts --falsify and
                  --auto-execute — Part 2 §4.5)
    replay        run / counterfactual

Audit-gap extensions (#11/#12 — benchmark + export surface):
    export        json / markdown / trace / graph
    benchmark     run / baselines / report   (benchmark/ is imported lazily
                  inside the dispatch functions so the CLI loads without it)

Exit codes (§14.2):
    0  success
    2  invalid arguments / user error (bad IDs, invalid status, empty strings)
    1  runtime error (DB down, LLM down, unexpected exception)

Note on §14.3 acceptance #2: ``case show none_123`` must exit 2 with
``Error: Case not found: none_123``. Not-found IDs are user errors, so the
entire *NotFoundError family from case_engine.exceptions is mapped to exit
 code 2 alongside ValueError (§14.2 "bad IDs"), while DatabaseUnavailable /
LLMUnavailable stay runtime errors (exit 1).
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from case_engine.case import (
    create_case, get_case, list_cases, update_case_status, get_case_summary,
)
from case_engine.evidence import (
    ingest_evidence, ingest_evidence_from_file, ingest_evidence_from_url,
    ingest_external_analysis, mark_superseded, get_evidence,
    get_active_evidence, _create_source,
)
from case_engine.claim import (
    extract_claims, create_claim, link_evidence_to_claim,
    get_claim, get_claims_for_case, set_claim_status,
)
from case_engine.hypothesis import (
    create_hypothesis, get_hypothesis, get_current_version,
    get_version_history, recompute_hypothesis_confidence,
    set_hypothesis_status, list_hypotheses_for_case,
)
from case_engine.contradiction import (
    detect_contradiction, get_active_contradictions, resolve_contradiction,
)
from case_engine.information_gap import (
    create_gap, get_gap, list_gaps_for_case, compute_gap_priority, resolve_gap,
)
from case_engine.evidence_graph import get_hypothesis_graph, get_evidence_chain
from case_engine.provenance import compute_evidence_trace_coverage
from case_engine.action import (
    generate_actions, get_pending_actions, approve_action,
    execute_action, reject_action, get_action,
)
from case_engine.next_best_action import select_next_best_action
from case_engine.decision import decide
from case_engine.replay import (
    replay_case, replay_without_evidence, compute_evidence_causal_influence,
    compute_decision_attribution,
)
from case_engine.cycle import run_investigation_cycle  # renamed from case_engine.investigation (merge spec Part 3 §0.6)
from case_engine.exceptions import (
    CaseNotFoundError, EvidenceNotFoundError, ClaimNotFoundError,
    HypothesisNotFoundError, ContradictionNotFoundError, GapNotFoundError,
    ThreadNotFoundError, ActionNotFoundError, DecisionNotFoundError,
)
from db.connection import init_db, close_db, get_pool

# Bad IDs are user errors (§14.2): these map to exit code 2, not 1.
_USER_ERROR_EXCEPTIONS = (
    ValueError,
    CaseNotFoundError, EvidenceNotFoundError, ClaimNotFoundError,
    HypothesisNotFoundError, ContradictionNotFoundError, GapNotFoundError,
    ThreadNotFoundError, ActionNotFoundError, DecisionNotFoundError,
)


def _parse_iso(value):
    """Parse an ISO-8601 string into a datetime; None-safe; Z -> +00:00."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_default(obj):
    """JSON encoder fallback: datetimes -> ISO strings, models -> dicts."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _dump_json(payload) -> str:
    """Serialize payload exactly the way the spec's CLI prints JSON."""
    return json.dumps(payload, indent=2, default=_json_default)


def _write_output(text: str, out: str | None) -> None:
    """Write text to --out FILE (plus a confirmation) or to stdout."""
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
        print(f"Wrote {out}")
    else:
        print(text)


# ---------------------------------------------------------------------------
# Parser (§14.1) — the full argument tree
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ice", description="Investigation Continuity Engine"
    )
    sub = parser.add_subparsers(dest="group", required=True)

    # ---- case
    case = sub.add_parser("case")
    case_sub = case.add_subparsers(dest="cmd", required=True)

    p = case_sub.add_parser("create")
    p.add_argument("--title", required=True)
    p.add_argument("--objective", required=True)
    p.add_argument("--deadline", default=None)
    # Part 2 §3.4: default None so create_case applies the per-type default
    # (0.85 debug / 0.0 research & explore) instead of a fixed 0.85 that
    # would silently override research/explore cases.
    p.add_argument("--required-confidence", type=float, default=None)
    p.add_argument(
        "--type",
        default="debug",
        choices=["debug", "research", "explore"],
        help="Case type: selects decision/action behaviour (Part 2 §3.4)",
    )

    p = case_sub.add_parser("list")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=50)

    p = case_sub.add_parser("show")
    p.add_argument("case_id")

    p = case_sub.add_parser("update")
    p.add_argument("case_id")
    p.add_argument("--status", required=True)
    p.add_argument("--reason", default=None)

    p = case_sub.add_parser("summary")
    p.add_argument("case_id")

    # ---- evidence
    ev = sub.add_parser("evidence")
    ev_sub = ev.add_subparsers(dest="cmd", required=True)

    p = ev_sub.add_parser("add")
    p.add_argument("case_id")
    p.add_argument("--content", required=True)
    p.add_argument("--type", default=None)
    p.add_argument("--observed-at", default=None)

    p = ev_sub.add_parser("add-llm")  # Part 2 §2.5: external LLM analysis
    p.add_argument("case_id")
    p.add_argument("--llm", required=True, help="LLM name, e.g. claude-3.5-sonnet")
    p.add_argument("--prompt", required=True, help="What you asked the LLM")
    p.add_argument("--response", required=True, help="What the LLM said")
    p.add_argument("--context", default=None)
    p.add_argument("--source-uri", default=None)

    p = ev_sub.add_parser("upload")
    p.add_argument("case_id")
    p.add_argument("--file", required=True)

    p = ev_sub.add_parser("url")
    p.add_argument("case_id")
    p.add_argument("--url", required=True)

    p = ev_sub.add_parser("list")
    p.add_argument("case_id")

    p = ev_sub.add_parser("show")
    p.add_argument("evidence_id")

    p = ev_sub.add_parser("supersede")
    p.add_argument("old_evidence_id")
    p.add_argument("--new", required=True)
    p.add_argument("--reason", required=True)

    p = ev_sub.add_parser("chain")
    p.add_argument("evidence_id")

    # ---- claim
    cl = sub.add_parser("claim")
    cl_sub = cl.add_subparsers(dest="cmd", required=True)

    p = cl_sub.add_parser("extract")
    p.add_argument("evidence_id")

    p = cl_sub.add_parser("create")
    p.add_argument("case_id")
    p.add_argument("--statement", required=True)
    p.add_argument("--type", required=True)

    p = cl_sub.add_parser("list")
    p.add_argument("case_id")

    p = cl_sub.add_parser("show")
    p.add_argument("claim_id")

    p = cl_sub.add_parser("link")
    p.add_argument("claim_id")
    p.add_argument("--evidence", required=True)
    p.add_argument("--relation", choices=["support", "contradict"], required=True)

    p = cl_sub.add_parser("status")
    p.add_argument("claim_id")
    p.add_argument("--status", choices=["ACTIVE", "SUPERSEDED", "RETRACTED"], required=True)
    p.add_argument("--reason", required=True)

    # ---- hypothesis
    hy = sub.add_parser("hypothesis")
    hy_sub = hy.add_subparsers(dest="cmd", required=True)

    p = hy_sub.add_parser("create")
    p.add_argument("case_id")
    p.add_argument("--statement", required=True)
    p.add_argument("--confidence", type=float, default=0.5)

    p = hy_sub.add_parser("list")
    p.add_argument("case_id")

    p = hy_sub.add_parser("show")
    p.add_argument("hypothesis_id")

    p = hy_sub.add_parser("versions")
    p.add_argument("hypothesis_id")

    p = hy_sub.add_parser("recompute")
    p.add_argument("hypothesis_id")

    p = hy_sub.add_parser("status")
    p.add_argument("hypothesis_id")
    p.add_argument("--status", required=True)

    p = hy_sub.add_parser("graph")
    p.add_argument("hypothesis_id")

    # ---- contradiction
    co = sub.add_parser("contradiction")
    co_sub = co.add_subparsers(dest="cmd", required=True)

    p = co_sub.add_parser("list")
    p.add_argument("case_id")

    p = co_sub.add_parser("detect")
    p.add_argument("--claim-a", required=True)
    p.add_argument("--claim-b", required=True)

    p = co_sub.add_parser("resolve")
    p.add_argument("contradiction_id")
    p.add_argument("--evidence", required=True)
    p.add_argument("--summary", required=True)

    # ---- gap
    ga = sub.add_parser("gap")
    ga_sub = ga.add_subparsers(dest="cmd", required=True)

    p = ga_sub.add_parser("create")
    p.add_argument("case_id")
    p.add_argument("--needed", required=True)
    p.add_argument("--why", default="")
    p.add_argument("--discriminates", nargs="*", default=[])

    p = ga_sub.add_parser("list")
    p.add_argument("case_id")

    p = ga_sub.add_parser("show")
    p.add_argument("gap_id")

    p = ga_sub.add_parser("resolve")
    p.add_argument("gap_id")
    p.add_argument("--evidence", required=True)

    # ---- action
    ac = sub.add_parser("action")
    ac_sub = ac.add_subparsers(dest="cmd", required=True)

    p = ac_sub.add_parser("list")
    p.add_argument("case_id")

    p = ac_sub.add_parser("show")
    p.add_argument("action_id")

    p = ac_sub.add_parser("approve")
    p.add_argument("action_id")

    p = ac_sub.add_parser("execute")
    p.add_argument("action_id")

    p = ac_sub.add_parser("reject")
    p.add_argument("action_id")
    p.add_argument("--reason", required=True)

    p = ac_sub.add_parser("generate")
    p.add_argument("case_id")

    # ---- decision
    de = sub.add_parser("decision")
    de_sub = de.add_subparsers(dest="cmd", required=True)

    p = de_sub.add_parser("run")
    p.add_argument("case_id")

    p = de_sub.add_parser("show")
    p.add_argument("decision_id")

    # ---- investigate
    inv = sub.add_parser("investigate")
    inv_sub = inv.add_subparsers(dest="cmd", required=True)

    p = inv_sub.add_parser("run")
    p.add_argument("case_id")
    p.add_argument(
        "--falsify",
        action="store_true",
        help="Enable hypothesis falsification loop",
    )  # merge spec Part 1 §5.4 P4 / improvement I4
    p.add_argument(
        "--auto-execute",
        action="store_true",
        help="Approve and execute the top-priority action",
    )  # ICE fixes doc Part 2 §4.5

    p = inv_sub.add_parser("state")
    p.add_argument("case_id")

    # ---- replay
    rp = sub.add_parser("replay")
    rp_sub = rp.add_subparsers(dest="cmd", required=True)

    p = rp_sub.add_parser("run")
    p.add_argument("case_id")

    p = rp_sub.add_parser("counterfactual")
    p.add_argument("case_id")
    p.add_argument("--exclude-evidence", required=True)

    # ---- export (audit gaps #11/#12)
    ex = sub.add_parser("export")
    ex_sub = ex.add_subparsers(dest="cmd", required=True)

    p = ex_sub.add_parser("json")
    p.add_argument("case_id")
    p.add_argument("--out", default=None)

    p = ex_sub.add_parser("markdown")
    p.add_argument("case_id")
    p.add_argument("--out", default=None)

    p = ex_sub.add_parser("trace")
    p.add_argument("case_id")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", default=None)

    p = ex_sub.add_parser("graph")
    p.add_argument("hypothesis_id")
    p.add_argument("--out", default=None)

    # ---- benchmark (audit gaps #11/#12)
    bm = sub.add_parser("benchmark")
    bm_sub = bm.add_subparsers(dest="cmd", required=True)

    p = bm_sub.add_parser("run")
    p.add_argument("--cases", default="benchmark/cases")
    p.add_argument("--json", default=None)
    # Hari ICE improvements pass (P0 rebuild): default is the honest
    # no-leakage mode (engine induces its own hypotheses);
    # --seed-from-resolution re-enables the OLD leaking behavior for
    # comparison runs; --execute-actions runs the full approve+execute
    # loop so outcome semantics are measured too.
    p.add_argument("--seed-from-resolution", action="store_true",
                   help="LEGACY: seed hypotheses from the hidden resolution")
    p.add_argument("--execute-actions", action="store_true",
                   help="approve + execute proposed actions during the run")

    p = bm_sub.add_parser("baselines")
    p.add_argument("case_id")
    p.add_argument("--question", default="What is the root cause?")

    p = bm_sub.add_parser("report")
    p.add_argument("--results", default="benchmark/results.json")

    # ---- reasoning (Hari ICE improvements pass: P1-P5 surface) ----
    rs = sub.add_parser("reasoning")
    rs_sub = rs.add_subparsers(dest="cmd", required=True)

    p = rs_sub.add_parser("induce")
    p.add_argument("case_id")
    p.add_argument("--max-candidates", type=int, default=6)
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("cycle")
    p.add_argument("case_id")
    p.add_argument("--induction", action="store_true")
    p.add_argument("--workspace", action="store_true")
    p.add_argument("--falsification", action="store_true")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("workspace")
    p.add_argument("case_id")
    p.add_argument("--run", action="store_true")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("ach")
    p.add_argument("case_id")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("timeline")
    p.add_argument("case_id")
    p.add_argument("--state-change", action="store_true")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("search")
    p.add_argument("case_id")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--cross-case", action="store_true")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("contrastive")
    p.add_argument("case_id")
    p.add_argument("query")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("trajectory")
    p.add_argument("case_id")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("introspection")
    p.add_argument("case_id")
    p.add_argument("--fleet", action="store_true",
                   help="cross-case comparison instead of one case")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("predict")
    p.add_argument("case_id")
    p.add_argument("--resolve", default=None,
                   help="prediction_id to resolve")
    p.add_argument("--status", default="CONFIRMED",
                   choices=["CONFIRMED", "REFUTED", "UNRESOLVED", "VOID"])
    p.add_argument("--observed", default="")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("calibrate")
    p.add_argument("case_id")

    p = rs_sub.add_parser("plan")
    p.add_argument("case_id")
    p.add_argument("--add", default=None, help="add a plan item description")
    p.add_argument("--reconcile", action="store_true")
    p.add_argument("--out", default=None)

    p = rs_sub.add_parser("mechanisms")
    p.add_argument("--decision", default=None)

    # ---- goals group (Evolving Project Model — goal system phase) ----
    gl = sub.add_parser("goals")
    gl_sub = gl.add_subparsers(dest="cmd", required=True)

    p = gl_sub.add_parser("add")
    p.add_argument("case_id")
    p.add_argument("statement")
    p.add_argument("--kind", default="goal",
                   choices=["goal", "need", "actor", "scenario",
                            "requirement", "quality_objective",
                            "constraint", "assumption", "decision",
                            "alternative", "risk", "open_question",
                            "milestone"])
    p.add_argument("--clarity", type=int, default=1, choices=[1, 2, 3, 4])
    p.add_argument("--origin", default="developer_statement",
                   choices=["developer_statement", "developer_confirmed",
                            "ice_inference", "ice_proposal",
                            "repo_observation"])
    p.add_argument("--rationale", default=None)
    p.add_argument("--supersedes", default=None)

    p = gl_sub.add_parser("list")
    p.add_argument("case_id")
    p.add_argument("--kind", default=None)
    p.add_argument("--hide-superseded", action="store_true")

    p = gl_sub.add_parser("show")
    p.add_argument("goal_id")

    p = gl_sub.add_parser("status")
    p.add_argument("goal_id")
    p.add_argument("status",
                   choices=["exploring", "active", "satisfied",
                            "abandoned", "superseded"])
    p.add_argument("--by", default="user", choices=["user", "ice"])
    p.add_argument("--note", default="")

    p = gl_sub.add_parser("supersede")
    p.add_argument("goal_id")
    p.add_argument("statement")
    p.add_argument("--rationale", default="")

    p = gl_sub.add_parser("decompose")
    p.add_argument("goal_id")

    p = gl_sub.add_parser("edge")
    p.add_argument("from_goal_id")
    p.add_argument("to_goal_id")
    p.add_argument("relation",
                   choices=["subgoal_of", "requires", "conflicts_with",
                            "alternative_to", "mitigates", "addresses",
                            "elaborates", "operationalizes", "replaces"])
    p.add_argument("--note", default="")

    p = gl_sub.add_parser("maturity")
    p.add_argument("goal_id")
    p.add_argument("maturity",
                   choices=["no_candidate", "scaffolded", "candidate",
                            "connected", "tested", "verified",
                            "runtime_observed"])
    p.add_argument("--evidence", nargs="*", default=[],
                   help="evidence ids backing the assessment (required "
                        "above 'scaffolded')")
    p.add_argument("--note", default="")

    p = gl_sub.add_parser("intent-profile")
    p.add_argument("case_id")

    p = gl_sub.add_parser("reconcile")
    p.add_argument("case_id")

    # ---- research group (STORM-lite research mode) ----
    rs = sub.add_parser("research")
    rs_sub2 = rs.add_subparsers(dest="cmd", required=True)

    p = rs_sub2.add_parser("perspectives")
    p.add_argument("case_id")

    p = rs_sub2.add_parser("questions")
    p.add_argument("case_id")
    p.add_argument("--max-gaps", type=int, default=6)
    p.add_argument("--all-perspectives", action="store_true")

    p = rs_sub2.add_parser("outline")
    p.add_argument("case_id")

    p = rs_sub2.add_parser("brief")
    p.add_argument("case_id")
    p.add_argument("--max-gaps", type=int, default=6)

    # ---- repointel group (RepoIntel AST, call graph, and maturity checks) ----
    rp = sub.add_parser("repointel")
    rp_sub = rp.add_subparsers(dest="cmd", required=True)

    p = rp_sub.add_parser("status")

    p = rp_sub.add_parser("arch")
    p.add_argument("--repo-id", default=None)

    p = rp_sub.add_parser("symbols")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)

    p = rp_sub.add_parser("callers")
    p.add_argument("symbol")

    p = rp_sub.add_parser("code")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)

    p = rp_sub.add_parser("maturity")
    p.add_argument("statement")
    p.add_argument("--goal-id", default="g_cli")
    p.add_argument("--kind", default="goal")

    return parser


# ---------------------------------------------------------------------------
# Per-group dispatch helpers (clean restructure of the spec's broken
# if/elif tree — behavior identical)
# ---------------------------------------------------------------------------

async def _dispatch_case(args, parser) -> int:
    if args.cmd == "create":
        case = await create_case(
            title=args.title,
            objective=args.objective,
            decision_deadline=_parse_iso(args.deadline),
            required_confidence=args.required_confidence,
            case_type=args.type,  # Part 2 §3.4
        )
        print(_dump_json(case.model_dump(mode="json")))
        return 0
    elif args.cmd == "list":
        cases = await list_cases(status=args.status, limit=args.limit)
        for c in cases:
            print(f"{c.case_id}\t{c.status:14s}\t{c.title}")
        return 0
    elif args.cmd == "show":
        case = await get_case(args.case_id)
        print(_dump_json(case.model_dump(mode="json")))
        return 0
    elif args.cmd == "update":
        case = await update_case_status(args.case_id, args.status, args.reason)
        print(_dump_json(case.model_dump(mode="json")))
        return 0
    elif args.cmd == "summary":
        summary = await get_case_summary(args.case_id)
        print(_dump_json(summary))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_evidence(args, parser) -> int:
    if args.cmd == "add":
        src = await _create_source(args.case_id, "HUMAN", None)
        ev = await ingest_evidence(
            args.case_id, args.content, src.source_id,
            evidence_type=args.type,
            observed_at=_parse_iso(args.observed_at),
        )
        print(_dump_json(ev.model_dump(mode="json")))
        return 0
    elif args.cmd == "add-llm":
        # Part 2 §2.5: file an external LLM analysis as MODEL_INFERENCE
        # evidence with source_llm set (dedup on (llm, prompt) hash inside).
        ev = await ingest_external_analysis(
            case_id=args.case_id,
            llm_name=args.llm,
            prompt_used=args.prompt,
            analysis_text=args.response,
            conversation_context=args.context,
            source_uri=args.source_uri,
        )
        print(_dump_json(ev.model_dump(mode="json")))
        return 0
    elif args.cmd == "upload":
        items = await ingest_evidence_from_file(args.case_id, args.file)
        print(f"Ingested {len(items)} evidence items.")
        for ev in items:
            print(f" {ev.evidence_id}\t{ev.evidence_type}")
        return 0
    elif args.cmd == "url":
        items = await ingest_evidence_from_url(args.case_id, args.url)
        for ev in items:
            print(f" {ev.evidence_id}\t{ev.evidence_type}")
        return 0
    elif args.cmd == "list":
        items = await get_active_evidence(args.case_id)
        for ev in items:
            print(f"{ev.evidence_id}\t{ev.evidence_type:16s}\t{ev.content[:80]}")
        return 0
    elif args.cmd == "show":
        ev = await get_evidence(args.evidence_id)
        print(_dump_json(ev.model_dump(mode="json")))
        return 0
    elif args.cmd == "supersede":
        await mark_superseded(args.old_evidence_id, args.new, args.reason)
        print(f"Marked {args.old_evidence_id} superseded by {args.new}")
        return 0
    elif args.cmd == "chain":
        chain = await get_evidence_chain(args.evidence_id)
        print(_dump_json(chain))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_claim(args, parser) -> int:
    if args.cmd == "extract":
        claims = await extract_claims(args.evidence_id)
        print(f"Extracted {len(claims)} claims.")
        return 0
    elif args.cmd == "create":
        claim = await create_claim(args.case_id, args.statement, args.type)
        print(_dump_json(claim.model_dump(mode="json")))
        return 0
    elif args.cmd == "list":
        claims = await get_claims_for_case(args.case_id)
        for c in claims:
            print(f"{c.claim_id}\t{c.claim_type:22s}\t{c.statement[:80]}")
        return 0
    elif args.cmd == "show":
        claim = await get_claim(args.claim_id)
        print(_dump_json(claim.model_dump(mode="json")))
        return 0
    elif args.cmd == "link":
        await link_evidence_to_claim(args.claim_id, args.evidence, args.relation)
        print(f"Linked {args.evidence} → {args.claim_id} ({args.relation})")
        return 0
    elif args.cmd == "status":
        claim = await set_claim_status(args.claim_id, args.status, args.reason)
        print(_dump_json(claim.model_dump(mode="json")))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_hypothesis(args, parser) -> int:
    if args.cmd == "create":
        h = await create_hypothesis(args.case_id, args.statement, args.confidence)
        print(_dump_json(h.model_dump(mode="json")))
        return 0
    elif args.cmd == "list":
        items = await list_hypotheses_for_case(args.case_id)
        for h in items:
            v = await get_current_version(h.hypothesis_id)
            print(
                f"{h.hypothesis_id}\t{v.confidence:.3f}\t"
                f"v{v.version_number}\t{h.statement[:60]}"
            )
        return 0
    elif args.cmd == "show":
        h = await get_hypothesis(args.hypothesis_id)
        print(_dump_json(h.model_dump(mode="json")))
        return 0
    elif args.cmd == "versions":
        versions = await get_version_history(args.hypothesis_id)
        for v in versions:
            print(
                f"v{v.version_number}\t{v.confidence:.3f}\t"
                f"prior={v.prior_confidence}\t{v.reason}"
            )
        return 0
    elif args.cmd == "recompute":
        version = await recompute_hypothesis_confidence(
            args.hypothesis_id, "cli_recompute"
        )
        if version is None:
            print("No change.")
        else:
            print(f"New version v{version.version_number}: {version.confidence:.3f}")
        return 0
    elif args.cmd == "status":
        h = await set_hypothesis_status(args.hypothesis_id, args.status, "cli_update")
        print(f"Status: {h.status}")
        return 0
    elif args.cmd == "graph":
        graph = await get_hypothesis_graph(args.hypothesis_id)
        print(_dump_json(graph))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_contradiction(args, parser) -> int:
    if args.cmd == "list":
        items = await get_active_contradictions(args.case_id)
        for c in items:
            print(
                f"{c.contradiction_id}\tsev={c.severity:.2f}\t"
                f"{c.claim_a[:40]} vs {c.claim_b[:40]}"
            )
        return 0
    elif args.cmd == "detect":
        c = await detect_contradiction(args.claim_a, args.claim_b)
        if c is None:
            print("No contradiction detected.")
        else:
            print(f"Contradiction: {c.contradiction_id} severity={c.severity:.2f}")
        return 0
    elif args.cmd == "resolve":
        c = await resolve_contradiction(args.contradiction_id, args.evidence, args.summary)
        print(f"Resolved: {c.contradiction_id}")
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_gap(args, parser) -> int:
    if args.cmd == "create":
        g = await create_gap(args.case_id, args.needed, args.why, args.discriminates)
        print(_dump_json(g.model_dump(mode="json")))
        return 0
    elif args.cmd == "list":
        items = await list_gaps_for_case(args.case_id)
        for g in items:
            p = await compute_gap_priority(g.gap_id)
            print(f"{g.gap_id}\tpriority={p:.2f}\t{g.needed_evidence[:60]}")
        return 0
    elif args.cmd == "show":
        g = await get_gap(args.gap_id)
        print(_dump_json(g.model_dump(mode="json")))
        return 0
    elif args.cmd == "resolve":
        g = await resolve_gap(args.gap_id, args.evidence)
        print(f"Resolved: {g.gap_id}")
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_action(args, parser) -> int:
    if args.cmd == "generate":
        actions = await generate_actions(args.case_id)
        print(f"Generated {len(actions)} actions.")
        return 0
    elif args.cmd == "list":
        items = await get_pending_actions(args.case_id)
        for a in items:
            print(f"{a.action_id}\t{a.action_type:20s}\t{(a.target or '')[:60]}")
        return 0
    elif args.cmd == "show":
        a = await get_action(args.action_id)
        print(_dump_json(a.model_dump(mode="json")))
        return 0
    elif args.cmd == "approve":
        a = await approve_action(args.action_id)
        print(f"Approved: {a.action_id}")
        return 0
    elif args.cmd == "execute":
        a = await execute_action(args.action_id)
        print(f"Executed: {a.action_id} → status={a.status}")
        return 0
    elif args.cmd == "reject":
        a = await reject_action(args.action_id, args.reason)
        print(f"Rejected: {a.action_id}")
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_decision(args, parser) -> int:
    if args.cmd == "run":
        d = await decide(args.case_id)
        print(f"{d.decision_type} (confidence={d.confidence:.2f}): {d.rationale}")
        return 0
    elif args.cmd == "show":
        attribution = await compute_decision_attribution(args.decision_id)
        print(_dump_json(attribution))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_investigate(args, parser) -> int:
    if args.cmd == "run":
        # Audit #22: same logic as the API route — one shared
        # run_investigation_cycle (recompute all hypotheses, optional action
        # generation, decide(), leading hypothesis, select_next_best_action).
        # --falsify (merge spec Part 1 §5.4 P4) flips the falsification leg;
        # --auto-execute (Part 2 §4.5) approves + executes the top action.
        result = await run_investigation_cycle(
            args.case_id,
            enable_falsification=getattr(args, "falsify", False),
            enable_auto_execute=getattr(args, "auto_execute", False),
        )
        decision = result["decision"]
        print(f"{decision['decision_type']} (confidence={decision['confidence']:.2f})")
        nba = result.get("recommended_action")
        if nba:
            print(f"Next action: {nba['action_type']} (priority {nba['priority']:.2f})")
        auto = result.get("auto_executed_action")
        if auto:
            print(
                f"Auto-executed: {auto['action_type']} (status={auto['status']})"
            )
        return 0
    elif args.cmd == "state":
        summary = await get_case_summary(args.case_id)
        print(_dump_json(summary))
        return 0
    parser.error("Unknown command")
    return 2


async def _dispatch_replay(args, parser) -> int:
    if args.cmd == "run":
        state = await replay_case(args.case_id)
        print(f"Events: {state.event_count}")
        print(f"Leading hypothesis: {state.leading_hypothesis_id}")
        print(f"Confidence: {state.leading_hypothesis_confidence:.3f}")
        return 0
    elif args.cmd == "counterfactual":
        result = await compute_evidence_causal_influence(
            args.case_id, args.exclude_evidence
        )
        print(_dump_json(result))
        return 0
    parser.error("Unknown command")
    return 2


# ---------------------------------------------------------------------------
# Export group (audit gaps #11/#12)
# ---------------------------------------------------------------------------

async def _export_case_bundle(case_id: str) -> dict:
    """Assemble the full JSON export for a case (summary + hypotheses +
    gaps + contradictions, all via case_engine)."""
    summary = await get_case_summary(case_id)

    hypotheses = []
    for h in await list_hypotheses_for_case(case_id):
        entry = {
            "hypothesis_id": h.hypothesis_id,
            "statement": h.statement,
            "status": h.status,
        }
        try:
            v = await get_current_version(h.hypothesis_id)
            entry["confidence"] = v.confidence
            entry["version_number"] = v.version_number
        except Exception:
            entry["confidence"] = None
            entry["version_number"] = None
        hypotheses.append(entry)

    gaps = [g.model_dump(mode="json") for g in await list_gaps_for_case(case_id)]
    contradictions = [
        c.model_dump(mode="json") for c in await get_active_contradictions(case_id)
    ]

    return {
        "summary": summary,
        "hypotheses": hypotheses,
        "gaps": gaps,
        "contradictions": contradictions,
    }


def _render_markdown(bundle: dict, decisions: list[dict]) -> str:
    """Render the export bundle + recent decisions as a Markdown report."""
    summary = bundle.get("summary") or {}
    counts = summary.get("counts") or {}

    lines: list[str] = []
    lines.append(f"# Case: {summary.get('title', '(untitled)')}")
    lines.append("")
    lines.append(f"**Objective:** {summary.get('objective', '')}")
    lines.append("")
    lines.append(f"**Status:** {summary.get('status', 'open')}")
    lines.append(
        f"**Required confidence:** {summary.get('required_confidence', 0.85):.2f}"
    )
    deadline = summary.get("decision_deadline")
    lines.append(f"**Decision deadline:** {deadline or 'None'}")
    lines.append("")

    lines.append("## Counts")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    for key in (
        "evidence_active", "evidence_total", "claims", "hypotheses",
        "hypotheses_supported", "contradictions_active", "gaps_unresolved",
        "threads_active", "actions_proposed", "decisions", "events",
    ):
        lines.append(f"| {key} | {counts.get(key, 0)} |")
    lines.append("")

    lines.append("## Leading Hypothesis")
    lines.append("")
    lh = summary.get("leading_hypothesis")
    if lh:
        lines.append(
            f"- **{lh.get('statement', '')}** — confidence "
            f"{lh.get('confidence', 0.0):.2f} (v{lh.get('version_number', 1)})"
        )
    else:
        lines.append("_No hypotheses yet._")
    lines.append("")

    lines.append("## Top Gaps")
    lines.append("")
    gaps = sorted(
        bundle.get("gaps") or [], key=lambda g: g.get("priority", 0.0), reverse=True
    )
    if gaps:
        for g in gaps[:10]:
            lines.append(
                f"- {g.get('needed_evidence', '')} "
                f"(priority {g.get('priority', 0.0):.2f})"
            )
    else:
        lines.append("_No unresolved gaps._")
    lines.append("")

    lines.append("## Active Contradictions")
    lines.append("")
    contradictions = bundle.get("contradictions") or []
    if contradictions:
        for c in contradictions:
            lines.append(
                f"- {c.get('claim_a', '')} vs {c.get('claim_b', '')} "
                f"(severity {c.get('severity', 0.0):.2f})"
            )
    else:
        lines.append("_No active contradictions._")
    lines.append("")

    lines.append("## Recent Decisions")
    lines.append("")
    if decisions:
        for d in decisions[:5]:
            created = str(d.get("created_at", ""))[:19]
            lines.append(
                f"- **{d.get('decision_type', '?')}** "
                f"(confidence {d.get('confidence', 0.0):.2f}) — {created}: "
                f"{d.get('rationale', '')}"
            )
    else:
        lines.append("_No decisions yet._")
    lines.append("")

    return "\n".join(lines)


async def _recent_decisions(case_id: str, limit: int = 5) -> list[dict]:
    """Fetch the most recent decisions for a case (read-only)."""
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT decision_id, decision_type, confidence, rationale, created_at
            FROM decisions WHERE case_id = $1
            ORDER BY created_at DESC LIMIT $2
            """,
            case_id, limit,
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def _dispatch_export(args, parser) -> int:
    if args.cmd == "json":
        bundle = await _export_case_bundle(args.case_id)
        _write_output(_dump_json(bundle), args.out)
        return 0
    elif args.cmd == "markdown":
        bundle = await _export_case_bundle(args.case_id)
        decisions = await _recent_decisions(args.case_id)
        _write_output(_render_markdown(bundle, decisions), args.out)
        return 0
    elif args.cmd == "trace":
        pool = await get_pool()
        if pool is None:
            raise ValueError("Database unavailable: cannot export trace")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM case_events WHERE case_id = $1
                ORDER BY event_seq ASC LIMIT $2
                """,
                args.case_id, args.limit,
            )
        events = []
        for r in rows:
            e = dict(r)
            for key, value in list(e.items()):
                if isinstance(value, datetime):
                    e[key] = value.isoformat()
            events.append(e)
        _write_output(_dump_json(events), args.out)
        return 0
    elif args.cmd == "graph":
        graph = await get_hypothesis_graph(args.hypothesis_id)
        _write_output(_dump_json(graph), args.out)
        return 0
    parser.error("Unknown command")
    return 2


# ---------------------------------------------------------------------------
# Reasoning group (Hari ICE improvements pass): induction, workspace, ACH,
# timeline, search, trajectory, introspection, predictions, plan, mechanisms.
# ---------------------------------------------------------------------------

async def _dispatch_reasoning(args, parser) -> int:
    if args.cmd == "induce":
        from case_engine.induction import induce_hypotheses

        result = await induce_hypotheses(
            args.case_id, max_candidates=args.max_candidates, create=True
        )
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "cycle":
        from case_engine.cycle import run_investigation_cycle

        result = await run_investigation_cycle(
            args.case_id,
            enable_induction=args.induction,
            enable_workspace=args.workspace,
            enable_falsification=args.falsification,
        )
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "workspace":
        if args.run:
            from case_engine.workspace import run_workspace_cycle

            result = await run_workspace_cycle(args.case_id)
        else:
            from case_engine.workspace import get_workspace

            result = await get_workspace(args.case_id)
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "ach":
        from case_engine.causal import build_ach_matrix

        result = await build_ach_matrix(args.case_id)
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "timeline":
        if args.state_change:
            from case_engine.timeline import explain_state_change

            result = await explain_state_change(args.case_id)
        else:
            from case_engine.timeline import build_case_timeline

            result = [e.to_dict() for e in await build_case_timeline(args.case_id)]
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "search":
        from case_engine.retrieval import search_evidence

        hits = await search_evidence(
            args.query, case_id=args.case_id, limit=args.limit,
            cross_case=args.cross_case,
        )
        result = [h.to_dict() for h in hits]
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "contrastive":
        from case_engine.retrieval import contrastive_search

        hits = await contrastive_search(args.query, case_id=args.case_id)
        result = [h.to_dict() for h in hits]
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "trajectory":
        from case_engine.trajectory import localize_first_failure

        result = await localize_first_failure(args.case_id)
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "introspection":
        if args.fleet:
            from case_engine.introspection import fleet_flow_report

            result = await fleet_flow_report()
        else:
            from case_engine.introspection import investigation_flow_report

            result = await investigation_flow_report(args.case_id)
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "predict":
        from case_engine.predictions import (
            list_predictions, resolve_prediction,
        )

        if args.resolve:
            result = await resolve_prediction(
                args.resolve, args.status, args.observed or "(no detail)"
            )
        else:
            result = await list_predictions(case_id=args.case_id)
        print(_dump_json(result))
        return 0

    if args.cmd == "calibrate":
        from case_engine.predictions import prediction_calibration_report

        print(_dump_json(await prediction_calibration_report(args.case_id)))
        return 0

    if args.cmd == "plan":
        if args.add:
            from case_engine.plan import create_plan_item

            result = await create_plan_item(args.case_id, args.add)
        elif args.reconcile:
            from case_engine.plan import reconcile_plan

            result = await reconcile_plan(args.case_id)
        else:
            from case_engine.plan import list_plan_items, list_plan_revisions

            result = {
                "items": await list_plan_items(args.case_id),
                "revisions": await list_plan_revisions(args.case_id),
            }
        print(_dump_json(result))
        _write_output(_dump_json(result), args.out)
        return 0

    if args.cmd == "mechanisms":
        from case_engine.mechanism_lab import (
            list_mechanisms, seed_mechanism_lab,
        )

        try:
            n = await seed_mechanism_lab()
            if n:
                print(f"seeded {n} mechanisms")
        except Exception:
            pass
        print(_dump_json(await list_mechanisms(decision=args.decision)))
        return 0

    parser.error("Unknown command")
    return 2


# ---------------------------------------------------------------------------
# Benchmark group (audit gaps #11/#12) — benchmark/ is imported lazily so
# the CLI compiles and loads even before the benchmark package exists.
# ---------------------------------------------------------------------------

async def _dispatch_benchmark(args, parser) -> int:
    if args.cmd == "run":
        from benchmark.runner import run_all_cases
        from benchmark.metrics import report

        results = await run_all_cases(
            args.cases,
            seed_hypotheses_from_resolution=args.seed_from_resolution,
            execute_actions=args.execute_actions,
        )
        rep = report(results)
        print(_dump_json(rep))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, default=_json_default)
            print(f"Saved results to {args.json}")
        return 0
    elif args.cmd == "baselines":
        from benchmark import baselines

        question = args.question
        runners = [
            ("B1 llm_recent", baselines.baseline_1_llm_recent, (args.case_id, question)),
            ("B2 vector_rag", baselines.baseline_2_vector_rag, (args.case_id, question)),
            ("B3 case_summary", baselines.baseline_3_case_summary, (args.case_id, question)),
            ("B4 no_contradictions", baselines.baseline_4_no_contradictions, (args.case_id, question)),
            ("B5 no_versioning", baselines.baseline_5_no_versioning, (args.case_id, question)),
            ("B6 full_engine", baselines.baseline_6_full_engine, (args.case_id, question)),
        ]
        for label, fn, call_args in runners:
            try:
                result = await fn(*call_args)
                print(f"{label}: {result}")
            except Exception as exc:
                print(f"{label}: (failed: {type(exc).__name__}: {exc})")
        return 0
    elif args.cmd == "report":
        from benchmark.metrics import report

        if not os.path.exists(args.results):
            raise ValueError(f"Results file not found: {args.results}")
        with open(args.results, encoding="utf-8") as fh:
            results = json.load(fh)
        if not isinstance(results, list):
            raise ValueError("Results file must contain a JSON list of result dicts")
        print(_dump_json(report(results)))
        return 0
    parser.error("Unknown command")
    return 2


# ---------------------------------------------------------------------------
# Goals group (Evolving Project Model — goal-system phase)
# ---------------------------------------------------------------------------

async def _dispatch_goals(args, parser) -> int:
    if args.cmd == "add":
        from case_engine.goals import create_goal

        result = await create_goal(
            case_id=args.case_id, statement=args.statement,
            kind=args.kind, clarity_level=args.clarity,
            origin=args.origin, rationale=args.rationale,
            supersedes=args.supersedes,
        )
    elif args.cmd == "list":
        from case_engine.goals import list_goals

        result = await list_goals(
            args.case_id, kind=args.kind,
            include_superseded=not args.hide_superseded,
        )
    elif args.cmd == "show":
        from case_engine.goals import get_goal

        result = await get_goal(args.goal_id)
    elif args.cmd == "status":
        from case_engine.goals import set_goal_status

        result = await set_goal_status(
            args.goal_id, args.status, decided_by=args.by, note=args.note,
        )
    elif args.cmd == "supersede":
        from case_engine.goals import create_goal, get_goal

        current = await get_goal(args.goal_id)
        result = await create_goal(
            case_id=current["case_id"], statement=args.statement,
            kind=current["kind"],
            clarity_level=current["clarity_level"],
            origin="developer_statement", rationale=args.rationale,
            supersedes=args.goal_id,
        )
    elif args.cmd == "decompose":
        from case_engine.goals import decompose_goal

        result = await decompose_goal(args.goal_id)
    elif args.cmd == "edge":
        from case_engine.goals import connect_goals

        result = await connect_goals(
            args.from_goal_id, args.to_goal_id, args.relation,
            rationale=args.note,
        )
    elif args.cmd == "maturity":
        from case_engine.goals import assess_maturity

        result = await assess_maturity(
            args.goal_id, args.maturity,
            evidence_ids=args.evidence, note=args.note,
        )
    elif args.cmd == "intent-profile":
        from case_engine.goals import intent_profile

        result = await intent_profile(args.case_id)
    elif args.cmd == "reconcile":
        from case_engine.goals import reconcile_goals

        result = await reconcile_goals(args.case_id)
    else:
        parser.error("Unknown command")
        return 2
    print(_dump_json(result))
    return 0


# ---------------------------------------------------------------------------
# Research group (STORM-lite research mode)
# ---------------------------------------------------------------------------

async def _dispatch_research(args, parser) -> int:
    if args.cmd == "perspectives":
        from case_engine.research import discover_perspectives

        result = await discover_perspectives(args.case_id)
    elif args.cmd == "questions":
        from case_engine.research import generate_research_questions

        result = await generate_research_questions(
            args.case_id, max_gaps=args.max_gaps,
            only_unexplored=not args.all_perspectives,
        )
    elif args.cmd == "outline":
        from case_engine.research import research_synthesis_outline

        result = await research_synthesis_outline(args.case_id)
    elif args.cmd == "brief":
        from case_engine.research import research_brief

        result = await research_brief(args.case_id, max_gaps=args.max_gaps)
    else:
        parser.error("Unknown command")
        return 2
    print(_dump_json(result))
    return 0


# ---------------------------------------------------------------------------
# RepoIntel group (AST, call graph, and maturity checks)
# ---------------------------------------------------------------------------

async def _dispatch_repointel(args, parser) -> int:
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()

    if args.cmd == "status":
        result = {
            "connected": client.is_available,
            "database_path": client.db_path,
            "repositories": client.list_repositories() if client.is_available else [],
        }
    elif args.cmd == "arch":
        result = client.get_architecture(repo_id=args.repo_id)
    elif args.cmd == "symbols":
        result = [s.__dict__ for s in client.search_symbols(args.query, limit=args.limit)]
    elif args.cmd == "callers":
        result = client.get_callers(args.symbol)
    elif args.cmd == "code":
        result = client.search_code(args.query, limit=args.limit)
    elif args.cmd == "maturity":
        assessment = client.check_goal_code_maturity(
            goal_id=args.goal_id, statement=args.statement, target_kind=args.kind
        )
        result = assessment.__dict__
    else:
        parser.error("Unknown command")
        return 2

    print(_dump_json(result))
    return 0


# ---------------------------------------------------------------------------
# Dispatch + entry point
# ---------------------------------------------------------------------------

_DISPATCHERS = {
    "case": _dispatch_case,
    "evidence": _dispatch_evidence,
    "claim": _dispatch_claim,
    "hypothesis": _dispatch_hypothesis,
    "contradiction": _dispatch_contradiction,
    "gap": _dispatch_gap,
    "action": _dispatch_action,
    "decision": _dispatch_decision,
    "investigate": _dispatch_investigate,
    "reasoning": _dispatch_reasoning,
    "replay": _dispatch_replay,
    "export": _dispatch_export,
    "benchmark": _dispatch_benchmark,
    "goals": _dispatch_goals,
    "research": _dispatch_research,
    "repointel": _dispatch_repointel,
}


async def dispatch(args, parser) -> int:
    """Route a parsed argument namespace to its group handler.

    Exit codes: 0 success, 2 ValueError / unknown command / bad IDs
    (the *NotFoundError family), 1 any other exception. Errors are printed
    to stderr (§14.2).
    """
    handler = _DISPATCHERS.get(args.group)
    if handler is None:
        parser.error("Unknown command")
        return 2
    try:
        return await handler(args, parser)
    except _USER_ERROR_EXCEPTIONS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    async def runner():
        await init_db()
        try:
            return await dispatch(args, parser)
        finally:
            await close_db()

    return asyncio.run(runner())


if __name__ == "__main__":
    sys.exit(main())