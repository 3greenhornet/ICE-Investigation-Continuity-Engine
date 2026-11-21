# Incident Report — Checkout Latency (Duplicate Copy)

## Incident summary

The checkout service p95 latency regressed after the v42 deployment went
out at 14:00 UTC. Baseline p95 latency was 240ms; it peaked at 890ms.
Checkout completion dropped about 9%. Rolling back to v41 at 16:30 UTC
restored the 240ms latency baseline within 10 minutes.

## Timeline

- 14:00 — v42 deployed to all checkout pods.
- 14:05 — latency alerts fired, p95 355ms.
- 14:30 — p95 peaked at 890ms.
- 16:30 — rollback to v41; latency recovered to 240ms.

## Impact

- p95 latency 240ms → 890ms during the incident window.
- ~41k delayed checkouts, 9% completion-rate drop.
- Rollback confirmed v42 as the source of the regression.