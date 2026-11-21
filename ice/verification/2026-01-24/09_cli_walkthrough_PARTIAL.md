# Stage 9 — CLI walkthrough: PARTIAL (argparse verified here; live run user-side)

**Status: partial** — the CLI's parser layer and dispatch wiring for every
command the doc's §10 walkthrough exercises is verified in this sandbox; the
live run (which needs the DB + LLM) is user-side.

## What WAS verified here

The full `.scratch/fix2b_verify.py` harness (86/86 PASS — includes live HTTP
against a fake pool); its section (d) exercises the real argparse parser
(`cli.build_parser()`) for the walkthrough's new-flag commands. Verbatim
output:

```
$ python3 /home/z/my-project/.scratch/fix2b_verify.py   # section (d) only

[PASS] (d) case create --type research parsed
[PASS] (d) case create defaults: type=debug, required_confidence=None
[PASS] (d) case create explicit confidence + explore parsed
[PASS] (d) evidence add-llm parsed
[PASS] (d) investigate run --falsify --auto-execute parsed
[PASS] (d) investigate run defaults both flags False
[PASS] (d) case create rejects bogus --type
[PASS] (d) _dispatch_case passes case_type=args.type
[PASS] (d) _dispatch_investigate passes enable_auto_execute
[PASS] (d) CLI imports ingest_external_analysis
```

These cover the doc §10.4 gate items directly: `--falsify` is accepted
(P4), `--auto-execute` is accepted (Part 2 §4.5), `--type` validates against
the {debug,research,explore} enum (Part 2 §3.4), and `evidence add-llm`
parses all five of its options (Part 2 §2.5). The dispatch-source checks
confirm the parsed values are actually threaded into `create_case(...)` /
`run_investigation_cycle(...)`. Parser round-trips for the remaining
walkthrough commands (`case list/show/summary`, `evidence list`, `claim list`,
`hypothesis list`, `decision run`, `replay run`, `export json/markdown`,
`benchmark report`) are pre-existing Oct 22 surface, unchanged by the merge
(CHANGES.md "Kept Oct 22"), and exercised live user-side below.

## What the user must run (doc Part 4 §10.2–§10.7, verbatim commands)

With containers up and `CASE_ID_2` from Stage 3
(`03_verify_part2_BLOCKED.md`):

```bash
# §10.2 case commands
docker compose exec api python -m cli.cli case list --limit 5          # [5 cases: case_id status title]
docker compose exec api python -m cli.cli case show $CASE_ID_2         # [JSON case fields]
docker compose exec api python -m cli.cli case summary $CASE_ID_2      # [JSON counts + leading hypothesis + next action]

# §10.3 evidence, claim, hypothesis
docker compose exec api python -m cli.cli evidence list $CASE_ID_2
docker compose exec api python -m cli.cli claim list $CASE_ID_2
docker compose exec api python -m cli.cli hypothesis list $CASE_ID_2

# §10.4 investigation — first without flags, then the two new flags
docker compose exec api python -m cli.cli investigate run $CASE_ID_2   # [Decision: <type> (confidence=...)]
docker compose exec api python -m cli.cli investigate run $CASE_ID_2 --falsify
docker compose exec api python -m cli.cli investigate run $CASE_ID_2 --auto-execute

# Part 2 additions worth exercising in the same pass:
docker compose exec api python -m cli.cli case create --title "R" --objective "survey" --type research
docker compose exec api python -m cli.cli evidence add-llm $CASE_ID_2 --llm claude-3.5-sonnet \
    --prompt "What could cause checkout latency?" --response "Pool saturation under burst load..."
docker compose exec api python -m cli.cli evidence show <evidence_id>   # [source_llm visible in dump]

# §10.5 decision and replay
docker compose exec api python -m cli.cli decision run $CASE_ID_2      # [ACT|INVESTIGATE|ABSTAIN (confidence=...)]
docker compose exec api python -m cli.cli replay run $CASE_ID_2        # [Events: N / Leading hypothesis / Confidence]

# §10.6 export (audit gap #11)
docker compose exec api python -m cli.cli export json $CASE_ID_2 --out /tmp/cli_export.json
docker compose exec api python -m cli.cli export markdown $CASE_ID_2 --out /tmp/cli_export.md
docker compose exec api cat /tmp/cli_export.json | head -3
docker compose exec api cat /tmp/cli_export.md | head -5

# §10.7 benchmark CLI (audit gap #12) — after Stage 8 produces results
docker compose exec api cp /tmp/benchmark_full_results.json /tmp/cli_benchmark.json 2>/dev/null || echo "(no prior results)"
docker compose exec api python -m cli.cli benchmark report --results /tmp/cli_benchmark.json | head -10
```

Gate (doc §10.8): every command runs without `Error:`; `export json` created
a file; `investigate run --falsify` (and `--auto-execute`) accepted. Save the
transcript as `verification/<date>/09_cli_walkthrough.md`.