# Reconciliation Job — Documented Behavior

## Documented behavior (recorded 10:00 UTC, before the deploy)

The reconciliation job runs nightly at 00:00 UTC and is idempotent. The
job's SQL reads the `user_status` column from the `accounts` table and
reconciles account state transitions against the ledger. Schema v3 is
stable and has been unchanged for six months; the reconciliation job has
run without modification for the same period. The reconciliation job is
idempotent and stable.

## Ownership and change process

Schema changes require a review of downstream SQL consumers. The
reconciliation job is listed as a consumer of the `accounts` table, but its
SQL is stored in a separate repository that is not covered by the
migration tooling's automated reference scan.