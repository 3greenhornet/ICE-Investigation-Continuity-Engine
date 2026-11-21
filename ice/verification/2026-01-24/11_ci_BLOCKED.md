# Stage 11 — CI verification: BLOCKED in this sandbox

**Status: blocked-user-side** (requires a git remote on GitHub + a push; this
sandbox has no remote, no `gh` CLI, and the workspace repo is unrelated
auto-snapshots — see `00_baseline.md`).

## Why blocked

CI (doc Part 4 §12) is verified by pushing to a repository and watching the
three workflow jobs go green. That needs a real remote. What WAS verified
here, statically:

- `.github/workflows/test.yml` exists (workflow-file gate, doc §12.1).
- Its three jobs match the shipped test suite's markers (fix-3 verified;
  re-checked for this pass — see the excerpt below).
- The job commands are exactly the ones that pass/skip cleanly in this
  sandbox (Stage 5): `pytest -m unit`, `pytest -m integration`,
  `pytest -m eval`.

Doc §0.8 disposition (recovery scan): only the Oct 22 version of
`test.yml` ever existed in this environment — there was no Oct 23
counterpart to compare or recover (see `12_recovery_scan.md` and the
CHANGES.md merge note), so the Oct 22 workflow was kept and only its
test commands were confirmed current for the 20-file suite.

## What the user must run (doc Part 4 §12, verbatim commands)

```bash
# §12.1 workflow file present
ls -1 .github/workflows/          # [test.yml]

# §12.2 push to trigger CI  (first: create the repo — see 00_baseline.md git-init block)
git add .
git commit -m "Verification run — $(date -Iseconds)"
git push origin main

# §12.3 check status — expect three jobs (unit, integration, eval) all completed success
gh run list --limit 5
gh run view --log
```

Common CI failures per the doc (§12.3): `GEMINI_API_KEY` missing as a repo
secret → eval job fails (add the secret); `DATABASE_URL` not provided by the
service container → integration job fails (check the workflow's postgres
service config); missing dependency in CI's Python env → update the workflow's
`pip install` step. Save output as `verification/<date>/11_ci_result.txt`.