# Checkout Latency Incident Report — 2026-09-20

## Incident summary

At approximately 14:00 UTC the checkout service p95 latency began climbing
after the deployment of v42. The p95 rose from a stable 240ms baseline to
890ms at peak. Customer-facing checkout completion rate dropped 9% during
the window. The service was rolled back to v41 at 16:30 UTC and latency
returned to the 240ms baseline within 10 minutes.

## Timeline

- 13:40 — v41 steady state: p95 240ms, cache hit rate 95%, ~19 active DB
  connections.
- 14:00 — Deployment v42 rolled out to 100% of checkout pods.
- 14:05 — First latency alerts: p95 355ms, active DB connections pinned at
  the new maximum.
- 14:30 — p95 peaked at 890ms; cache hit rate dipped to 40% during the
  saturation window.
- 16:30 — Rollback to v41 executed; latency and cache hit rate recovered
  immediately.

## Impact

- p95 checkout latency: 240ms → 890ms (3.7x regression) between 14:00 and
  16:30 UTC.
- Estimated 41k delayed checkouts and a 9% completion-rate drop.
- No data loss; all orders eventually completed or were retried.
- The rollback at 16:30 restored the 240ms baseline, confirming the
  regression was introduced by deployment v42.