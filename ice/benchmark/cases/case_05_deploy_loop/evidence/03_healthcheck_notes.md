# Health Check Configuration Notes

## The port migration

In the last config change (merged yesterday 09:00, release r310) the
`/api/health` endpoint moved from port 8080 to port 9090 as part of the
container hardening work. The service binds and serves `/api/health` on
9090 — confirmed with a direct curl from inside the staging network.

## The load balancer probe

The staging load balancer's health check still probes
`/api/health` on the OLD port 8080. The probe gets a connection timeout
(nothing listens on 8080 anymore) and reports the target unhealthy after
3/3 failed probes — a false negative: the service is healthy on the new
port the whole time. The autoscaler treats the unhealthy state as a failed
release and triggers the auto-rollback; the deploy pipeline then re-deploys
r310 on its 15-minute staging schedule, and the cycle repeats.

## What is NOT the problem

- The service is not crash-looping: process uptime spans the whole loop
  window, memory is flat, and restart counts are zero.
- The autoscaler is behaving as configured — the remediation it performs
  (rollback) is just the wrong tool for a probe misconfiguration.