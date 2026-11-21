# Reconciliation Failure — Incident Report (14:00 UTC)

## What failed

At 00:00 UTC the nightly reconciliation job failed with the error
"column user_status does not exist" (SQLSTATE 42703). The job aborted on
its first statement and wrote no reconciliation results. This is the first
failure of the job in six months.

## Data impact

Dashboards show 12,120 accounts rows with NULL `account_state` that the
job was supposed to reconcile. Rows created or updated after the 12:00
deploy have `account_state` set, but a subset of rows touched by an
application path still writes the old column name through an ORM mapping
cache, producing NULLs. The 10:00 documentation says the job is idempotent
and stable and that schema v3 is stable, but the job's SQL now fails
outright after the deploy.

## Timeline

- 00:00 — Reconciliation job starts, fails immediately (42703).
- 12:00 — (previous day) Migration 004 renamed `user_status` to
  `account_state` and ran a backfill.
- 14:00 — On-call opens this incident; 12,120 NULL rows confirmed.