# Staging Deployment Loop — Incident Report

## What is happening

The staging environment has been redeploying itself every 15 minutes since
yesterday morning. No human has triggered a deploy; the staging deploy
pipeline shows automated triggers only. Each cycle: the service deploys
successfully, then within a few minutes the auto-rollback fires, and 15
minutes later the pipeline re-deploys. The service itself is healthy —
pages load and the API responds normally throughout.

## Timeline

- Yesterday 09:00 — Last config change merged (service port migration).
- Yesterday 09:15 — First auto-rollback observed; loop begins.
- Every 15 minutes since — deploy → auto-rollback → redeploy cycle
  repeats. 96+ cycles as of this morning.

## Impact

- Staging is effectively unusable for QA (state resets every 15 minutes).
- No production impact (loop is staging-only).
- Deploy pipeline quota is being consumed by ~96 spurious deploys/day.