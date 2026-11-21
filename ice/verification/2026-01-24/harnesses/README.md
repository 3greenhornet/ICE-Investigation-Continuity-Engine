# Sandbox harnesses used by this verification artifact

The two fix-4 harnesses whose outputs are pasted in this directory, copied
here so the artifact is self-contained (doc Part 4 §16: "if a future agent
can't run one command to reproduce Stage N from verification/, the
verification is incomplete"). Both run from the repo root:

    python3 verification/2026-01-24/harnesses/fix4_runtime_attrs.py
    python3 verification/2026-01-24/harnesses/fix4_p2_polarity.py

- `fix4_runtime_attrs.py` — doc Part 4 §2.3 runtime attribute checks plus the
  Part 2 dispatch/surface extensions. Output: `01_runtime_attrs.txt`
  (35/35 PASS).
- `fix4_p2_polarity.py` — doc P2.5 three-part polarity test run against the
  real engine functions (create_case → _create_source → ingest_evidence →
  create_hypothesis/create_claim → add_edge → recompute_hypothesis_confidence
  → get_current_version) over an in-memory fake asyncpg pool reproducing the
  012/013/015 column layouts. Output: pasted in `../verification-report.md`
  item 7 (the live-DB form remains the D.4 user-side command).

Both stub the third-party drivers absent in the verification sandbox
(litellm, google.genai, asyncpg, pgvector) — the stub pattern is documented
inside each file. The other harnesses referenced by CHANGES.md and the
worklogs (fix1/fix2a/fix2b/fix2c_verify, ice2a/ice4a*, test_ice2c/4c,
verify_ice4b, final_import_check/sweep) live in the development
environment's `.scratch/` and are historical dev-time gates, not part of
this artifact.