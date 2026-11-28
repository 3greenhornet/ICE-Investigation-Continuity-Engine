"""Held-out evaluation of SEARCH routing: learned router vs keyword regex.

None of these queries is in case_engine.router.SEED. Cross-domain questions
("how do epidemiologists model ...") are the important class: the old regex
sent them into the codebase, which is the domain lock the router fixes.

    python benchmark/router_eval.py
Measured 2026-11-28 (bge-base-en-v1.5): router+fallback 15/16, regex 9/16.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ICE_SEMANTIC_ROUTER"] = "1"

HELD_OUT = [
    ("what approaches exist for rate limiting an API", "web"),
    ("how do epidemiologists model spread through contact networks", "web"),
    ("techniques used in sports analytics to rate players fairly", "web"),
    ("how does queueing theory size worker pools", "web"),
    ("ideas from library science for organising notes", "web"),
    ("are there better ways to rank search results than BM25", "web"),
    ("how do airlines handle overbooking decisions", "web"),
    ("control theory approaches to autoscaling", "web"),
    ("where do we validate user input", "code"),
    ("which part of our backend writes the outbox file", "code"),
    ("show me the handler for the delete repository endpoint", "code"),
    ("what calls the ICE client when a finding is ingested", "code"),
    ("is there a test for the circuit breaker half-open state", "code"),
    ("where is the rate limiter configured in this project", "code"),
    ("how does our app compute the health score", "code"),
    ("which module stores triage decisions", "code"),
]


def main() -> None:
    import tempfile

    from case_engine.action import _WEB_CUES
    from case_engine.router import SemanticRouter

    r = SemanticRouter(Path(tempfile.mkdtemp()) / "router.json")  # seeds only, no learned examples
    rows, ok_router, ok_regex = [], 0, 0
    for text, expected in HELD_OUT:
        route, info = r.route(text)
        regex = "web" if _WEB_CUES.search(text) else "code"
        final = route or regex
        ok_router += final == expected
        ok_regex += regex == expected
        rows.append({"text": text, "expected": expected, "router": route, "regex": regex, **info})
    print(json.dumps(rows, indent=1))
    print(f"router+fallback {ok_router}/{len(HELD_OUT)}  regex-only {ok_regex}/{len(HELD_OUT)}")


if __name__ == "__main__":
    main()
