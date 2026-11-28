"""Live end-to-end benchmark of cross-domain discovery (needs network + LLM keys).

Cases: benchmark/cross_domain_cases.json (problem, origin field, expected
domains and mechanisms from other fields). Unlike tests/unit/test_discovery_
upgrade.py, nothing here is seeded from the expected answers: each variant
starts from the problem text alone.

Variants:
  baseline     the pre-discovery research step: plan_queries() (a "software
               team goal" prompt) + keyless web search; evidence = result text
  discover     case_engine.discovery without structural re-ranking
  discover+fp  case_engine.discovery with domain-stripped fingerprints

Metrics per case:
  mech_hit     an expected mechanism appears in the produced text (cards, or
               web results for the baseline): >= 60% of its distinctive words
  mech_recall  fraction of expected mechanisms found
  domain_hit   an expected domain shares a distinctive word with a selected field
  areas        distinct broad areas among selected fields
  seconds

    python benchmark/discovery_live.py [--cases 3] [--variants baseline,discover,discover+fp]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ICE))

_GENERIC = {"model", "models", "control", "theory", "analysis", "method", "methods", "process", "processes",
            "algorithm", "law", "criterion", "test", "design", "mitigation", "monitoring", "constraints",
            "the", "and", "for", "with", "based", "sampling", "chart", "optimization"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-']{2,}", text.lower())}


def _distinct(name: str) -> set[str]:
    ws = {w.strip("'s") for w in re.findall(r"[a-z][a-z0-9\-']{2,}", name.lower())}
    core = {w for w in ws if w not in _GENERIC}
    return core or ws


def mech_found(expected: str, text_words: set[str]) -> bool:
    d = _distinct(expected)
    return bool(d) and len(d & text_words) / len(d) >= 0.6


async def run_baseline(problem: str) -> dict:
    from case_engine.web_research import plan_queries, research

    t0 = time.monotonic()
    plan = await plan_queries(problem)
    texts = []
    for q in plan["web"][:2]:
        r = await research(q, max_results=4, include_github=False, fetch_pages=False)
        texts += [f"{i.get('title', '')} {i.get('content', '')[:600]}" for i in r["items"]]
    return {"text": " ".join(texts), "fields": [], "seconds": round(time.monotonic() - t0, 1),
            "queries": plan["web"]}


async def run_discover(problem: str, fingerprint: bool) -> dict:
    from case_engine.discovery import discover

    r = await discover(problem, ingest=False, catalogue=False, seed=11, fingerprint=fingerprint)
    text = " ".join(" ".join([c["name"], c.get("mechanism", ""), c.get("transferable", "")]
                             + [s["title"] or "" for s in c["sources"]]) for c in r.get("cards", []))
    return {"text": text, "fields": [(f["field"], f.get("parent") or "") for f in r.get("fields_selected", [])],
            "seconds": r.get("seconds"), "cards": [c["name"] for c in r.get("cards", [])],
            "errors": r.get("errors"), "areas": (r.get("diversity") or {}).get("distinct_parent_areas")}


def score(case: dict, out: dict) -> dict:
    tw = _words(out["text"])
    found = [m for m in case["expected_mechanisms"] if mech_found(m, tw)]
    field_words = _words(" ".join(f"{a} {b}" for a, b in out["fields"]))
    dom = [d for d in case["expected_domains"] if _distinct(d) & field_words]
    return {"mech_hit": bool(found), "mech_recall": round(len(found) / len(case["expected_mechanisms"]), 3),
            "found": found, "domain_hit": bool(dom), "domains": dom,
            "areas": out.get("areas") or len({b for _, b in out["fields"]}), "seconds": out["seconds"]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=0)
    ap.add_argument("--variants", default="baseline,discover,discover+fp")
    args = ap.parse_args()
    cases = json.loads((ICE / "benchmark" / "cross_domain_cases.json").read_text(encoding="utf-8"))
    if args.cases:
        cases = cases[:args.cases]
    variants = args.variants.split(",")
    rows = []
    for case in cases:
        for v in variants:
            try:
                out = await (run_baseline(case["problem"]) if v == "baseline"
                             else run_discover(case["problem"], fingerprint=(v == "discover+fp")))
                s = score(case, out)
                s.update({"case": case["id"], "variant": v, "cards": out.get("cards"), "errors": out.get("errors")})
            except Exception as exc:  # a failed run is a data point, not a crash
                s = {"case": case["id"], "variant": v, "error": f"{type(exc).__name__}: {exc}"[:200],
                     "mech_hit": False, "mech_recall": 0.0, "domain_hit": False, "areas": 0, "seconds": None}
            rows.append(s)
            print(json.dumps({k: s.get(k) for k in ("case", "variant", "mech_hit", "mech_recall", "found",
                                                    "domain_hit", "areas", "seconds", "error")}), flush=True)
    summary = {}
    for v in variants:
        rs = [r for r in rows if r["variant"] == v]
        summary[v] = {"cases": len(rs),
                      "mech_hit_rate": round(sum(r["mech_hit"] for r in rs) / len(rs), 3),
                      "mean_mech_recall": round(sum(r["mech_recall"] for r in rs) / len(rs), 3),
                      "domain_hit_rate": round(sum(r["domain_hit"] for r in rs) / len(rs), 3),
                      "mean_areas": round(sum(r["areas"] or 0 for r in rs) / len(rs), 2),
                      "mean_seconds": round(sum(r["seconds"] or 0 for r in rs) / len(rs), 1)}
    out = ICE / "benchmark" / "results" / f"discovery_live_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print("written", out)


if __name__ == "__main__":
    from envfiles import load_env_files

    load_env_files()
    asyncio.run(main())
