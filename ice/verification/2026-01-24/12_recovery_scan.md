# Stage 12 — Recovery scan final check: DISPOSITIONED (nothing to scan)

**Status: dispositioned** — this is not "blocked by the sandbox"; the scan
itself was **impossible by construction**: the Oct 23 directory was never
provided to this environment. The doc's own §0 check ("you must actually have
the Oct 23 directory — if you only have the repomix, this entire section is a
no-op") applies, and the disposition is already recorded in CHANGES.md.

## The honest disposition (quoted from CHANGES.md, "Merge note (ICE fixes doc
Part 3 §0.10) — 2026-01-24" → "Recovery scan — honest disposition")

> **The Oct 23 directory was never provided to this environment.** No
> `hari-ice-revamp-oct23/` tree ever existed here to inventory, diff, or
> recover files from — the document's §0.2 file-inventory, §0.3 presence
> probe, and migration probe had nothing to probe beyond our Oct 22 base, and
> `scripts/seed_demo.py` (§0.7) could not be recovered for the same reason.
> What WAS available: the "hari ice fixes" document itself, whose Part 1
> carries the complete Oct 23 extraction pack (P1–P8, S1–S5, R1–R19, the four
> manual patches, the disposition matrix) as verbatim code, plus the OCT 23
> EXTRACTION PACK appendix at its tail. The merge therefore applied that
> pack's patches by hand onto the Oct 22 base — the route the document itself
> prescribes for exactly this situation — and **nothing else was available to
> recover**.

The same CHANGES.md section records the per-category outcomes:
**Recovered from Oct 23:** none (no directory existed). **Ported:** P1–P8
(P7 already landed), S1/S2, R6 — via the document's verbatim patch code.
**Renamed:** `case_engine/investigation.py` → `cycle.py` + re-export shim
(§0.6 Option B). **Kept Oct 22:** everything else. **Deleted:** nothing —
`seed_demo.py` is a §0.7 Case-1-equivalent (not recoverable, not created;
`load_demo_case.py` is the single canonical demo script). **Added:** 012a,
015, 016, 017, `dedupe_contradictions.sql`, `endogenous.py`, `nli.py`,
proposed-edges surface, `tests/`, `verification/`.

## Why there is no `comm -23` output to paste

Doc Part 4 §13.1 runs `find hari-ice-revamp-oct23/ ...` — that path does not
exist here (and never did), so the re-inventory, the `comm -23` set
arithmetic, and the "unaccounted files" review all have an empty left-hand
side. Fabricating an inventory output would violate the honesty mandate; the
disposition above is the truthful record.

## What the user must run IF they possess the Oct 23 directory

Only then does Stage 12 become operative (doc Part 4 §13, verbatim):

```bash
find hari-ice-revamp-oct23/ -type f \
  ! -path '*/__pycache__/*' ! -path '*/.git/*' ! -path '*/node_modules/*' \
  ! -path '*/logs/*' ! -path '*/profiles/*' ! -path '*/outputs/*' \
  ! -path '*/.pytest_cache/*' ! -path '*/.venv/*' ! -path '*/venv/*' \
  ! -name '*.pyc' ! -name '*.log' ! -name '.DS_Store' \
  | sed 's|^hari-ice-revamp-oct23/||' | sort > /tmp/oct23_final.txt
echo "Oct 23 files: $(wc -l < /tmp/oct23_final.txt)"

find hari/ -type f \
  ! -path '*/__pycache__/*' ! -path '*/.git/*' ! -path '*/node_modules/*' \
  ! -path '*/logs/*' ! -path '*/profiles/*' ! -path '*/outputs/*' \
  ! -path '*/.pytest_cache/*' ! -path '*/.venv/*' ! -path '*/venv/*' \
  ! -path '*/verification/*' \
  ! -name '*.pyc' ! -name '*.log' ! -name '.DS_Store' \
  | sed 's|^hari/||' | sort > /tmp/merged_final.txt
echo "Merged files: $(wc -l < /tmp/merged_final.txt)"

echo "## Oct 23 files NOT in merged base"
comm -23 /tmp/oct23_final.txt /tmp/merged_final.txt
# Every listed file must be documented in CHANGES.md as intentionally not
# ported (e.g. seed_demo.py Case-1 disposition) — otherwise port it per
# Part 3 §0.5.3's general rule, append to CHANGES.md, and re-run.

grep -A 50 "Merge note" CHANGES.md | head -60    # §13.2 merge-note presence
```