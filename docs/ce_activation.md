# Complex Editor Activation

This document describes how BOM_DB discovers, activates, and communicates with
an on-device Complex Editor (CE) Bridge. It captures the runtime contracts, the
configuration surface, and the troubleshooting steps for the zero-touch
experience.

## Single source of truth

All bridge interactions flow through four modules:

- `app/integration/ce_supervisor.py` – starts and stops the bridge, polls
  `/admin/health`, and owns the preflight and diagnostics surfaces.
- `app/integration/ce_bridge_client.py` – constructs CE API calls after the
  supervisor ensures readiness. `resolve_bridge_connection()` normalises the
  base URL, bearer token, and timeouts, while `coerce_comp_id()` harmonises
  Complex IDs.
- `app/integration/ce_bridge_transport.py` – provides the shared
  proxy-bypassing `requests.Session`, the header builder, and the
  `/admin/health` polling logic used during preflight.
- `app/services/bom_to_ce_export.py` – implements the BOM→CE export workflow,
  including trace propagation and report handling.

No other module should talk to the bridge directly; the above helpers are the
single source of truth for authentication, preflight behaviour, and trace
handling.

## Configuration

### Executable discovery

`ce_supervisor.get_ce_app_exe()` resolves the Complex Editor UI executable from
one of two locations:

1. The `CE_APP_EXE` environment variable (highest precedence).
2. The `[complex_editor].app_exe_path` entry in `settings.toml`. The legacy
   `[complex_editor].exe_path` key is still honoured for backwards compatibility.

`settings.toml` is created under `~/.bom_platform/settings.toml` (or alongside
the frozen executable). A minimal configuration looks like:

```toml
[complex_editor]
ui_enabled = true
app_exe_path = "C:/Program Files/ComplexEditor/ComplexEditor.exe"
auto_start_bridge = true
auto_stop_bridge_on_exit = true

[complex_editor.bridge]
base_url = "http://127.0.0.1:8765"
auth_token = "super-secret"
request_timeout_seconds = 10

[viva_export]
ce_bridge_url = "http://127.0.0.1:8765"
ce_auth_token = "super-secret"
```

Environment variables can override the TOML values at runtime:

- `CE_APP_EXE` – path to the CE UI executable.
- `CE_BRIDGE_URL` / `CE_AUTH_TOKEN` – forwarded by the packaging layer via
  `[viva_export]` if needed (see `app/config.py`).
- `CE_ALLOW_HEADLESS_EXPORTS`, `CE_TEMPLATE_MDB`, `CE_LOG_LEVEL`, etc. – passed
  through untouched to the CE process. BOM_DB does not interpret these values,
  but it preserves them when spawning the fallback bridge.

### Bridge URL, token, timeout

`ce_bridge_client.resolve_bridge_connection()` merges the `[complex_editor]`
bridge table, the `[viva_export]` overrides, and any environment overrides to
produce the `(base_url, auth_token, timeout_seconds)` tuple. All downstream
modules rely on this function; do not reimplement URL parsing or token
normalisation elsewhere.

The default bridge URL is `http://127.0.0.1:8765`. To use a custom port or host
update `[complex_editor.bridge].base_url`. Confirm that the port is unused with
`netstat -ano` (Windows) or `lsof -i :8765` (Unix) before starting BOM_DB.

## Headless gating and health polling

The supervisor always interrogates `GET /admin/health`. The JSON payload exposes
`ready`, `headless`, `allow_headless`, and optional `trace_id` fields. The flow:

1. Call `/admin/health` using the shared session and `Authorization: Bearer`
   header.
2. If `ready == true`, record the payload and return.
3. If `headless == true` and `allow_headless == false`, the supervisor will
   launch the UI executable and continue polling.
4. Any other non-ready payload is surfaced verbatim in the eventual
   `CEBridgeError` message.

Legacy `/state`, `/selftest`, and `/health` endpoints are no longer used.

## Launch contract

When BOM_DB must start CE itself it executes:

```
<ComplexEditor.exe> --start-bridge --port <port> --token <token> [--config <path>]
```

If the UI executable cannot be resolved or fails to spawn, the supervisor falls
back to a tiny helper module:

```
python -m app.integration.ce_fallback_runner
```

The fallback runner requires `CE_MDB_PATH` to point at the Complex Editor main
database. A minimal invocation looks like:

```
CE_MDB_PATH=~/ComplexEditor/Main.mdb CE_AUTH_TOKEN=secret-token \
    python -m app.integration.ce_fallback_runner
```

When the supervisor spawns the runner it also sets:

- `CE_BRIDGE_HOST` / `CE_BRIDGE_PORT` – the resolved listener.
- `CE_AUTH_TOKEN` – the bearer token from settings (omitted when empty).
- `CE_ALLOW_HEADLESS_EXPORTS` – `"1"` when headless exports are permitted,
  `"0"` otherwise.

The supervisor remembers the `Popen` handle and only terminates the process it
spawned. User-launched bridges are untouched.

## Activation flows

### Settings ▸ “Test CE Bridge”

`app/gui/dialogs/settings_dialog.py` defers to `ce_supervisor.ensure_ready()`.
After readiness succeeds the dialog performs a single `GET /admin/health` using
`ce_bridge_transport.build_headers()` and displays the latest payload plus the
request trace ID. This replaces the previous `/health` probe.

### BOM→VIVA export

`app/services/bom_to_ce_export.py` invokes `ensure_ready()` before issuing any
bridge requests. The export payload carries the current `trace_id`, and all
artifacts (MDB and CSV) are written under `<VIVA export root>/CE/`. The GUI uses
the summary to offer **Open CE Folder**, and the trace can be passed to
`/admin/logs/{trace_id}` for diagnostics.

### Clean shutdown

`ce_supervisor.stop_ce_bridge_if_started()` only runs when BOM_DB created the
bridge process (`_BRIDGE_AUTO_STOP == True`). It first inspects `/state` for
unsaved changes, then calls `POST /admin/shutdown`. If that endpoint is absent,
it terminates the local process; otherwise it waits for a graceful exit.

## Zero-touch UX

A typical workflow requires no manual intervention:

1. Configure the CE executable once in `settings.toml` or via `CE_APP_EXE`.
2. Press **Test CE Bridge** – the supervisor starts CE if needed and displays a
   ready state plus trace ID.
3. Run **BOM→VIVA** – the export runs immediately, writes under `…/CE/`, and the
   GUI provides shortcuts to the MDB and CSV report.
4. Exit BOM_DB – only supervisor-owned bridge processes are stopped; a
   user-launched CE instance keeps running.

## Troubleshooting

### “Exports disabled in headless mode”

The `/admin/health` payload reported `headless: true` with
`allow_headless: false`. The supervisor automatically starts the CE UI and
retries; if CE still refuses headless mode, configure the environment variable
`CE_ALLOW_HEADLESS_EXPORTS=1` (if your CE build permits) or keep the UI running
manually.

### “Timed out waiting for bridge”

Common causes:

- Port already in use – verify with `netstat`/`lsof` and update the bridge
  `base_url` if required.
- Incorrect token – ensure the CE bridge was started with `--token <value>` that
  matches the configured `auth_token`.
- CE failed to boot – inspect the CE logs via
  `curl -H "Authorization: Bearer <token>" <base_url>/admin/logs/<trace_id>`.

You can manually test readiness with:

```
curl -H "Authorization: Bearer <token>" \
     -H "X-Trace-Id: manual-check" \
     <base_url>/admin/health
```

### “Outdir unwritable” / “Template missing”

The CE bridge surfaces these conditions in the export response with
`reason: "outdir_unwritable"` or `"template_missing_or_incompatible"`. Update the
export directory permissions or adjust `CE_TEMPLATE_MDB` to point to a valid
MDB template before retrying. The summary report saved under the `CE/` folder
contains the affected BOM rows.

### Bridge logs

Every request includes an `X-Trace-Id`. Use
`GET /admin/logs/{trace_id}` to retrieve the server-side log bundle that
matches the GUI’s trace display.

## Tests

`tests/test_ce_supervisor.py` exercises the supervisor’s readiness flow, bridge
spawning, fallback path, shutdown behaviour, and wizard launcher. It verifies
that `/admin/health` is the only health endpoint used during tests.
