# Intermittent Auth Failures — Incident Report

## Incident summary

Starting at approximately 09:05 on the day of the certificate rotation,
roughly 5% of login attempts fail with "token validation error: signature
verification failed". Failures are intermittent — retries usually succeed.
The failures began immediately after the 09:00 certificate rotation and
were not present before it. Failures cluster on requests served by two
specific backend nodes.

## Timeline

- 08:50 — Steady state: ~0.1% auth failure rate (background noise).
- 09:00 — Certificate rotation executed: new ECDSA cert deployed to the
  auth tier.
- 09:05 — First spike of token validation errors, ~5% of logins.
- 09:30 — On-call confirmed errors are concentrated on backend-2 and
  backend-5.

## Impact

- ~5% of login attempts fail with token validation errors; retries mostly
  succeed, so user impact is soft but visible in support tickets.
- Only logins are affected; existing sessions validate normally.