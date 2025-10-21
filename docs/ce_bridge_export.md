# Complex Editor Bridge Export Workflow

This document summarizes how BOM_DB communicates with the Complex Editor (CE)
Bridge when generating MDB exports, and how to interpret the status messages
surfaced in the GUI such as “exports disabled in headless mode”.

## Overview

All CE communication is funneled through the `export_bom_to_ce_bridge`
service (`app/services/bom_to_ce_export.py`). The service performs the
end-to-end workflow when the user chooses **BOM → VIVA**, and the GUI then
invokes a synthetic CE export afterwards. The high‑level sequence is:

1. Resolve connection details (base URL, bearer token, timeout) via
   `ce_bridge_client.resolve_bridge_connection()`.
2. Obtain a shared `requests.Session` and build headers using
   `ce_bridge_transport.get_session()` / `build_headers()`.
3. Perform a health probe (`GET /admin/health`) before attempting an export.
4. Collect Complex IDs from BOM data, existing `ComplexLink` entries, and any
   optional PN→ID overrides.
5. Submit the export (`POST /exports/mdb`) with a trace ID, output directory,
   and requested MDB name.
6. Map the HTTP response into a domain status, report skipped rows, and
   capture diagnostics for the GUI summary dialog.

## Transport & Authentication

* **Session** – The shared session is created by
  `ce_bridge_transport.get_session(base_url)` to reuse HTTP connections per
  host while isolating CE requests from the wider app.
* **Headers** – `build_headers(token, trace_id, content_type=...)` sets:
  * `Authorization: Bearer <token>` (if provided in settings)
  * `X-Trace-Id: <uuid>` – propagated end-to-end for diagnostics
  * `Content-Type: application/json` for POSTs
* **Trace IDs** – `export_bom_to_ce_bridge` creates a UUID when the GUI does
  not supply one. The trace ID is included in GUI dialogs, logs, and the CE
  response payloads, making it straightforward to correlate bridge logs with
  user reports.

## Health Check Prior to Export

BOM_DB now gatekeeps exports by querying `/admin/health` first. When exports are blocked because the bridge is headless, the CE supervisor automatically launches the configured CE UI to flip `headless` off before continuing. If the bridge remains blocked the workflow exits early with a RETRY status and the GUI only shows the final summary dialog.

Before exporting we call `GET /admin/health` with the same headers. The JSON
payload determines whether the bridge is ready:

| Field            | Meaning                                              | Action in BOM_DB                                                       |
|------------------|------------------------------------------------------|------------------------------------------------------------------------|
| `ready`          | `true` when the bridge can service requests          | Continue with the export                                               |
| `last_ready_error` / `detail` | Additional diagnostics when `ready` is `false` | Return `STATUS_RETRY_LATER` plus the error text                        |
| `headless` / `allow_headless` | Indicates CE is running without UI and whether exports are permitted | When `headless==true` and `allow_headless==false` we surface “exports disabled in headless mode” and return `STATUS_RETRY_LATER` |

The screenshot in the original question corresponds to this path: the bridge
reported `headless: true` but did not allow headless exports, so the GUI shows
“Complex Editor export deferred … exports disabled in headless mode”.

## Preparing the Export Payload

`export_bom_to_ce_bridge` assembles the export manifest as follows:

1. Load every fitted BOM row for the target assembly from the SQL database
   (`ComplexLink`, `BOMItem`, `Part`).
2. Filter to rows whose test method is **Complex**.
3. Resolve candidate CE IDs in priority order:
   * Stored `ComplexLink.ce_complex_id`
   * Optional `ce_component_map` override (PN→comp_id)
4. Track unresolved rows and generate a CSV report if necessary. Any row without
   a valid CE ID receives the `not_linked_in_CE` reason and is omitted from the
   export request.
5. Produce a deduplicated, positive integer list of comp IDs to send to CE.

If no valid IDs remain after filtering, the service still returns
`STATUS_PARTIAL_SUCCESS`, records the skipped lines in the CSV report, and the
GUI informs the user that no MDB was produced.

## Export Request

The POST body sent to `POST /exports/mdb` looks like:

```json
{
  "comp_ids": [101, 202, 303],
  "out_dir": "C:/path/to/export/Customer - Project",
  "mdb_name": "Customer - Project - CE Export.mdb"
}
```

* The bridge is responsible for creating the directory if needed.
* The response is parsed alongside the HTTP status code to determine the GUI
  outcome.

### Response Handling

| HTTP Status / Payload                     | BOM_DB Status                | Notes / GUI Message                                                            |
|-------------------------------------------|------------------------------|--------------------------------------------------------------------------------|
| `200 OK`                                  | `SUCCESS` / `PARTIAL_SUCCESS`| Missing or unlinked IDs produce CSV entries. Export path is read from JSON.    |
| `404` with `reason: comp_ids_not_found`   | `FAILED_INPUT`               | All IDs were rejected. CSV marks each as `not_found_in_CE`.                    |
| `409` with `reason: empty_selection`      | `FAILED_INPUT`               | Request body empty after normalization.                                       |
| `409` with `reason: outdir_unwritable`    | `FAILED_INPUT`               | Surface detail message to the user.                                           |
| `409` with `reason: template_missing_or_incompatible` | `FAILED_BACKEND` | Include `template_path` if provided.                                          |
| `503` with `reason: bridge_headless`      | `RETRY_LATER`                | GUI shows “exports disabled in headless mode”.                                |
| Any other `5xx`                           | `FAILED_BACKEND`             | Treated as a bridge/server failure.                                           |
| Any other `4xx`                           | `FAILED_INPUT`               | Typically misconfiguration or invalid payload.                                |
| Network errors / timeouts                 | `RETRY_WITH_BACKOFF`         | The GUI advises retrying after the backoff period.                            |

The function returns a dictionary containing:

```python
{
    "status": "...",
    "trace_id": "...",
    "export_path": "... or None",
    "exported_count": int,
    "missing_count": int,
    "report_path": "path/to/ce_export_report.csv" or None,
    "detail": "human readable summary"
}
```

The CSV report is written only when there are skipped or missing rows. Each row
records the BOM ID, BOM line ID, part number, CE ID, test method, status, and
reason string (`not_linked_in_CE`, `not_found_in_CE`, `unlinked_data_in_CE`).

## GUI Integration

`app/gui/bom_editor_pane.py` drives the UX:

1. Perform the VIVA export as before, then call `_run_ce_export` to invoke the
   bridge workflow with output folder `<viva_export>/<project-name>/`.
2. Display the CE summary dialog (`_show_ce_export_summary`) with:
   * Overall status (“completed”, “deferred”, “failed”…)
   * Export count and report path if available
   * Trace ID and detail text
   * Action buttons to open the CE folder or CSV report (when present)
3. When headless mode blocks the export, the dialog matches the screenshot:
   status `RETRY_LATER`, zero exported components, and a detail line
   “exports disabled in headless mode”.

## Troubleshooting

* **Headless block** – If you see the deferred dialog with “exports disabled
  in headless mode”, start Complex Editor with its UI or enable headless
  exports in the bridge configuration (`CE_ALLOW_HEADLESS_EXPORTS=1`).
* **Trace IDs** – Use the trace ID shown in the dialog to correlate with
  bridge logs or the `/admin/logs/{trace_id}` endpoint.
* **Missing report** – A report CSV is only produced when rows are skipped,
  missing, or unlinked. A `SUCCESS` status with every component exported
  will not generate a file.
* **Network issues** – `RETRY_WITH_BACKOFF` indicates we could not reach the
  bridge. Verify the bridge service is running and reachable on the configured
  base URL/port.
* **Configuration drift** – All HTTP requests are made via the shared session
  with the token and trace headers. Ensure the bridge settings (base URL,
  token, timeout) are correct in `.bom_platform/settings.toml`.

For further detail, refer to the implementation in:

* `app/services/bom_to_ce_export.py`
* `app/integration/ce_bridge_client.py`
* `app/integration/ce_bridge_transport.py`
* `app/gui/bom_editor_pane.py`

These modules collectively define the CE communication path, status mapping,
and user experience. Let the team know if you need deeper visibility into the
bridge logs or want to automate headless approval in specific environments.
