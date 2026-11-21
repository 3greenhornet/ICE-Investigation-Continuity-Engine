# Certificate Rotation Notes — 09:00 Rotation

## Rotation record

- 09:00 — New ECDSA certificate (CN=auth.example.com) rotated into the
  auth tier; the old RSA certificate was removed from the primary trust
  bundle at the same time.
- The rotation plan assumed all backend services pick up certificates via
  the shared config service and restart automatically.

## Config audit (post-incident)

- 4 of 6 backend services restarted with the new ECDSA certificate as
  expected.
- 2 of 6 backend services (backend-2 and backend-5) still reference the
  old RSA certificate in their local config override files; the shared
  config update did not take effect because the override files pin the
  cert path directly.
- The load balancer distributes token validation across all 6 backends,
  so roughly one third of requests hit a stale-cert node — matching the
  intermittent, retryable failure pattern.