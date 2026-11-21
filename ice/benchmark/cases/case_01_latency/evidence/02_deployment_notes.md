# Deployment v42 Notes — 2026-09-20

## Deployment v42 changes

- **Connection pool config change**: the database connection pool setting
  `max_connections` was reduced from 100 to 20 as part of a cost-reduction
  initiative for database instances. The change was applied to the checkout
  service's application config.
- **Cache TTL change**: the response cache TTL was shortened from 300s to
  60s to improve freshness of inventory data shown during checkout.
- No application code changes were included in v42; both changes are
  configuration-only.

## Rollback procedure

- `kubectl rollout undo deploy/checkout --to-revision=previous` restores
  the v41 config (max_connections 100, cache TTL 300s).
- Verify with `curl -s localhost:9090/metrics | grep checkout_p95_ms` that
  p95 returns below 300ms within 10 minutes.
- The rollback was executed at 16:30 UTC on 2026-09-20 and latency
  recovered as expected.