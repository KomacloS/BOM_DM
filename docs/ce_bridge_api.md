# CE Bridge API Reference

## Overview
The CE (Complex Editor) bridge exposes an HTTP interface that lets BOM_DB discover
complexes, inspect normalization behavior, and coordinate alias management. The
bridge is deployed alongside the CE desktop tooling; BOM_DB communicates with it
via authenticated JSON APIs.

Unless otherwise noted, all endpoints return JSON bodies encoded in UTF-8 and
expect standard query-string encoding for parameters.

## Base URL and Authentication
* **Base URL:** Configured via BOM_DB settings (`bridge.base_url`).
* **Authentication:** Optional bearer token. When configured, every request
  must include `Authorization: Bearer <token>`.
* **Traceability:** Every request **must** include an `X-Trace-Id` header with a
  32-character hexadecimal string (UUID without dashes is fine). BOM_DB logs the
  trace id so support can correlate requests with bridge-side logs.
* **Timeouts:** The default timeout is obtained from bridge settings
  (`bridge.timeout_seconds`). Callers may override it per request when a shorter
  or longer wait is needed.

## Common Headers
| Header        | Required | Description |
| ------------- | -------- | ----------- |
| `X-Trace-Id`  | Yes      | 32-hex identifier that ties BOM_DB actions to bridge logs. |
| `Authorization` | Optional | `Bearer <token>` when authentication is enabled. |
| `Accept` / `Content-Type` | Optional | Defaulted by the HTTP client to `application/json`. |

## Endpoints

### `GET /health`
Performs a shallow health probe to verify that the bridge process is alive.

**Query params:** none

**Example:**
```bash
curl -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     http://ce-bridge.local:8080/health
```

**Success (200):**
```json
{
  "ready": true,
  "headless": false,
  "allow_headless": false,
  "reason": "ok",
  "trace_id": "abcdef1234..."
}
```

**Response fields:**
* `ready` – bridge initialization status; callers wait for this to become `true`.
* `headless` – indicates whether CE is currently running without its UI.
* `allow_headless` – `true` when the bridge permits exports while headless.
* `reason` – free-form status string explaining the current readiness state.
* `trace_id` – identifier echoed back by CE for correlating request/response logs.

**Common errors:**
* `503 Service Unavailable` – bridge failed its self-check (includes reason).

### `GET /state`
Feature probe that surfaces bridge capabilities and configuration flags. BOM_DB
uses this to ensure the bridge supports ranked match analysis and the required
normalization ruleset.

**Query params:** none

**Response fields:**
* `features.search_match_kind` – `true` when match_kind analysis is available.
* `features.normalization_rules_version` – expected to be `"v1"`.
* `features.export_mdb` – optional export capability flag.
* `allow_headless` – indicates whether GUI automation is permitted in this
  environment.

**Example:**
```bash
curl -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     -H "Authorization: Bearer $TOKEN" \
     http://ce-bridge.local:8080/state
```

**Success (200):**
```json
{
  "features": {
    "search_match_kind": true,
    "normalization_rules_version": "v1",
    "export_mdb": false
  },
  "allow_headless": true
}
```

**Errors:**
* `403 Forbidden` – invalid or missing token when the bridge enforces auth.
* `5xx` – transport or bridge error; include response body in support logs.

### `GET /admin/pn_normalization`
Returns the active normalization rule set and configuration. Support uses this
endpoint to troubleshoot why specific part numbers normalize differently.

**Response fields:**
* `rules_version` – version string, typically `"v1"` for CE bridge v0.1.0.
* `config.case` – case-folding strategy.
* `config.remove_chars` – characters stripped during normalization.
* `config.ignore_suffixes` – suffixes trimmed from part numbers.
* `trace_id` – BOM_DB appends this locally when rendering diagnostics.

**Example:**
```bash
curl -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     http://ce-bridge.local:8080/admin/pn_normalization
```

**Notes:** Search responses may surface `normalized_input` and
`normalized_targets` at the top level (v0.1.0) or nested under an `analysis`
object (future releases). Clients should support both layouts.

### `GET /complexes/search`
Performs ranked search against CE complexes. BOM_DB requests analysis metadata to
provide context for the match.

**Query params:**
* `pn` – the user-supplied part number (validated client-side; reject empty or
  wildcard-only strings before calling the bridge).
* `limit` – maximum results to return (BOM_DB defaults to 50 for UI search and
  10 for background auto-link).
* `analyze` – must be `true` to obtain `match_kind`, `reason`, and normalized
  analysis.

**Example:**
```bash
curl -G \
     -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     --data-urlencode "pn=LM317" \
     --data-urlencode "limit=25" \
     --data-urlencode "analyze=true" \
     http://ce-bridge.local:8080/complexes/search
```

**Success (200):**
```json
[
  {
    "id": "12345",
    "pn": "LM317",
    "aliases": ["LM317T"],
    "match_kind": "exact_pn",
    "reason": "primary part number",
    "normalized_input": "lm317",
    "normalized_targets": ["lm317"],
    "analysis": {
      "normalized_input": "lm317",
      "normalized_targets": ["lm317"]
    }
  }
]
```

**Common errors:**
* `400 Bad Request` – input was empty or consisted of wildcards/punctuation only
  (BOM_DB mirrors this validation client-side).
* `401 Unauthorized` / `403 Forbidden` – authentication issues.
* `5xx` – include HTTP status and body when reporting issues.

### `POST /complexes/{id}/aliases`
Adds or removes aliases on a complex.

**Request body:**
```json
{"add": ["LM317T"], "remove": ["LM317P"]}
```

**Success (200):**
```json
{
  "id": "12345",
  "aliases": ["LM317", "LM317T"],
  "source_hash": "f0c3..."
}
```

**Common errors:**
* `400 Bad Request` – malformed payload.
* `404 Not Found` – complex id unknown.

### `POST /selftest`
Runs the CE exporter self-test that validates tooling prerequisites.

**Request body:** empty JSON `{}`.

**Response fields:**
* `resolve_mdb_path_failed` – `true` when the bridge could not locate the MDB path.
* `exporter` – object with the exporter probe details:
  * `template_ok` – bridge located and opened the template MDB successfully.
  * `template_path` – filesystem path to the template MDB under test.
  * `template_hash` – checksum reported by the bridge for the template file.
  * `write_test` – bridge verified it can create and remove files in the export directory.
  * `write_dir` – directory used for the write test.
  * `subset_roundtrip_ok` – round-tripping a subset export succeeded.
  * `subset_error_reason` – short machine-readable reason when the subset check fails.
  * `subset_error_detail` – verbose message with troubleshooting context.

`resolve_mdb_path_failed` is often paired with `reason: "resolve_mdb_path_failed"`
in higher-level responses when MDB discovery fails. Any additional keys in the
payload capture bridge-specific diagnostics surfaced during the run.

**Example:**
```bash
curl -X POST \
     -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     -H "Content-Type: application/json" \
     -d '{}' \
     http://ce-bridge.local:8080/selftest
```

**Common errors:**
* `500 Internal Server Error` – exporter invocation failed; check logs.

### `GET /admin/logs/{trace_id}`
Retrieves bridge-side log excerpts for a specific request trace.

**Path params:**
* `trace_id` – hexadecimal identifier previously sent in the `X-Trace-Id` header.

**Example:**
```bash
curl -H "X-Trace-Id: $(uuidgen | tr -d -)" \
     http://ce-bridge.local:8080/admin/logs/1a2b3c4d5e6f7g8h9i0j
```

**Success (200):**
Returns a JSON object with bridge log lines and metadata for the provided trace.

**Errors:**
* `404 Not Found` – no logs are stored for that trace id (may be expired or never recorded).
* `5xx` – bridge failed to load the log bundle; retry after verifying the trace id.

## Error Handling Summary
| Status | Meaning | Client Action |
| ------ | ------- | ------------- |
| 200    | Success | Consume payload. |
| 400    | Invalid request (e.g., wildcard-only part number). | Surface friendly validation error. |
| 401    | Missing/invalid credentials. | Prompt for updated token. |
| 403    | Authenticated but forbidden. | Check token scope. |
| 5xx    | Bridge failure. | Log trace id, show status + body. |

## Version Notes
CE Bridge v0.1.0 is the minimum version that exposes `features.search_match_kind`
(`true`) and `features.normalization_rules_version == "v1"`. Later versions may
move normalization fields under `analysis`; clients must honor both layouts. New
features should be gated behind the `/state` capability flags before use.
