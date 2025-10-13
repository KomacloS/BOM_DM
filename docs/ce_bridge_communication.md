# Complex Editor Bridge Communication Overview

The Complex Editor (CE) bridge client now centralises all HTTP traffic through a
single shared `requests.Session`. The session is created lazily on first use and
has `trust_env` disabled so that local connections are not routed through
corporate proxies that could inject `407 Proxy Authentication Required` or `503`
errors. When an authentication token is configured, every request includes an
`Authorization: Bearer <token>` header.

## Preflight readiness loop

Every user-facing workflow that talks to the CE bridge now performs a
preflight handshake by calling `preflight_ready()` before sending any other
request. The helper polls `GET /state` until the payload reports
`{"ready": true}` or the deadline expires. While warming up it:

1. Interprets `401`/`403` responses as immediate authentication failures.
2. Treats all `>= 500` responses (including `503 warming_up`) as a signal to
   keep waiting.
3. Runs `POST /selftest` diagnostics on the first iteration and roughly every
   three seconds afterwards to nudge the bridge into running its self-checks.
4. Ignores transient network errors and keeps polling.
5. Sleeps between polls (default 0.3s) until the bridge becomes ready.

On success the helper caches the readiness timestamp and payload so subsequent
callers can skip the handshake for about five seconds. On timeout it asks
`GET /health` for the latest reason string (for example `mdb_unavailable` or
`port_conflict`) and surfaces it in the `CEBridgeError` message.

## Where the preflight runs

* `ensure_ce_bridge_ready()` invokes `preflight_ready()` immediately after
  spawning (or reusing) the bridge process.
* The settings dialog's "Test Bridge" action uses the preflight and then runs a
  single `GET /health` call to show the connection details.
* The Complex Panel widget checks whether the cached preflight is still fresh;
  if not, it runs the handshake before starting search/detail/create flows and
  shows the UI message "Complex Editor is starting (running diagnostics)…" while
  waiting.

Together these changes ensure that all bridge traffic bypasses proxies, carries
credentials, waits for the CE to be ready, and exposes clear feedback when the
service is still warming up.
